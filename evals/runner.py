"""Eval runner: run a question suite over an agent factory, grade, report."""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date

from analyst.config import AgentConfig
from analyst.db import Database, QueryError
from analyst.llm import LLMClient, ModelUnavailable
from analyst.loop import Agent, AgentResult
from analyst.tools import ToolRegistry
from analyst.tracing import Tracer
from .grading import results_match, value_present

_SQL_BLOCK = re.compile(r"```sql\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_ANY_BLOCK = re.compile(r"```\s*(.*?)```", re.DOTALL)


@dataclass
class RunReport:
    suite: str
    config_name: str
    model: str
    date: str
    per_question: list[dict] = field(default_factory=list)
    success_rate: float = 0.0
    loose_success_rate: float = 0.0
    pass_k: float = 0.0
    mean_tokens: float = 0.0
    mean_steps: float = 0.0
    sql_error_rate: float = 0.0
    mean_clarifications: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _write_rowcount(db: Database, sql: str) -> int | None:
    """Run a write on a throwaway copy of the source; return rows changed."""
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False).name
    try:
        import shutil

        shutil.copyfile(db.path, tmp)
        con = sqlite3.connect(tmp)
        try:
            cur = con.execute(sql)
            con.commit()
            return cur.rowcount
        except sqlite3.Error:
            return None
        finally:
            con.close()
    finally:
        os.unlink(tmp)


def _question_db(question: dict, cfg: AgentConfig, fallback: Database | None) -> Database:
    """Per-question database: benchmarks like Mini-Dev carry their own
    db_path; otherwise fall back to the suite's gold db."""
    if question.get("db_path"):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False).name
        return Database(question["db_path"], tmp, cfg.row_limit, cfg.timeout_s)
    if fallback is None:
        raise ValueError("question has no db_path and no gold_db was given")
    return fallback


def _grade(question: dict, res: AgentResult, gold_db: Database | None, cfg: AgentConfig) -> tuple[bool, bool]:
    """Return (strict_correct, loose_correct). Strict is the BIRD-style
    row match of the answer SQL's result against the gold result. Loose
    additionally counts single-value questions where the gold value
    appears in the agent's result rows (right number, different shape)."""
    db = _question_db(question, cfg, gold_db)
    if question.get("write"):
        if not res.write_sql or res.write_rows is None:
            return False, False
        gold_rows = _write_rowcount(db, question["gold_sql"])
        ok = gold_rows is not None and res.write_rows == gold_rows
        return ok, ok
    if not res.sql:
        return False, False
    try:
        expected = db.query(question["gold_sql"])
        actual = db.query(res.sql)
    except QueryError:
        return False, False
    if results_match(expected, actual):
        return True, True
    return False, value_present(expected, actual)


def run_suite(
    questions: list[dict],
    make_agent,
    repeats: int,
    gold_db: Database | None,
    cfg: AgentConfig,
    suite: str,
    config_name: str,
    model: str,
) -> RunReport:
    """make_agent(question: dict) -> agent with .run(question: str)."""
    per_question: list[dict] = []
    total_correct = total_loose = total_attempts = 0
    total_tokens = total_steps = total_clar = 0
    total_sql_runs = total_sql_errors = 0

    for q in questions:
        attempts = []
        for _ in range(repeats):
            agent = make_agent(q)
            res = agent.run(q["question"])
            correct, loose = _grade(q, res, gold_db, cfg)
            attempts.append(
                {
                    "correct": correct,
                    "loose": loose,
                    "answer": res.answer,
                    "sql": res.sql,
                    "stop_reason": res.stop_reason,
                }
            )
            total_correct += int(correct)
            total_loose += int(loose)
            total_attempts += 1
            total_tokens += res.prompt_tokens + res.completion_tokens
            total_steps += res.steps
            total_clar += res.clarifications
            total_sql_runs += res.sql_runs
            total_sql_errors += res.sql_errors
        per_question.append(
            {
                "id": q["id"],
                "question": q["question"],
                "ambiguous": q.get("ambiguous", False),
                "write": q.get("write", False),
                "correct": [a["correct"] for a in attempts],
                "loose": [a["loose"] for a in attempts],
                "solved_every_repeat": all(a["correct"] for a in attempts),
                "answers": [a["answer"] for a in attempts],
                "sqls": [a["sql"] for a in attempts],
                "stop_reasons": [a["stop_reason"] for a in attempts],
            }
        )

    n = len(questions)
    return RunReport(
        suite=suite,
        config_name=config_name,
        model=model,
        date=date.today().isoformat(),
        per_question=per_question,
        success_rate=total_correct / total_attempts if total_attempts else 0.0,
        loose_success_rate=total_loose / total_attempts if total_attempts else 0.0,
        pass_k=(
            sum(p["solved_every_repeat"] for p in per_question) / n if n else 0.0
        ),
        mean_tokens=total_tokens / total_attempts if total_attempts else 0.0,
        mean_steps=total_steps / total_attempts if total_attempts else 0.0,
        sql_error_rate=(
            total_sql_errors / total_sql_runs if total_sql_runs else 0.0
        ),
        mean_clarifications=total_clar / total_attempts if total_attempts else 0.0,
    )


# -- B0 baseline: plain model, full schema in the prompt, no tools ------------

BASELINE_PROMPT = """You are a data analyst. The user asks a business question \
about {db_note}.

Database schema:
{schema}

Answer with a single SQL query in a ```sql fenced block, then one short \
sentence. The query must run against this schema as written.
"""


def extract_sql(text: str | None) -> str | None:
    if not text:
        return None
    m = _SQL_BLOCK.search(text) or _ANY_BLOCK.search(text)
    if m:
        return m.group(1).strip().rstrip(";").strip()
    return None


def full_schema(db: Database) -> str:
    parts = []
    for table in db.tables():
        info = db.describe(table, samples=2)
        cols = ", ".join(f"{c['name']} {c['type']}" for c in info["columns"])
        part = f"{table}({cols})"
        if info["sample_rows"]:
            part += " samples: " + "; ".join(
                str(tuple(r)) for r in info["sample_rows"]
            )
        parts.append(part)
    return "\n".join(parts)


class B0Agent:
    """B0 baseline: one model call, no tools, full schema in the prompt.

    The model answers with SQL directly; the harness extracts and runs it
    read-only (writes are never executed).
    """

    def __init__(self, cfg: AgentConfig, llm: LLMClient, db: Database,
                 db_note: str = "a SQLite database"):
        self._cfg = cfg
        self._llm = llm
        self._db = db
        self._system = BASELINE_PROMPT.format(db_note=db_note, schema=full_schema(db))

    def run(self, question: str) -> AgentResult:
        try:
            reply = self._llm.chat(
                [
                    {"role": "system", "content": self._system},
                    {"role": "user", "content": question},
                ]
            )
        except ModelUnavailable as e:
            return AgentResult(
                answer=f"Stopped: the model server is unavailable ({e}).",
                sql=None, steps=1, prompt_tokens=0, completion_tokens=0,
                clarifications=0, stop_reason="model_unavailable",
                sql_runs=1, sql_errors=1,
            )
        sql = extract_sql(reply.content)
        sql_runs = sql_errors = 0
        if sql is None:
            sql_runs = sql_errors = 1
            answer = reply.content or ""
        else:
            sql_runs = 1
            from analyst.sqlcheck import check_sql

            check = check_sql(sql)
            if not check.ok or check.kind == "write":
                # B0 has no approval gate; a write is simply not executed.
                sql_errors = 1
                answer = (
                    f"{reply.content or ''}\n[baseline: query not executed "
                    f"(not a plain read)]"
                )
            else:
                try:
                    res = self._db.query(sql)
                    rows = "\n".join(
                        " | ".join("" if v is None else str(v) for v in r)
                        for r in res.rows
                    )
                    answer = f"{reply.content or ''}\n[rows]\n{rows}"
                except QueryError as e:
                    sql_errors = 1
                    answer = f"{reply.content or ''}\n[query error: {e}]"
        return AgentResult(
            answer=answer,
            sql=sql,
            steps=1,
            prompt_tokens=reply.prompt_tokens,
            completion_tokens=reply.completion_tokens,
            clarifications=0,
            stop_reason="answered",
            sql_runs=sql_runs,
            sql_errors=sql_errors,
        )


def benchmark_system_prompt(db: Database, db_id: str) -> str:
    """System prompt for benchmark suites (Mini-Dev): same rules as the
    default Olist prompt, but the database is named correctly."""
    return (
        f"You are a data analyst agent for the '{db_id}' SQLite database "
        "(BIRD benchmark). Answer the user's business question using the "
        "tools.\n\nRules:\n"
        "1. Explore before querying: use list_tables and describe_table "
        "to learn the schema before writing SQL.\n"
        "2. Use run_sql for read-only queries. Data-changing SQL is "
        "rejected there.\n"
        "3. When you are done, reply with a concise plain-text answer. "
        "End with the single SQL query in a ```sql fenced block that "
        "directly computes the requested result — not an exploratory or "
        "breakdown query. If the question asks for one number, the query "
        "must return exactly that number.\n"
        "4. If a tool returns an error, read the error and fix your "
        "approach.\n"
    )


def make_agent_factory(
    cfg: AgentConfig,
    approve_all: bool = True,
):
    """Returns make_agent(question) -> Agent | B0Agent for the real model."""

    def make_agent(question: dict):
        if question.get("db_path"):
            db = Database(
                question["db_path"],
                tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False).name,
                cfg.row_limit,
                cfg.timeout_s,
            )
        else:
            db = Database(cfg.db_path, cfg.scratch_path, cfg.row_limit, cfg.timeout_s)
            db.reset_scratch()
        if cfg.run_name == "b0":
            if question.get("db_id"):
                db_note = f"the '{question['db_id']}' SQLite database (BIRD benchmark)"
            else:
                db_note = (
                    "the Olist marketplace database (SQLite, Brazilian "
                    "e-commerce, 2017-2018)"
                )
            return B0Agent(cfg, LLMClient(cfg), db, db_note=db_note)
        tools = ToolRegistry(db, cfg)
        tracer = Tracer()
        stored = question.get("clarification_answer")

        def ask_user(clarification: str) -> str:
            return stored or "Use your best judgement and state your assumption."

        system_prompt = None
        if question.get("db_path") and question.get("db_id"):
            system_prompt = benchmark_system_prompt(db, question["db_id"])
        return Agent(
            cfg,
            LLMClient(cfg),
            tools,
            tracer,
            ask_user,
            approve=lambda sql: approve_all,
            system_prompt=system_prompt,
        )

    return make_agent
