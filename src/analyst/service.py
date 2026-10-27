"""Transport-neutral analyst service: sessions, pausing, result tables.

Adapters (MCP today) call ``ask`` / ``reply`` / ``decide_write`` and get a
``TurnResult``. A turn runs ``Agent.run`` in a worker thread; the agent's
``ask_user`` and ``approve`` callbacks park that thread and hand a
``NeedsInput`` / ``NeedsApproval`` back to the caller, who later releases
it with ``reply`` / ``decide_write``. A session keeps its conversation
(persisted as JSONL) and its own scratch database, so writes survive
across turns of one chat.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import AgentConfig
from .db import Database, QueryError
from .llm import LLMClient
from .loop import Agent, AgentResult
from .sqlcheck import check_sql
from .tools import ToolRegistry
from .tracing import Tracer

_ID = re.compile(r"[0-9a-f]{12}")
_NO_ANSWER = "No answer was given; use your best judgement and state your assumption."


@dataclass
class Finished:
    session_id: str
    answer: str
    sql: str | None
    columns: list[str]
    rows: list[list]
    rows_truncated: bool
    stop_reason: str
    steps: int
    prompt_tokens: int
    completion_tokens: int
    trace_id: str | None
    write: str | None  # "executed: N row(s)" | "rejected" | "failed" | None


@dataclass
class NeedsInput:
    session_id: str
    run_id: str
    question: str


@dataclass
class NeedsApproval:
    session_id: str
    run_id: str
    sql: str


@dataclass
class Failure:
    message: str


TurnResult = Finished | NeedsInput | NeedsApproval | Failure


class _Run:
    def __init__(self, run_id: str, session_id: str):
        self.run_id = run_id
        self.session_id = session_id
        self.outcomes: queue.Queue = queue.Queue()  # worker -> caller
        self.answers: queue.Queue = queue.Queue()  # caller -> worker
        self.agent: Agent | None = None
        self.waiting: str | None = None  # "input" | "approval" | None
        self.expired = False


def _jsonable(v):
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else v


class AnalystService:
    def __init__(
        self,
        cfg: AgentConfig,
        *,
        sessions_dir: str | os.PathLike,
        llm=None,
        tracer_factory: Callable[[str], Tracer] | None = None,
        retriever=None,
        wait_timeout_s: float = 600.0,
        table_row_limit: int = 10_000,
    ):
        self._cfg = cfg
        self._dir = Path(sessions_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._llm = llm or LLMClient(cfg)
        self._tracer_factory = tracer_factory or (lambda sid: Tracer(session_id=sid))
        if retriever is None and cfg.rag and cfg.rag_index:
            from .rag.search import Retriever

            retriever = Retriever(cfg.rag_index)
        self._retriever = retriever
        self._wait_timeout_s = wait_timeout_s
        self._table_row_limit = table_row_limit
        self._lock = threading.Lock()
        self._runs: dict[str, _Run] = {}
        self._active: dict[str, str] = {}  # session_id -> run_id
        self._expired: set[str] = set()

    # -- public API ----------------------------------------------------------

    def ask(self, question: str, session_id: str | None = None) -> TurnResult:
        with self._lock:
            if session_id is None:
                sid = uuid.uuid4().hex[:12]
                self._new_session(sid)
            else:
                sid = session_id
                if not _ID.fullmatch(sid) or not self._history_path(sid).exists():
                    return Failure(
                        f"Unknown session_id '{session_id}'. Omit session_id "
                        "to start a new chat."
                    )
                if sid in self._active:
                    return Failure(
                        "A run in this session is waiting for input (run_id "
                        f"{self._active[sid]}). Call reply or approve_write "
                        "for it, or omit session_id to start a new chat."
                    )
            run = _Run(uuid.uuid4().hex[:12], sid)
            self._runs[run.run_id] = run
            self._active[sid] = run.run_id
        history = self._load(sid)
        threading.Thread(
            target=self._work, args=(run, question, history), daemon=True
        ).start()
        return run.outcomes.get()

    def reply(self, run_id: str, text: str) -> TurnResult:
        run = self._take(run_id, "input")
        if isinstance(run, Failure):
            return run
        run.answers.put(text)
        return run.outcomes.get()

    def decide_write(self, run_id: str, approve: bool) -> TurnResult:
        run = self._take(run_id, "approval")
        if isinstance(run, Failure):
            return run
        run.answers.put(bool(approve))
        return run.outcomes.get()

    def close(self) -> None:
        """Cancel every active run and release any parked worker."""
        with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            if run.agent is not None:
                run.agent.cancel()
            run.answers.put(None)

    # -- runs ----------------------------------------------------------------

    def _take(self, run_id: str, kind: str) -> _Run | Failure:
        """Claim a paused run for exactly one caller."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None and not run.expired and run.waiting == kind:
                run.waiting = None
                return run
        if run is None:
            if run_id in self._expired:
                return Failure(self._expired_message())
            return Failure(f"Unknown run_id '{run_id}'.")
        if run.expired:
            return Failure(self._expired_message())
        if run.waiting != kind:
            if run.waiting == "approval":
                return Failure(
                    "This run is waiting for approval of a write, not a text "
                    "reply. Call approve_write."
                )
            if run.waiting == "input":
                return Failure(
                    "This run is waiting for an answer to a question, not a "
                    "write decision. Call reply."
                )
            return Failure("This run is not waiting for anything.")
        return Failure("This run is not waiting for anything.")

    def _expired_message(self) -> str:
        return (
            f"This run expired (no reply within {self._wait_timeout_s:g} "
            "seconds) and was cancelled. Ask the question again."
        )

    def _wait(self, run: _Run, default):
        try:
            answer = run.answers.get(timeout=self._wait_timeout_s)
        except queue.Empty:
            run.expired = True
            if run.agent is not None:
                run.agent.cancel()
            return default
        return default if answer is None else answer

    def _work(self, run: _Run, question: str, history: list[dict]) -> None:
        try:
            result: TurnResult = self._execute(run, question, history)
        except Exception as e:  # never leave the caller waiting
            result = Failure(f"Internal error: {type(e).__name__}: {e}")
        with self._lock:
            self._active.pop(run.session_id, None)
            self._runs.pop(run.run_id, None)
            if run.expired:
                self._expired.add(run.run_id)
        run.outcomes.put(result)

    def _execute(self, run: _Run, question: str, history: list[dict]) -> TurnResult:
        sid, cfg = run.session_id, self._cfg
        db = Database(cfg.db_path, str(self._scratch_path(sid)), cfg.row_limit, cfg.timeout_s)
        tools = ToolRegistry(db, cfg, retriever=self._retriever)

        def ask_user(q: str) -> str:
            run.waiting = "input"
            run.outcomes.put(NeedsInput(sid, run.run_id, q))
            return self._wait(run, _NO_ANSWER)

        def approve(sql: str) -> bool:
            run.waiting = "approval"
            run.outcomes.put(NeedsApproval(sid, run.run_id, sql))
            return self._wait(run, False)

        tracer = self._tracer_factory(sid)
        agent = Agent(cfg, self._llm, tools, tracer, ask_user, approve)
        run.agent = agent
        try:
            res = agent.run(question, history)
        finally:
            try:
                tracer.close()  # the host may kill the server at any time
            except Exception:
                pass
        if res.stop_reason != "model_unavailable":
            self._save(sid, res.messages)
        return self._finished(sid, res)

    # -- results -------------------------------------------------------------

    def _finished(self, sid: str, res: AgentResult) -> Finished:
        columns, rows, truncated = self._table(sid, res.sql)
        if res.write_rows is not None:
            write = f"executed: {res.write_rows} row(s)"
        elif res.write_rejected:
            write = "rejected"
        elif res.write_sql:
            write = "failed"
        else:
            write = None
        return Finished(
            session_id=sid, answer=res.answer, sql=res.sql, columns=columns,
            rows=rows, rows_truncated=truncated, stop_reason=res.stop_reason,
            steps=res.steps, prompt_tokens=res.prompt_tokens,
            completion_tokens=res.completion_tokens, trace_id=res.trace_id,
            write=write,
        )

    def _table(self, sid: str, sql: str | None) -> tuple[list[str], list[list], bool]:
        """Re-run the answer SQL read-only with the table row limit. Writes
        and unparseable SQL are never run."""
        if not sql:
            return [], [], False
        check = check_sql(sql)
        if not check.ok or check.kind != "read":
            return [], [], False
        cfg = self._cfg
        db = Database(cfg.db_path, str(self._scratch_path(sid)), self._table_row_limit, cfg.timeout_s)
        try:
            res = db.query(sql)
        except QueryError:
            return [], [], False
        return (
            list(res.columns),
            [[_jsonable(v) for v in row] for row in res.rows],
            res.truncated,
        )

    # -- sessions on disk ----------------------------------------------------

    def _history_path(self, sid: str) -> Path:
        return self._dir / f"{sid}.jsonl"

    def _scratch_path(self, sid: str) -> Path:
        return self._dir / f"{sid}.scratch.sqlite"

    def _new_session(self, sid: str) -> None:
        self._history_path(sid).write_text("")
        cfg = self._cfg
        Database(
            cfg.db_path, str(self._scratch_path(sid)), cfg.row_limit, cfg.timeout_s
        ).reset_scratch()

    def _load(self, sid: str) -> list[dict]:
        text = self._history_path(sid).read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def _save(self, sid: str, messages: list[dict]) -> None:
        path = self._history_path(sid)
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text(
            "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages),
            encoding="utf-8",
        )
        os.replace(tmp, path)
