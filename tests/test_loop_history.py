"""Conversation history in the loop (plan Task 2)."""

import pytest

from analyst.loop import heal_history
from test_loop_hardening import ROWS, build, text, tools_reply


@pytest.fixture(autouse=True)
def no_langfuse(monkeypatch, tmp_path):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.chdir(tmp_path)


def contents(call):
    return [str(m.get("content")) for m in call[0]]


def test_second_turn_sees_first_turn_messages(tmp_path):
    agent, llm, db, _ = build(tmp_path, [text("first answer"), text("second answer")])
    r1 = agent.run("first question")
    agent.run("second question", history=r1.messages)
    seen = contents(llm.calls[1])
    assert seen.index("first question") < seen.index("first answer") < seen.index("second question")


def test_result_messages_exclude_system_and_include_user_turn(tmp_path):
    agent, llm, db, _ = build(tmp_path, [text("the answer")])
    res = agent.run("q")
    assert res.messages[0] == {"role": "user", "content": "q"}
    assert res.messages[-1]["role"] == "assistant" and res.messages[-1]["content"] == "the answer"
    assert all(m["role"] != "system" for m in res.messages)


def test_history_argument_not_mutated(tmp_path):
    agent, llm, db, _ = build(tmp_path, [text("a1"), text("a2")], microcompact_keep=1)
    r1 = agent.run("q1")
    history = [dict(m) for m in r1.messages]
    agent.run("q2", history=history)
    assert history == r1.messages


def test_heal_adds_results_for_dangling_tool_calls():
    msgs = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "run_sql", "arguments": "{}"}},
            {"id": "b", "type": "function", "function": {"name": "run_sql", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "ok"},
    ]
    healed = heal_history(msgs)
    tool_ids = [m["tool_call_id"] for m in healed if m["role"] == "tool"]
    assert tool_ids == ["a", "b"]
    assert "interrupted" in healed[-1]["content"].lower()
    assert len(msgs) == 3                          # input untouched
    assert heal_history(healed) == healed          # idempotent


def test_retry_limit_midbatch_leaves_valid_history(tmp_path):
    bad = ("run_sql", {"sql": "SELECT nope FROM orders"})
    agent, llm, db, _ = build(tmp_path, [tools_reply(bad, bad, bad, bad)], sql_checks=True)
    res = agent.run("q")
    assert res.stop_reason == "sql_retry_limit"
    answered = {m["tool_call_id"] for m in res.messages if m["role"] == "tool"}
    assert answered == {"call_0", "call_1", "call_2", "call_3"}


def test_dangling_history_is_healed_before_use(tmp_path):
    agent, llm, db, _ = build(tmp_path, [text("fine")])
    history = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "x", "type": "function", "function": {"name": "run_sql", "arguments": "{}"}}]},
    ]
    agent.run("q2", history=history)
    assert any("interrupted" in c.lower() for c in contents(llm.calls[0]))


def test_old_turn_results_elided_with_microcompact_keep(tmp_path):
    agent, llm, db, _ = build(tmp_path, [
        tools_reply(("run_sql", ROWS)), text("turn one done"),
        tools_reply(("run_sql", ROWS)), text("turn two done"),
    ], microcompact_keep=1)
    r1 = agent.run("q1")
    agent.run("q2", history=r1.messages)
    tool_texts = [str(m["content"]) for m in llm.calls[3][0] if m.get("role") == "tool"]
    assert len(tool_texts) == 2
    assert "elided" in tool_texts[0] and "elided" not in tool_texts[1]
