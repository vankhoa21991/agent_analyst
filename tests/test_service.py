"""AnalystService: sessions, pausing for questions and approvals, table
(plan Task 3)."""

import sqlite3
import time
from pathlib import Path

import pytest

from analyst.config import AgentConfig
from analyst.db import load_olist
from analyst.llm import ModelUnavailable
from analyst.service import (
    AnalystService, Failure, Finished, NeedsApproval, NeedsInput,
)
from test_loop_hardening import FIXTURES, UPDATE, text, tools_reply

ASK = ("ask_user", {"question": "Which revenue?"})


@pytest.fixture(autouse=True)
def no_langfuse(monkeypatch, tmp_path):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.chdir(tmp_path)


class ScriptedLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append(list(messages))
        r = self.replies.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def make(tmp_path, replies, cfg_kw=None, **svc_kw):
    src = tmp_path / "src.sqlite"
    if not src.exists():
        load_olist(str(FIXTURES), str(src))
    cfg = AgentConfig(model="m", base_url="u", api_key="k", db_path=str(src),
                      scratch_path=str(tmp_path / "unused.sqlite"),
                      clarify=True, **(cfg_kw or {}))
    llm = ScriptedLLM(replies)
    svc = AnalystService(cfg, sessions_dir=tmp_path / "sessions", llm=llm, **svc_kw)
    return svc, llm


def answer(sql):
    return text(f"Here you go.\n\n```sql\n{sql}\n```")


def status_in(path, order="o1"):
    con = sqlite3.connect(path)
    try:
        return con.execute(
            "SELECT order_status FROM orders WHERE order_id=?", (order,)).fetchone()
    finally:
        con.close()


def scratch(tmp_path, sid):
    return tmp_path / "sessions" / f"{sid}.scratch.sqlite"


def test_ask_returns_finished_with_table_for_answer_sql(tmp_path):
    svc, llm = make(tmp_path, [answer("SELECT order_id FROM orders")])
    r = svc.ask("list orders")
    assert isinstance(r, Finished)
    assert r.columns == ["order_id"] and len(r.rows) == 3 and not r.rows_truncated
    assert r.stop_reason == "answered" and r.session_id and r.sql == "SELECT order_id FROM orders"


def test_new_session_id_reported_and_reused(tmp_path):
    svc, llm = make(tmp_path, [text("a"), text("b")])
    first = svc.ask("q1")
    second = svc.ask("q2", session_id=first.session_id)
    assert second.session_id == first.session_id


def test_follow_up_sees_earlier_turn(tmp_path):
    svc, llm = make(tmp_path, [text("first answer"), text("second")])
    sid = svc.ask("first question").session_id
    svc.ask("add the order id to that", session_id=sid)
    seen = [str(m.get("content")) for m in llm.calls[1]]
    assert "first question" in seen and "first answer" in seen


def test_session_persists_across_service_instances(tmp_path):
    svc1, _ = make(tmp_path, [text("first answer")])
    sid = svc1.ask("first question").session_id
    svc2, llm2 = make(tmp_path, [text("again")])
    assert isinstance(svc2.ask("follow up", session_id=sid), Finished)
    assert "first question" in [str(m.get("content")) for m in llm2.calls[0]]


def test_clarifying_question_pauses_then_reply_resumes(tmp_path):
    svc, llm = make(tmp_path, [tools_reply(ASK), text("done")])
    r = svc.ask("what is the revenue?")
    assert isinstance(r, NeedsInput) and r.question == "Which revenue?" and r.run_id
    final = svc.reply(r.run_id, "net of refunds")
    assert isinstance(final, Finished) and final.answer == "done"
    assert any("net of refunds" in str(m.get("content")) for m in llm.calls[1])


def test_write_pauses_then_decide_write_true_runs_on_session_scratch(tmp_path):
    svc, llm = make(tmp_path, [tools_reply(("execute_write", {"sql": UPDATE})),
                               text("updated"), text("nothing more")])
    r = svc.ask("mark o1 as x")
    assert isinstance(r, NeedsApproval) and r.sql == UPDATE
    final = svc.decide_write(r.run_id, True)
    assert isinstance(final, Finished) and final.write == "executed: 1 row(s)"
    assert status_in(scratch(tmp_path, r.session_id)) == ("x",)
    assert status_in(tmp_path / "src.sqlite") == ("delivered",)       # source untouched
    svc.ask("anything else?", session_id=r.session_id)
    assert status_in(scratch(tmp_path, r.session_id)) == ("x",)        # not reset by later turns


def test_decide_write_false_rejects_and_scratch_unchanged(tmp_path):
    svc, llm = make(tmp_path, [tools_reply(("execute_write", {"sql": UPDATE})), text("ok")])
    r = svc.ask("mark o1 as x")
    final = svc.decide_write(r.run_id, False)
    assert isinstance(final, Finished) and final.write == "rejected"
    assert status_in(scratch(tmp_path, r.session_id)) == ("delivered",)


def test_second_ask_while_paused_returns_failure(tmp_path):
    svc, llm = make(tmp_path, [tools_reply(ASK), text("done")])
    paused = svc.ask("q")
    busy = svc.ask("another", session_id=paused.session_id)
    assert isinstance(busy, Failure) and "reply" in busy.message
    assert isinstance(svc.reply(paused.run_id, "x"), Finished)


def test_paused_run_cancelled_after_timeout(tmp_path):
    svc, llm = make(tmp_path, [tools_reply(ASK), text("fresh turn")], wait_timeout_s=0.2)
    paused = svc.ask("q")
    time.sleep(0.8)
    late = svc.reply(paused.run_id, "too late")
    assert isinstance(late, Failure) and "expired" in late.message
    for _ in range(50):                                   # session becomes usable again
        again = svc.ask("new question", session_id=paused.session_id)
        if not isinstance(again, Failure):
            break
        time.sleep(0.1)
    assert isinstance(again, Finished)


def test_unknown_session_and_run_ids_return_failure(tmp_path):
    svc, llm = make(tmp_path, [])
    assert "Unknown session" in svc.ask("q", session_id="nope").message
    assert "Unknown run" in svc.reply("nope", "x").message
    assert "Unknown run" in svc.decide_write("nope", True).message


def test_reply_to_a_run_waiting_for_approval_is_refused(tmp_path):
    svc, llm = make(tmp_path, [tools_reply(("execute_write", {"sql": UPDATE})), text("ok")])
    r = svc.ask("mark o1")
    wrong = svc.reply(r.run_id, "yes")
    assert isinstance(wrong, Failure) and "approval" in wrong.message
    assert isinstance(svc.decide_write(r.run_id, False), Finished)


def test_model_unavailable_finishes_and_session_still_usable(tmp_path):
    svc, llm = make(tmp_path, [ModelUnavailable("down"), text("back")])
    r1 = svc.ask("q1")
    assert isinstance(r1, Finished) and r1.stop_reason == "model_unavailable"
    r2 = svc.ask("q2", session_id=r1.session_id)
    assert isinstance(r2, Finished) and r2.stop_reason == "answered"
    users = [m["content"] for m in llm.calls[1] if m.get("role") == "user"]
    assert users == ["q2"]


@pytest.mark.parametrize("sql", ["UPDATE orders SET order_status = 'z'", "SELEC nope"])
def test_write_or_unparseable_answer_sql_not_rerun_for_table(tmp_path, sql):
    svc, llm = make(tmp_path, [answer(sql)])
    r = svc.ask("q")
    assert isinstance(r, Finished) and r.columns == [] and r.rows == []
    assert status_in(tmp_path / "src.sqlite") == ("delivered",)
    assert status_in(scratch(tmp_path, r.session_id)) == ("delivered",)


def test_table_limit_is_separate_from_model_limit(tmp_path):
    sql = "SELECT order_id FROM orders"
    svc, _ = make(tmp_path, [answer(sql)], cfg_kw={"row_limit": 2}, table_row_limit=10)
    r = svc.ask("q")
    assert len(r.rows) == 3 and r.rows_truncated is False
    svc2, _ = make(tmp_path, [answer(sql)], cfg_kw={"row_limit": 2}, table_row_limit=2)
    r2 = svc2.ask("q")
    assert len(r2.rows) == 2 and r2.rows_truncated is True


def test_paused_run_can_be_claimed_only_once(tmp_path):
    """Two concurrent decide_write calls must not leave a stale decision
    queued for the run's next approval (review: Important)."""
    svc, _ = make(tmp_path, [tools_reply(("execute_write", {"sql": UPDATE})),
                             text("done")])
    r = svc.ask("update")
    assert isinstance(r, NeedsApproval)
    assert svc._take(r.run_id, "approval") is not None
    second = svc._take(r.run_id, "approval")
    assert isinstance(second, Failure)
    svc.close()


def test_tracer_flushed_after_every_turn(tmp_path):
    """The server can be killed any time; a turn's spans must be flushed."""
    closed = []

    class Flushing:
        def __init__(self):
            from analyst.tracing import Tracer
            self._t = Tracer()

        def __getattr__(self, name):
            return getattr(self._t, name)

        def close(self):
            closed.append(1)

    svc, _ = make(tmp_path, [answer("SELECT 1")], tracer_factory=lambda sid: Flushing())
    assert isinstance(svc.ask("q"), Finished)
    assert closed == [1]
