"""Smoke test: the Chainlit app module imports and builds a real Agent."""

import textwrap
from pathlib import Path

import pytest

from analyst.loop import Agent

FIXTURES = Path(__file__).parent / "fixtures" / "olist"


def test_app_module_builds_agent(tmp_path):
    import analyst.ui.app as app

    src = tmp_path / "olist.sqlite"
    from analyst.db import load_olist

    load_olist(str(FIXTURES), str(src))
    cfg_file = tmp_path / "agent.yaml"
    cfg_file.write_text(textwrap.dedent(f"""\
        model: fake-model
        base_url: http://localhost:1/v1
        api_key: test
        db_path: {src}
        scratch_path: {tmp_path / "scratch.sqlite"}
        rag: false
        sql_checks: true
        clarify: true
    """))
    agent = app.build_agent(str(cfg_file))
    assert isinstance(agent, Agent)
    assert agent._cfg.clarify is True
    assert agent._cfg.sql_checks is True
    # the default config path is the project's agent.yaml
    assert app.CONFIG_PATH == "agent.yaml"


def test_agent_yaml_is_valid(tmp_path):
    """The real agent.yaml parses and points at the real DB + RAG index."""
    from analyst.config import load_config

    root = Path(__file__).parent.parent
    cfg = load_config(str(root / "agent.yaml"))
    assert cfg.rag is True
    assert cfg.rag_index == "data/rag_index.json"
    assert cfg.sql_checks is True
    assert cfg.clarify is True
    assert (root / cfg.db_path).is_file()
    assert (root / cfg.rag_index).is_file()
