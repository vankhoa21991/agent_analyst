"""The agent loop: model call, tool calls, results, repeat.

No framework. Budgets: max_steps (model calls), max_total_tokens, and a
character-based context estimate (chars / 3) checked before every call.
"""

from __future__ import annotations

import json
import re
import sys
import threading
from dataclasses import dataclass, field
from typing import Callable

from .config import AgentConfig, load_config
from .db import Database
from .llm import LLMClient, LLMReply, ModelUnavailable
from .tools import ToolRegistry, ToolResult
from .tracing import Tracer

SYSTEM_PROMPT = """You are a data analyst agent for the Olist marketplace \
database (SQLite, Brazilian e-commerce, 2017-2018). Answer the user's \
business question using the tools.

Rules:
1. Explore before querying: use list_tables and describe_table to learn the \
schema before writing SQL.
2. Use run_sql for read-only queries. Data-changing SQL is rejected there.
3. To change data, use execute_write. It only runs after the user approves \
it. If the user rejects it, say so and do not retry.
4. When you are done, reply with a concise plain-text answer. End with the \
single SQL query in a ```sql fenced block that directly computes the \
requested result — not an exploratory or breakdown query. If the question \
asks for one number, the query must return exactly that number.
5. If a tool returns an error, read the error and fix your approach.
"""

_SQL_BLOCK = re.compile(r"```sql\s*(.*?)```", re.DOTALL)
_ANY_BLOCK = re.compile(r"```\s*(.*?)```", re.DOTALL)


def answer_sql(text: str | None) -> str | None:
    """The SQL the agent presents as its answer: the last fenced sql block
    in the final message (falling back to any fenced block)."""
    if not text:
        return None
    blocks = _SQL_BLOCK.findall(text) or _ANY_BLOCK.findall(text)
    if not blocks:
        return None
    return blocks[-1].strip().rstrip(";").strip()

_ROW_COUNT = re.compile(r"\((\d+) rows, ")

TRUNCATED_CALL = (
    "Tool call not executed: the response hit the output token limit, so "
    "its arguments may be truncated. Re-issue the tool call with complete "
    "arguments."
)

CLARIFY_RULE = """6. If the question has more than one reasonable meaning \
(for example an undefined business term), call ask_user exactly once before \
running any SQL, then use the confirmed meaning.
"""


@dataclass
class AgentResult:
    answer: str
    sql: str | None
    steps: int
    prompt_tokens: int
    completion_tokens: int
    clarifications: int
    stop_reason: str  # answered | max_steps | token_budget | model_unavailable | context_overflow | sql_retry_limit | cancelled
    sql_runs: int = 0
    sql_errors: int = 0
    write_sql: str | None = None
    write_rows: int | None = None
    write_rejected: bool = False


class Agent:
    def __init__(
        self,
        cfg: AgentConfig,
        llm: LLMClient,
        tools: ToolRegistry,
        tracer: Tracer,
        ask_user: Callable[[str], str],
        approve: Callable[[str], bool | str],
        system_prompt: str | None = None,
    ):
        self._cfg = cfg
        self._llm = llm
        self._tools = tools
        self._tracer = tracer
        self._ask_user = ask_user
        self._approve = approve
        self._system_override = system_prompt
        self._cancel = threading.Event()
        tools.ask_user = ask_user

    def cancel(self) -> None:
        """Ask the current run to stop at the next safe point (thread-safe).
        Applies to one run; the flag clears when that run ends."""
        self._cancel.set()

    def _system_prompt(self) -> str:
        if self._system_override is not None:
            return self._system_override
        return SYSTEM_PROMPT + (CLARIFY_RULE if self._cfg.clarify else "")

    @staticmethod
    def _estimate_tokens(messages: list[dict]) -> int:
        return sum(len(str(m.get("content") or "")) for m in messages) // 3

    @staticmethod
    def _microcompact(sql_results: list[dict], keep: int) -> int:
        """Replace all but the newest `keep` run_sql result bodies with a
        one-line marker. Returns how many were elided."""
        if keep <= 0:
            return 0
        old = sql_results[:-keep]
        del sql_results[:-keep]
        for msg in old:
            m = _ROW_COUNT.search(msg["content"])
            rows = f": {m.group(1)} rows" if m else ""
            msg["content"] = (
                f"(earlier run_sql result elided{rows}; "
                "re-run the query if you need it)"
            )
        return len(old)

    def run(self, question: str) -> AgentResult:
        try:
            return self._run(question)
        finally:
            self._cancel.clear()

    def _run(self, question: str) -> AgentResult:
        cfg = self._cfg
        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": question},
        ]
        specs = self._tools.specs()
        steps = 0
        prompt_tokens = 0
        completion_tokens = 0
        clarifications = 0
        sql_runs = 0
        sql_errors = 0
        sql_fail_streak = 0
        write_sql: str | None = None
        write_rows: int | None = None
        last_sql: str | None = None
        write_rejected = False
        sql_results: list[dict] = []  # run_sql tool messages, oldest first
        # Provider-reported context size after the latest reply, and how
        # many messages that number already covers. None = unknown.
        anchor_tokens: int | None = None
        anchor_len = 0

        def finish(answer: str, stop_reason: str) -> AgentResult:
            return AgentResult(
                answer=answer,
                # The SQL the agent presents as its answer wins over the
                # last exploratory run_sql (which is often a breakdown).
                sql=answer_sql(answer) or last_sql,
                steps=steps,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                clarifications=clarifications,
                stop_reason=stop_reason,
                sql_runs=sql_runs,
                sql_errors=sql_errors,
                write_sql=write_sql,
                write_rows=write_rows,
                write_rejected=write_rejected,
            )

        run_metadata = {
            "run_name": cfg.run_name,
            "model": cfg.model,
            "tools": [s["function"]["name"] for s in specs],
            "flags": {
                "rag": cfg.rag,
                "sql_checks": cfg.sql_checks,
                "clarify": cfg.clarify,
                "microcompact_keep": cfg.microcompact_keep,
            },
        }
        with self._tracer.run("agent run", input=question, metadata=run_metadata):
            while steps < cfg.max_steps:
                if self._cancel.is_set():
                    return finish("Stopped: cancelled by the user.", "cancelled")
                if self._microcompact(sql_results, cfg.microcompact_keep):
                    anchor_tokens = None  # earlier messages shrank
                if anchor_tokens is None:
                    context_tokens = self._estimate_tokens(messages)
                else:
                    context_tokens = anchor_tokens + self._estimate_tokens(
                        messages[anchor_len:]
                    )
                if context_tokens > cfg.context_window:
                    return finish(
                        "Stopped: the conversation exceeded the context "
                        f"window ({cfg.context_window} tokens estimated).",
                        "context_overflow",
                    )
                if prompt_tokens + completion_tokens >= cfg.max_total_tokens:
                    return finish(
                        "Stopped: the token budget "
                        f"({cfg.max_total_tokens} tokens) was used up.",
                        "token_budget",
                    )
                steps += 1
                try:
                    with self._tracer.span(
                        "llm call", step=steps,
                        prompt_tokens=self._estimate_tokens(messages),
                    ):
                        reply = self._llm.chat(messages, tools=specs)
                except ModelUnavailable as e:
                    return finish(
                        f"Stopped: the model server is unavailable ({e}).",
                        "model_unavailable",
                    )
                prompt_tokens += reply.prompt_tokens
                completion_tokens += reply.completion_tokens

                assistant_msg: dict = {"role": "assistant", "content": reply.content}
                if reply.reasoning:
                    assistant_msg["reasoning_content"] = reply.reasoning
                if reply.tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": tc.raw_arguments,
                            },
                        }
                        for tc in reply.tool_calls
                    ]
                messages.append(assistant_msg)
                anchor_tokens = (
                    reply.prompt_tokens + reply.completion_tokens
                    if reply.prompt_tokens > 0
                    else None
                )
                anchor_len = len(messages)

                if not reply.tool_calls:
                    return finish(reply.content or "", "answered")

                truncated = reply.finish_reason == "length"
                for tc in reply.tool_calls:
                    if truncated or self._cancel.is_set():
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": TRUNCATED_CALL
                                if truncated
                                else "Operation aborted: the run was cancelled.",
                            }
                        )
                        continue
                    if tc.name == "ask_user" and clarifications >= 1:
                        result = ToolResult(
                            "You already asked one clarifying question; "
                            "you may ask only once per run. Proceed with "
                            "your best judgement and state the assumption "
                            "in your answer.",
                            ok=False,
                        )
                    else:
                        result = self._execute_tool_call(tc.name, tc.raw_arguments)
                    if tc.name == "ask_user" and result.ok:
                        clarifications += 1
                    if tc.name == "run_sql":
                        sql_runs += 1
                        if result.ok:
                            last_sql = json.loads(tc.raw_arguments).get("sql")
                            sql_fail_streak = 0
                        else:
                            sql_errors += 1
                            sql_fail_streak += 1
                            if cfg.sql_checks and sql_fail_streak > 2:
                                return finish(
                                    f"Stopped: the query failed "
                                    f"{sql_fail_streak} times in a row "
                                    "(retry limit is 2). Last error: "
                                    f"{result.text}",
                                    "sql_retry_limit",
                                )
                    if result.needs_approval:
                        verdict = self._approve(result.pending_sql or "")
                        # A string is a rejection carrying its reason.
                        if verdict and not isinstance(verdict, str):
                            write_sql = result.pending_sql
                            result = self._tools.run_approved_write(
                                result.pending_sql
                            )
                            write_rows = result.rows
                        else:
                            write_rejected = True
                            reason = verdict.strip() if isinstance(verdict, str) else ""
                            result = ToolResult(
                                "The user rejected the write; it was not "
                                "executed. "
                                + (f"Reason: {reason}. " if reason else "")
                                + "Tell the user and do not retry."
                            )
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result.text,
                    }
                    messages.append(tool_msg)
                    if tc.name == "run_sql" and result.ok:
                        sql_results.append(tool_msg)
            return finish(
                f"Stopped after {cfg.max_steps} steps without a final answer.",
                "max_steps",
            )

    def _execute_tool_call(self, name: str, raw_arguments: str) -> ToolResult:
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            return ToolResult(
                f"Invalid JSON arguments: {e}. Retry the tool call with "
                "valid JSON.",
                ok=False,
            )
        return self._tools.call(name, arguments)


def build_agent(
    cfg: AgentConfig,
    ask_user: Callable[[str], str],
    approve: Callable[[str], bool],
) -> tuple[Agent, Database, Tracer]:
    db = Database(cfg.db_path, cfg.scratch_path, cfg.row_limit, cfg.timeout_s)
    db.reset_scratch()
    tools = ToolRegistry(db, cfg)
    tracer = Tracer()
    return Agent(cfg, LLMClient(cfg), tools, tracer, ask_user, approve), db, tracer


def main() -> None:
    cfg_path = "agent.yaml"
    args = [a for a in sys.argv[1:]]
    question = " ".join(args)
    if not question:
        question = input("Question: ")
    cfg = load_config(cfg_path)
    db = Database(cfg.db_path, cfg.scratch_path, cfg.row_limit, cfg.timeout_s)
    db.reset_scratch()

    def ask_user(q: str) -> str:
        print(f"\nAGENT asks: {q}")
        return input("You: ")

    def approve(sql: str) -> bool:
        print("\nPending write (runs on the scratch copy only):")
        print(sql)
        return input("Approve? [y/N] ").strip().lower() == "y"

    agent, _, tracer = build_agent(cfg, ask_user, approve)
    res = agent.run(question)
    print(f"\nAnswer: {res.answer}")
    if res.sql:
        print(f"SQL: {res.sql}")
    print(
        f"[{res.stop_reason}] steps={res.steps} "
        f"tokens={res.prompt_tokens}+{res.completion_tokens} "
        f"clarifications={res.clarifications}"
    )
    tracer.close()


if __name__ == "__main__":
    main()
