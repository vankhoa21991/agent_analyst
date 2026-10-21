import pytest

from analyst.tracing import Tracer


def test_tracer_noop_without_keys(monkeypatch, tmp_path):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here, so no keys anywhere
    t = Tracer()
    assert t.enabled is False
    with t.span("run_sql", sql="SELECT 1") as s:
        assert s is None
    with t.run("agent run", input="how many orders?"):
        with t.span("llm call"):
            pass
    t.close()  # must not raise


def test_tracer_disabled_by_env(monkeypatch, tmp_path):
    # Even with keys present, LANGFUSE_ENABLED=0 keeps the tracer a no-op.
    (tmp_path / ".env").write_text(
        "LANGFUSE_PUBLIC_KEY=pk-x\nLANGFUSE_SECRET_KEY=sk-x\n"
    )
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setenv("LANGFUSE_ENABLED", "0")
    monkeypatch.chdir(tmp_path)
    t = Tracer()
    assert t.enabled is False
    t.close()


def test_tracer_records_errors(monkeypatch, tmp_path):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    t = Tracer()
    with pytest.raises(ValueError):
        with t.span("boom"):
            raise ValueError("kaput")
