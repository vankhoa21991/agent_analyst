"""Loop hardening: truncated output, argument validation, rejection
reasons, microcompaction, provider-reported context size, cancellation,
and trace metadata (docs/notes/loop-lessons.md items 1-7)."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from analyst.config import AgentConfig
from analyst.db import Database, load_olist
from analyst.llm import LLMReply, ToolCall
from analyst.loop import Agent, AgentResult
from analyst.tools import ToolRegistry
from analyst.tracing import Tracer

FIXTURES = Path(__file__).parent / "fixtures" / "olist"
UPDATE = "UPDATE orders SET order_status = 'x' WHERE order_id = 'o1'"


@pytest.fixture(autouse=True)
def no_langfuse(monkeypatch, tmp_path):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.chdir(tmp_path)


class FakeLLM:
    def __init__(self, replies, on_chat=None):
        self.replies = list(replies)
        self.calls = []
        self.on_chat = on_chat

    def chat(self, messages, tools=None):
        self.calls.append((list(messages), tools))
        if self.on_chat:
            self.on_chat()
        return self.replies.pop(0)


def build(tmp_path, replies, approve=None, tracer=None, on_chat=None, **cfg_kw):
    src = tmp_path / "src.sqlite"
    scratch = tmp_path / "scratch.sqlite"
    load_olist(str(FIXTURES), str(src))
    cfg = AgentConfig(model="m", base_url="u", api_key="k",
                      db_path=str(src), scratch_path=str(scratch), **cfg_kw)
    db = Database(cfg.db_path, cfg.scratch_path, cfg.row_limit, cfg.timeout_s)
    db.reset_scratch()
    llm = FakeLLM(replies, on_chat)
    approvals = []

    def default_approve(sql):
        approvals.append(sql)
        return True

    agent = Agent(cfg, llm, ToolRegistry(db, cfg), tracer or Tracer(),
                  ask_user=lambda q: "a", approve=approve or default_approve)
    return agent, llm, db, approvals


def text(content, p=10, c=5):
    return LLMReply(content=content, prompt_tokens=p, completion_tokens=c,
                    seconds=0.01)


def tools_reply(*calls, p=10, c=5, finish_reason=None):
    return LLMReply(
        content=None,
        tool_calls=[ToolCall(id=f"call_{i}", name=n, raw_arguments=json.dumps(a))
                    for i, (n, a) in enumerate(calls)],
        prompt_tokens=p, completion_tokens=c, seconds=0.01,
        finish_reason=finish_reason,
    )


def tool_texts(llm, call_index):
    return [str(m["content"]) for m in llm.calls[call_index][0]
            if m.get("role") == "tool"]


def scratch_status(db):
    con = sqlite3.connect(f"file:{db.scratch_path}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT order_status FROM orders WHERE order_id='o1'").fetchone()
    finally:
        con.close()


# -- 1. truncated output --------------------------------------------------

def test_truncated_reply_executes_no_tool_calls(tmp_path):
    agent, llm, db, approvals = build(tmp_path, [
        tools_reply(("execute_write", {"sql": UPDATE}),
                    ("run_sql", {"sql": "SELECT 1"}),
                    finish_reason="length"),
        text("ok, will retry"),
    ])
    res = agent.run("mark o1 as x")
    assert approvals == []                      # never reached approval
    assert res.sql_runs == 0                    # the read was not run either
    assert scratch_status(db) == ("delivered",)
    texts = tool_texts(llm, 1)
    assert len(texts) == 2                      # one error result per call
    assert all("token limit" in t for t in texts)
    assert res.stop_reason == "answered"


def test_normal_finish_reason_still_executes(tmp_path):
    agent, llm, db, _ = build(tmp_path, [
        tools_reply(("run_sql", {"sql": "SELECT 1 AS n"}), finish_reason="tool_calls"),
        text("1"),
    ])
    res = agent.run("one")
    assert res.sql_runs == 1


# -- 2. argument validation -----------------------------------------------

@pytest.fixture
def registry(tmp_path):
    src = tmp_path / "src.sqlite"
    load_olist(str(FIXTURES), str(src))
    cfg = AgentConfig(model="m", base_url="u", api_key="k", db_path=str(src),
                      scratch_path=str(tmp_path / "s.sqlite"))
    db = Database(cfg.db_path, cfg.scratch_path, cfg.row_limit, cfg.timeout_s)
    return ToolRegistry(db, cfg)


def test_missing_required_argument_is_named(registry):
    res = registry.call("run_sql", {})
    assert not res.ok
    assert "missing required argument 'sql'" in res.text


def test_wrong_argument_type_is_named(registry):
    res = registry.call("run_sql", {"sql": 5})
    assert not res.ok
    assert "'sql' must be a string" in res.text


def test_valid_arguments_still_run(registry):
    assert registry.call("run_sql", {"sql": "SELECT 1 AS n"}).ok


# -- 3. rejection reasons --------------------------------------------------

def test_rejection_reason_reaches_the_model(tmp_path):
    agent, llm, db, _ = build(
        tmp_path,
        [tools_reply(("execute_write", {"sql": UPDATE})), text("understood")],
        approve=lambda sql: "touches production orders",
    )
    res = agent.run("mark o1 as x")
    assert scratch_status(db) == ("delivered",)         # a str never approves
    assert any("touches production orders" in t for t in tool_texts(llm, 1))
    assert res.write_rejected is True
    assert res.write_sql is None


def test_plain_false_still_rejects_without_reason(tmp_path):
    agent, llm, db, _ = build(
        tmp_path,
        [tools_reply(("execute_write", {"sql": UPDATE})), text("ok")],
        approve=lambda sql: False,
    )
    res = agent.run("mark o1 as x")
    assert scratch_status(db) == ("delivered",)
    assert any("rejected" in t.lower() for t in tool_texts(llm, 1))
    assert res.write_rejected is True


def test_approved_write_is_not_flagged_rejected(tmp_path):
    agent, llm, db, _ = build(
        tmp_path, [tools_reply(("execute_write", {"sql": UPDATE})), text("done")])
    res = agent.run("mark o1 as x")
    assert scratch_status(db) == ("x",)
    assert res.write_rejected is False and res.write_rows == 1


def test_write_status_line():
    from analyst.ui.app import write_status_line

    def result(**kw):
        base = dict(answer="", sql=None, steps=1, prompt_tokens=0,
                    completion_tokens=0, clarifications=0,
                    stop_reason="answered")
        return AgentResult(**{**base, **kw})

    assert write_status_line(result()) == ""
    assert "1 row" in write_status_line(result(write_sql="U", write_rows=1))
    assert "not approved" in write_status_line(result(write_rejected=True))
    failed = write_status_line(result(write_sql="U", write_rows=None))
    assert "failed" in failed and "not approved" not in failed


# -- 4. microcompaction ----------------------------------------------------

ROWS = {"sql": "SELECT order_id FROM orders"}


def test_old_sql_results_are_elided_when_enabled(tmp_path):
    agent, llm, db, _ = build(tmp_path, [
        tools_reply(("run_sql", ROWS)), tools_reply(("run_sql", ROWS)),
        tools_reply(("run_sql", ROWS)), text("done"),
    ], microcompact_keep=1)
    agent.run("q")
    texts = tool_texts(llm, 3)                  # what the 4th call saw
    assert len(texts) == 3
    assert all("elided" in t and "3 rows" in t for t in texts[:2])
    assert "elided" not in texts[2]             # newest result intact
    assert "order_id" in texts[2]


def test_nothing_elided_by_default(tmp_path):
    agent, llm, db, _ = build(tmp_path, [
        tools_reply(("run_sql", ROWS)), tools_reply(("run_sql", ROWS)),
        text("done"),
    ])
    agent.run("q")
    assert not any("elided" in t for t in tool_texts(llm, 2))


def test_error_results_are_kept(tmp_path):
    bad = {"sql": "SELECT nope FROM orders"}
    agent, llm, db, _ = build(tmp_path, [
        tools_reply(("run_sql", bad)), tools_reply(("run_sql", ROWS)),
        tools_reply(("run_sql", ROWS)), text("done"),
    ], microcompact_keep=1)
    agent.run("q")
    texts = tool_texts(llm, 3)
    assert any("no such column" in t for t in texts)


# -- 5. provider-reported context size -------------------------------------

def test_context_guard_uses_provider_prompt_tokens(tmp_path):
    agent, llm, db, _ = build(tmp_path, [
        tools_reply(("list_tables", {}), p=5000, c=5),
        text("unreachable"),
    ], context_window=4000)
    res = agent.run("q")
    assert res.stop_reason == "context_overflow"
    assert len(llm.calls) == 1


def test_provider_number_dropped_after_elision(tmp_path):
    """Eliding old results shrinks the context, so a stale provider number
    must not trigger a false overflow."""
    agent, llm, db, _ = build(tmp_path, [
        tools_reply(("run_sql", ROWS), p=10),
        tools_reply(("run_sql", ROWS), p=5000),
        text("done"),
    ], context_window=4000, microcompact_keep=1)
    res = agent.run("q")
    assert res.stop_reason == "answered"
    assert len(llm.calls) == 3


# -- 6. cancellation -------------------------------------------------------

def test_cancel_skips_tools_and_stops(tmp_path):
    holder = {}
    agent, llm, db, approvals = build(
        tmp_path,
        [tools_reply(("execute_write", {"sql": UPDATE})), text("unreachable")],
        on_chat=lambda: holder["agent"].cancel(),
    )
    holder["agent"] = agent
    res = agent.run("mark o1 as x")
    assert res.stop_reason == "cancelled"
    assert approvals == []
    assert scratch_status(db) == ("delivered",)
    assert len(llm.calls) == 1


def test_cancel_before_first_call_never_calls_model(tmp_path):
    agent, llm, db, _ = build(tmp_path, [text("unreachable")])
    agent.cancel()
    res = agent.run("q")
    assert res.stop_reason == "cancelled"
    assert llm.calls == []


def test_cancel_applies_to_one_run_only(tmp_path):
    agent, llm, db, _ = build(tmp_path, [text("fine")])
    agent.cancel()
    assert agent.run("q").stop_reason == "cancelled"
    assert agent.run("q").stop_reason == "answered"


# -- 7. trace metadata ----------------------------------------------------

class RecordingTracer:
    def __init__(self):
        self.runs = []

    @contextmanager
    def run(self, name, input=None, metadata=None):
        self.runs.append({"name": name, "input": input, "metadata": metadata})
        yield None

    @contextmanager
    def span(self, name, **attrs):
        yield None


def test_run_span_records_tools_and_flags(tmp_path):
    tracer = RecordingTracer()
    agent, llm, db, _ = build(tmp_path, [text("3.")], tracer=tracer,
                              clarify=True, sql_checks=True, run_name="h4")
    agent.run("How many orders?")
    meta = tracer.runs[0]["metadata"]
    assert meta["run_name"] == "h4"
    assert meta["model"] == "m"
    assert meta["tools"] == ["list_tables", "describe_table", "run_sql",
                             "execute_write", "ask_user"]
    assert meta["flags"] == {"rag": False, "sql_checks": True,
                             "clarify": True, "microcompact_keep": 0}


def test_tracer_run_merges_metadata(monkeypatch, tmp_path):
    seen = {}

    class FakeLF:
        @contextmanager
        def start_as_current_observation(self, **kw):
            seen.update(kw)
            yield object()

    t = Tracer(session_id="s1", metadata={"a": 1})
    t._lf = FakeLF()
    with t.run("agent run", input="q", metadata={"tools": ["x"]}):
        pass
    assert seen["metadata"] == {"a": 1, "session_id": "s1", "tools": ["x"]}


# -- UI reply composition -------------------------------------------------

def _result(**kw):
    base = dict(answer="", sql=None, steps=1, prompt_tokens=0,
                completion_tokens=0, clarifications=0, stop_reason="answered")
    return AgentResult(**{**base, **kw})


def test_reply_does_not_repeat_sql_already_in_answer():
    from analyst.ui.app import compose_reply

    answer = "3 orders.\n\n```sql\nSELECT COUNT(*) FROM orders\n```"
    content = compose_reply(_result(answer=answer, sql="SELECT COUNT(*) FROM orders"))
    assert content.count("```sql") == 1


def test_reply_appends_sql_when_answer_has_none():
    from analyst.ui.app import compose_reply

    content = compose_reply(_result(answer="3 orders.", sql="SELECT 1"))
    assert content.count("```sql") == 1 and "SELECT 1" in content


def test_reply_notes_stop_reason():
    from analyst.ui.app import compose_reply

    assert "stopped: max_steps" in compose_reply(_result(stop_reason="max_steps"))
