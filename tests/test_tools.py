from pathlib import Path

import pytest

from analyst.config import AgentConfig
from analyst.db import Database
from analyst.tools import ToolRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "olist"


def make_cfg(tmp_path, **overrides):
    from analyst.db import load_olist

    src = tmp_path / "src.sqlite"
    scratch = tmp_path / "scratch.sqlite"
    load_olist(str(FIXTURES), str(src))
    base = dict(
        model="m", base_url="u", api_key="k",
        db_path=str(src), scratch_path=str(scratch),
    )
    base.update(overrides)
    return AgentConfig(**base)


def make_registry(tmp_path, **cfg_overrides):
    cfg = make_cfg(tmp_path, **cfg_overrides)
    db = Database(cfg.db_path, cfg.scratch_path, cfg.row_limit, cfg.timeout_s)
    db.reset_scratch()
    return ToolRegistry(db, cfg), db


def test_list_tables(tmp_path):
    reg, _ = make_registry(tmp_path)
    res = reg.call("list_tables", {})
    assert "orders" in res.text
    assert "order_items" in res.text
    assert res.needs_approval is False


def test_describe_table_includes_samples(tmp_path):
    reg, _ = make_registry(tmp_path)
    res = reg.call("describe_table", {"table": "orders"})
    assert "order_id" in res.text
    assert "order_status" in res.text
    assert "o1" in res.text  # sample row content


def test_run_sql_rejects_write_with_hint(tmp_path):
    reg, _ = make_registry(tmp_path)
    res = reg.call("run_sql", {"sql": "DELETE FROM orders"})
    assert "execute_write" in res.text
    assert res.needs_approval is False


def test_run_sql_returns_rows(tmp_path):
    reg, _ = make_registry(tmp_path)
    res = reg.call("run_sql", {"sql": "SELECT COUNT(*) AS n FROM orders"})
    assert "3" in res.text


def test_run_sql_sql_error_is_text_not_exception(tmp_path):
    reg, _ = make_registry(tmp_path)
    res = reg.call("run_sql", {"sql": "SELECT * FROM no_such_table"})
    assert "no_such_table" in res.text


def test_execute_write_requires_approval_and_changes_nothing(tmp_path):
    reg, db = make_registry(tmp_path)
    res = reg.call(
        "execute_write",
        {"sql": "UPDATE orders SET order_status = 'x' WHERE order_id = 'o1'"},
    )
    assert res.needs_approval is True
    assert res.pending_sql is not None
    r = db.query("SELECT order_status FROM orders WHERE order_id = 'o1'")
    assert r.rows == [("delivered",)]  # nothing ran


def test_run_approved_write_changes_scratch(tmp_path):
    import sqlite3

    reg, db = make_registry(tmp_path)
    res = reg.run_approved_write(
        "UPDATE orders SET order_status = 'x' WHERE order_id = 'o1'"
    )
    assert "1" in res.text
    con = sqlite3.connect(f"file:{db.scratch_path}?mode=ro", uri=True)
    row = con.execute(
        "SELECT order_status FROM orders WHERE order_id='o1'").fetchone()
    con.close()
    assert row == ("x",)
    # the source database is untouched
    r = db.query("SELECT order_status FROM orders WHERE order_id = 'o1'")
    assert r.rows == [("delivered",)]


def test_sql_checks_flag_adds_empty_note(tmp_path):
    reg, _ = make_registry(tmp_path, sql_checks=True)
    res = reg.call(
        "run_sql", {"sql": "SELECT * FROM orders WHERE order_status = 'nope'"}
    )
    assert "empty" in res.text


def test_sql_checks_off_no_explain_note(tmp_path):
    reg, _ = make_registry(tmp_path, sql_checks=False)
    res = reg.call(
        "run_sql", {"sql": "SELECT * FROM orders WHERE order_status = 'nope'"}
    )
    assert "empty" not in res.text


def test_sql_checks_catches_unknown_column(tmp_path):
    reg, _ = make_registry(tmp_path, sql_checks=True)
    res = reg.call("run_sql", {"sql": "SELECT wrong_col FROM orders"})
    assert "wrong_col" in res.text


def test_unknown_tool_returns_error_text(tmp_path):
    reg, _ = make_registry(tmp_path)
    res = reg.call("fly_to_moon", {})
    assert "unknown tool" in res.text.lower()


def test_specs_expose_core_tools(tmp_path):
    reg, _ = make_registry(tmp_path)
    names = {s["function"]["name"] for s in reg.specs()}
    assert {"list_tables", "describe_table", "run_sql", "execute_write"} <= names
    assert "search_docs" not in names
    assert "ask_user" not in names


def test_specs_search_docs_only_with_retriever(tmp_path):
    reg, _ = make_registry(tmp_path)
    names = {s["function"]["name"] for s in reg.specs()}
    assert "search_docs" not in names

    class FakeRetriever:
        def search(self, query, k=5):
            return []

    reg2 = ToolRegistry(reg._db, reg._cfg, retriever=FakeRetriever())
    names2 = {s["function"]["name"] for s in reg2.specs()}
    assert "search_docs" in names2


def test_specs_ask_user_only_when_clarify(tmp_path):
    reg, _ = make_registry(tmp_path, clarify=True)
    names = {s["function"]["name"] for s in reg.specs()}
    assert "ask_user" in names

    reg2, _ = make_registry(tmp_path, clarify=False)
    names2 = {s["function"]["name"] for s in reg2.specs()}
    assert "ask_user" not in names2
