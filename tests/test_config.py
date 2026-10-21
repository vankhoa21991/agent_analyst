from analyst.config import AgentConfig, load_config


def test_load_config_reads_flags(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_text("model: m\nbase_url: http://x/v1\napi_key: k\n")
    cfg = load_config(str(p))
    assert isinstance(cfg, AgentConfig)
    assert cfg.model == "m"
    assert cfg.rag is False
    assert cfg.sql_checks is False
    assert cfg.clarify is False
    assert cfg.max_steps == 12


def test_load_config_explicit_values_and_env(tmp_path, monkeypatch):
    p = tmp_path / "agent.yaml"
    p.write_text(
        "model: m\nbase_url: u\napi_key: ${TEST_KEY}\n"
        "max_steps: 5\nrag: true\nrow_limit: 7\ntimeout_s: 2.5\n"
    )
    monkeypatch.setenv("TEST_KEY", "sekrit")
    cfg = load_config(str(p))
    assert cfg.api_key == "sekrit"
    assert cfg.max_steps == 5
    assert cfg.rag is True
    assert cfg.row_limit == 7
    assert cfg.timeout_s == 2.5
