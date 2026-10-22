import json
from pathlib import Path

import pytest

from analyst.config import AgentConfig
from analyst.db import Database, load_olist
from analyst.llm import LLMReply, ModelUnavailable, ToolCall
from analyst.loop import Agent
from analyst.tools import ToolRegistry
from analyst.tracing import Tracer

FIXTURES = Path(__file__).parent / "fixtures" / "olist"


@pytest.fixture(autouse=True)
def no_langfuse(monkeypatch, tmp_path):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.chdir(tmp_path)


def make_agent(tmp_path, replies, **cfg_overrides):
    src = tmp_path / "src.sqlite"
    scratch = tmp_path / "scratch.sqlite"
    load_olist(str(FIXTURES), str(src))
    base = dict(
        model="m", base_url="u", api_key="k",
        db_path=str(src), scratch_path=str(scratch),
    )
    base.update(cfg_overrides)
    cfg = AgentConfig(**base)
    db = Database(cfg.db_path, cfg.scratch_path, cfg.row_limit, cfg.timeout_s)
    db.reset_scratch()
    tools = ToolRegistry(db, cfg)

    class FakeLLM:
        def __init__(self, replies):
            self.replies = list(replies)
            self.calls = []

        def chat(self, messages, tools=None):
            self.calls.append((list(messages), tools))
            r = self.replies.pop(0)
            if isinstance(r, BaseException):
                raise r
            return r

    llm = FakeLLM(replies)
    approvals = []
    asked = []
    agent = Agent(
        cfg, llm, tools, Tracer(),
        ask_user=lambda q: asked.append(q) or "net of refunds",
        approve=lambda s: approvals.append(s) or True,
    )
    return agent, llm, db, approvals, asked


def text_reply(content, p=10, c=5):
    return LLMReply(content=content, prompt_tokens=p, completion_tokens=c, seconds=0.01)


def tool_reply(name, args, tc_id="call_1", p=10, c=5, raw=None):
    return LLMReply(
        content=None,
        tool_calls=[ToolCall(id=tc_id, name=name, raw_arguments=raw if raw is not None else json.dumps(args))],
        prompt_tokens=p, completion_tokens=c, seconds=0.01,
    )


def test_answers_after_tool_call(tmp_path):
    agent, llm, db, _, _ = make_agent(tmp_path, [
        tool_reply("list_tables", {}),
        text_reply("There are 3 tables."),
    ])
    res = agent.run("what tables are there?")
    assert res.stop_reason == "answered"
    assert res.steps == 2
    assert res.answer == "There are 3 tables."
    assert res.prompt_tokens == 20
    assert res.completion_tokens == 10


def test_answers_after_tool_call_records_sql(tmp_path):
    agent, llm, db, _, _ = make_agent(tmp_path, [
        tool_reply("run_sql", {"sql": "SELECT COUNT(*) AS n FROM orders"}),
        text_reply("3 orders."),
    ])
    res = agent.run("how many orders?")
    assert res.stop_reason == "answered"
    assert res.sql == "SELECT COUNT(*) AS n FROM orders"


def test_malformed_arguments_reported_to_model(tmp_path):
    agent, llm, db, _, _ = make_agent(tmp_path, [
        tool_reply("run_sql", None, raw="{not valid json"),
        tool_reply("run_sql", {"sql": "SELECT COUNT(*) AS n FROM orders"}),
        text_reply("3 orders."),
    ])
    res = agent.run("count orders")
    assert res.stop_reason == "answered"
    second_call_messages = llm.calls[1][0]
    tool_msgs = [m for m in second_call_messages if m.get("role") == "tool"]
    assert any("invalid json" in m["content"].lower() for m in tool_msgs)


def test_unknown_tool_name_reported(tmp_path):
    agent, llm, db, _, _ = make_agent(tmp_path, [
        tool_reply("fly_to_moon", {}),
        text_reply("ok"),
    ])
    res = agent.run("hi")
    assert res.stop_reason == "answered"
    second_call_messages = llm.calls[1][0]
    tool_msgs = [m for m in second_call_messages if m.get("role") == "tool"]
    assert any("unknown tool" in m["content"].lower() for m in tool_msgs)


def test_max_steps_stops(tmp_path):
    agent, llm, db, _, _ = make_agent(tmp_path, [
        tool_reply("list_tables", {}),
        tool_reply("list_tables", {}),
        tool_reply("list_tables", {}),
    ], max_steps=3)
    res = agent.run("loop forever")
    assert res.stop_reason == "max_steps"
    assert res.steps == 3
    assert len(llm.calls) == 3


def test_token_budget_stops(tmp_path):
    agent, llm, db, _, _ = make_agent(tmp_path, [
        tool_reply("list_tables", {}, p=15, c=15),
        text_reply("should not be reached"),
    ], max_total_tokens=20)
    res = agent.run("expensive")
    assert res.stop_reason == "token_budget"
    assert res.steps == 1
    assert len(llm.calls) == 1


def test_write_waits_for_approval(tmp_path):
    sql = "UPDATE orders SET order_status = 'x' WHERE order_id = 'o1'"
    agent, llm, db, approvals, _ = make_agent(tmp_path, [
        tool_reply("execute_write", {"sql": sql}),
        text_reply("Done, 1 row updated."),
    ])
    res = agent.run("mark o1 as x")
    assert res.stop_reason == "answered"
    assert approvals == [sql]
    import sqlite3
    con = sqlite3.connect(f"file:{db.scratch_path}?mode=ro", uri=True)
    assert con.execute(
        "SELECT order_status FROM orders WHERE order_id='o1'").fetchone() == ("x",)
    con.close()


def test_write_denied_runs_nothing(tmp_path):
    sql = "UPDATE orders SET order_status = 'x' WHERE order_id = 'o1'"
    src = tmp_path / "src.sqlite"
    scratch = tmp_path / "scratch.sqlite"
    load_olist(str(FIXTURES), str(src))
    cfg = AgentConfig(model="m", base_url="u", api_key="k",
                      db_path=str(src), scratch_path=str(scratch))
    db = Database(cfg.db_path, cfg.scratch_path, cfg.row_limit, cfg.timeout_s)
    db.reset_scratch()
    tools = ToolRegistry(db, cfg)

    class FakeLLM:
        def __init__(self):
            self.calls = []

        def chat(self, messages, tools=None):
            self.calls.append(list(messages))
            if len(self.calls) == 1:
                return tool_reply("execute_write", {"sql": sql})
            return text_reply("The write was not run because you rejected it.")

    llm = FakeLLM()
    agent = Agent(cfg, llm, tools, Tracer(),
                  ask_user=lambda q: "a", approve=lambda s: False)
    res = agent.run("mark o1 as x")
    assert res.stop_reason == "answered"
    assert "not run" in res.answer
    import sqlite3
    con = sqlite3.connect(f"file:{scratch}?mode=ro", uri=True)
    assert con.execute(
        "SELECT order_status FROM orders WHERE order_id='o1'").fetchone() == ("delivered",)
    con.close()
    # the model saw the rejection
    tool_msgs = [m for m in llm.calls[1] if m.get("role") == "tool"]
    assert any("reject" in m["content"].lower() for m in tool_msgs)


def test_ask_user_counts_clarification(tmp_path):
    agent, llm, db, _, asked = make_agent(tmp_path, [
        tool_reply("ask_user", {"question": "Which revenue definition?"}),
        text_reply("Revenue (delivered, paid): 35.5."),
    ], clarify=True)
    res = agent.run("what is the revenue?")
    assert res.stop_reason == "answered"
    assert res.clarifications == 1
    assert asked == ["Which revenue definition?"]
    # the answer was fed back into the next model call
    tool_msgs = [m for m in llm.calls[1][0] if m.get("role") == "tool"]
    assert any("net of refunds" in m["content"] for m in tool_msgs)


def test_model_down_returns_stop_reason(tmp_path):
    agent, llm, db, _, _ = make_agent(tmp_path, [
        ModelUnavailable("connection refused"),
    ])
    res = agent.run("hi")
    assert res.stop_reason == "model_unavailable"
    assert res.steps == 1  # the failed call was attempted
    assert "unavailable" in res.answer.lower()


def test_context_overflow_stops_before_calling(tmp_path):
    agent, llm, db, _, _ = make_agent(tmp_path, [
        text_reply("should not be reached"),
    ], context_window=50)
    res = agent.run("x" * 500)
    assert res.stop_reason == "context_overflow"
    assert llm.calls == []  # never called the model


def test_answer_sql_block_becomes_result_sql(tmp_path):
    """The ```sql block in the final answer is the answer SQL; an earlier
    exploratory run_sql must not shadow it."""
    agent, llm, db, _, _ = make_agent(tmp_path, [
        tool_reply("run_sql", {"sql": "SELECT order_status, COUNT(*) AS n FROM orders GROUP BY order_status"}),
        text_reply(
            "96,478 orders were delivered.\n\n```sql\n"
            "SELECT COUNT(*) AS n FROM orders WHERE order_status = 'delivered'\n```"
        ),
    ])
    res = agent.run("How many orders were delivered?")
    assert res.stop_reason == "answered"
    assert res.sql == "SELECT COUNT(*) AS n FROM orders WHERE order_status = 'delivered'"


def test_retry_with_error_text_then_success(tmp_path):
    """With sql_checks on, a failed SQL feeds its error back and the model
    gets a second chance."""
    agent, llm, db, _, _ = make_agent(tmp_path, [
        tool_reply("run_sql", {"sql": "SELECT nope FROM orders"}),
        tool_reply("run_sql", {"sql": "SELECT COUNT(*) AS n FROM orders"}),
        text_reply("There are 3 orders."),
    ], sql_checks=True)
    res = agent.run("How many orders?")
    assert res.stop_reason == "answered"
    assert res.sql_runs == 2
    assert res.sql_errors == 1
    # the error text reached the model in the next call
    second_call_messages = llm.calls[1][0]
    tool_texts = [str(m.get("content")) for m in second_call_messages
                  if m.get("role") == "tool"]
    assert any("no such column" in t for t in tool_texts)


def test_retry_limit_two(tmp_path):
    """With sql_checks on, three consecutive SQL failures (1 + 2 retries)
    stop the run; the answer carries the last error."""
    bad = tool_reply("run_sql", {"sql": "SELECT nope FROM orders"})
    agent, llm, db, _, _ = make_agent(tmp_path, [bad, bad, bad, bad],
                                      sql_checks=True)
    res = agent.run("How many orders?")
    assert res.stop_reason == "sql_retry_limit"
    assert res.sql_runs == 3
    assert res.sql_errors == 3
    assert "no such column" in res.answer
    assert len(llm.calls) == 3  # no fourth model call


def test_checks_off_means_no_explain(tmp_path, monkeypatch):
    import analyst.tools as tools_mod

    calls = []
    monkeypatch.setattr(
        tools_mod, "explain",
        lambda db, sql: calls.append(sql) or None,
    )
    agent, llm, db, _, _ = make_agent(tmp_path, [
        tool_reply("run_sql", {"sql": "SELECT COUNT(*) AS n FROM orders"}),
        text_reply("3."),
    ], sql_checks=False)
    agent.run("How many orders?")
    assert calls == []


def test_checks_on_calls_explain(tmp_path, monkeypatch):
    import analyst.tools as tools_mod

    calls = []
    monkeypatch.setattr(
        tools_mod, "explain",
        lambda db, sql: calls.append(sql) or None,
    )
    agent, llm, db, _, _ = make_agent(tmp_path, [
        tool_reply("run_sql", {"sql": "SELECT COUNT(*) AS n FROM orders"}),
        text_reply("3."),
    ], sql_checks=True)
    agent.run("How many orders?")
    assert calls == ["SELECT COUNT(*) AS n FROM orders"]


def test_clarify_off_never_offers_ask_user_tool(tmp_path):
    agent, llm, db, _, asked = make_agent(tmp_path, [
        text_reply("3."),
    ], clarify=False)
    agent.run("How many orders?")
    specs = llm.calls[0][1]
    names = [s["function"]["name"] for s in specs]
    assert "ask_user" not in names
    assert asked == []


def test_clarify_on_passes_answer_back_into_messages(tmp_path):
    agent, llm, db, _, asked = make_agent(tmp_path, [
        tool_reply("ask_user", {"question": "What does revenue mean?"}),
        tool_reply("run_sql", {"sql": "SELECT COUNT(*) AS n FROM orders"}),
        text_reply("3 orders."),
    ], clarify=True)
    res = agent.run("What is the revenue?")
    assert res.clarifications == 1
    assert asked == ["What does revenue mean?"]
    # the stored answer ("net of refunds" from make_agent's callback)
    # reached the model in the next call
    second_call = llm.calls[1][0]
    tool_texts = [str(m.get("content")) for m in second_call
                  if m.get("role") == "tool"]
    assert any("net of refunds" in t for t in tool_texts)


def test_at_most_one_clarification_per_run(tmp_path):
    agent, llm, db, _, asked = make_agent(tmp_path, [
        tool_reply("ask_user", {"question": "First ambiguity?"}),
        tool_reply("ask_user", {"question": "Second ambiguity?"}),
        text_reply("3 orders."),
    ], clarify=True)
    res = agent.run("Ambiguous question.")
    assert res.clarifications == 1
    assert len(asked) == 1
    # the second ask_user was refused with guidance, not answered
    third_call = llm.calls[2][0]
    tool_texts = [str(m.get("content")) for m in third_call
                  if m.get("role") == "tool"]
    assert any("only once" in t or "best judgement" in t.lower()
               for t in tool_texts)
