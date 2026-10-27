"""Transcript-style tracing: the trace reads like a record of the run
(plan Task 1)."""

from contextlib import contextmanager

import pytest

from analyst.tracing import Tracer, truncate
from test_loop_hardening import build, text, tools_reply


@pytest.fixture(autouse=True)
def no_langfuse(monkeypatch, tmp_path):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.chdir(tmp_path)


class FakeObs:
    def __init__(self, kw):
        self.kw = kw
        self.updates = []
        self.ended = False
        self.trace_id = "trace-1"

    def update(self, **kw):
        self.updates.append(kw)

    def end(self):
        self.ended = True

    def merged(self):
        out = {}
        for u in self.updates:
            out.update(u)
        return out


class FakeLF:
    def __init__(self):
        self.observations = []

    @contextmanager
    def start_as_current_observation(self, **kw):
        obs = FakeObs(kw)
        self.observations.append(obs)
        yield obs

    def start_observation(self, **kw):
        obs = FakeObs(kw)
        self.observations.append(obs)
        return obs


class BrokenLF:
    """Every Langfuse call raises, as if the server were misbehaving."""

    def start_as_current_observation(self, **kw):
        raise RuntimeError("langfuse down")

    def start_observation(self, **kw):
        raise RuntimeError("langfuse down")


def traced(session_id=None):
    t = Tracer(session_id=session_id)
    t._lf = FakeLF()
    return t, t._lf


def test_generation_records_model_usage_and_output():
    t, lf = traced()
    with t.generation(step=1, model="m", input=[{"role": "user", "content": "hi"}]) as g:
        g.end({"content": None, "tool_calls": [{"name": "run_sql", "arguments": "{}"}]},
              {"input": 10, "output": 5}, "tool_calls")
    obs = lf.observations[0]
    assert obs.kw["as_type"] == "generation" and obs.kw["model"] == "m"
    assert obs.kw["metadata"]["step"] == 1
    merged = obs.merged()
    assert merged["usage_details"] == {"input": 10, "output": 5}
    assert merged["output"]["tool_calls"][0]["name"] == "run_sql"
    assert merged["metadata"]["finish_reason"] == "tool_calls"
    assert obs.ended


def test_tool_observation_records_arguments_and_result():
    t, lf = traced()
    with t.tool("run_sql", '{"sql": "SELECT 1"}') as o:
        o.end("n\n1\n(1 rows, 0.00s)", True)
    obs = lf.observations[0]
    assert obs.kw["as_type"] == "tool" and obs.kw["name"] == "run_sql"
    assert obs.kw["input"] == {"sql": "SELECT 1"}
    assert obs.merged()["output"].startswith("n\n1")
    assert obs.ended


def test_tool_result_truncated_at_2000_chars():
    assert truncate("short") == "short"
    cut = truncate("x" * 5000)
    assert len(cut) < 2100 and "[truncated 3000 chars]" in cut
    t, lf = traced()
    with t.tool("run_sql", "{}") as o:
        o.end("y" * 5000, True)
    assert len(lf.observations[0].merged()["output"]) < 2100


def test_run_end_records_output_and_stop_reason():
    t, lf = traced()
    with t.run("agent run", input="q", metadata={"a": 1}) as r:
        r.end("the answer", "answered")
        assert r.trace_id == "trace-1"
    merged = lf.observations[0].merged()
    assert merged["output"] == "the answer"
    assert merged["metadata"]["stop_reason"] == "answered"


def test_session_id_groups_runs(monkeypatch):
    import langfuse

    seen = []

    @contextmanager
    def fake_propagate(**kw):
        seen.append(kw)
        yield

    monkeypatch.setattr(langfuse, "propagate_attributes", fake_propagate)
    t, _ = traced(session_id="chat-1")
    with t.run("agent run", input="q"):
        pass
    assert seen == [{"session_id": "chat-1"}]


def test_langfuse_errors_do_not_break_agent_run(tmp_path):
    t = Tracer()
    t._lf = BrokenLF()
    agent, llm, db, _ = build(tmp_path, [
        tools_reply(("run_sql", {"sql": "SELECT 1 AS n"})), text("1"),
    ], tracer=t)
    res = agent.run("one")
    assert res.stop_reason == "answered" and res.answer == "1"


class Rec:
    """Tracer stub recording what the loop reports, in order."""

    def __init__(self):
        self.events = []
        self.run_meta = None
        self.run_end = None

    @contextmanager
    def run(self, name, input=None, metadata=None):
        self.run_meta = metadata
        rec = self

        class R:
            trace_id = None

            def end(self, output, stop_reason):
                rec.run_end = (output, stop_reason)

        yield R()

    @contextmanager
    def generation(self, step, model, input):
        ev = {"kind": "generation", "step": step, "model": model, "input": input}
        self.events.append(ev)

        class G:
            def end(self, output, usage, finish_reason):
                ev.update(output=output, usage=usage, finish_reason=finish_reason)

        yield G()

    @contextmanager
    def tool(self, name, arguments):
        ev = {"kind": "tool", "name": name, "arguments": arguments}
        self.events.append(ev)

        class T:
            def end(self, result, ok):
                ev.update(result=result, ok=ok)

        yield T()

    @contextmanager
    def span(self, name, **attrs):
        yield None


def test_run_trace_reads_like_a_transcript(tmp_path):
    rec = Rec()
    agent, llm, db, _ = build(tmp_path, [
        tools_reply(("run_sql", {"sql": "SELECT 1 AS n"})),
        text("The answer is 1."),
    ], tracer=rec)
    res = agent.run("one?")
    assert [e["kind"] for e in rec.events] == ["generation", "tool", "generation"]
    first, tool, second = rec.events
    assert first["step"] == 1 and second["step"] == 2 and first["model"] == "m"
    assert first["output"]["tool_calls"][0]["name"] == "run_sql"
    assert first["usage"] == {"input": 10, "output": 5}
    assert all(m["role"] != "system" for m in first["input"])     # prompt lives in run metadata
    assert any(m["role"] == "tool" for m in second["input"])
    assert tool["name"] == "run_sql" and "1 rows" in tool["result"] and tool["ok"]
    assert second["output"]["content"] == "The answer is 1."
    assert rec.run_end == ("The answer is 1.", "answered")
    assert rec.run_meta["system_prompt"].startswith("You are a data analyst")
    assert res.stop_reason == "answered"


def test_write_outcome_traced_as_approval_observation(tmp_path):
    rec = Rec()
    update = "UPDATE orders SET order_status = 'x' WHERE order_id = 'o1'"
    agent, llm, db, _ = build(
        tmp_path,
        [tools_reply(("execute_write", {"sql": update})), text("ok")],
        tracer=rec, approve=lambda sql: "not on production",
    )
    agent.run("mark o1")
    approval = [e for e in rec.events if e["kind"] == "tool" and e["name"] == "approval"]
    assert len(approval) == 1
    assert "rejected" in approval[0]["result"] and "not on production" in approval[0]["result"]


def test_agent_result_carries_trace_id(tmp_path):
    t, lf = traced()
    agent, llm, db, _ = build(tmp_path, [text("done")], tracer=t)
    assert agent.run("q").trace_id == "trace-1"
