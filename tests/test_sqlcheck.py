import sqlite3
from pathlib import Path

import pytest

from analyst.db import Database, QueryResult
from analyst.sqlcheck import check_sql, explain, flag_result

FIXTURES = Path(__file__).parent / "fixtures" / "olist"


def make_db(tmp_path):
    from analyst.db import load_olist

    src = tmp_path / "src.sqlite"
    load_olist(str(FIXTURES), str(src))
    return Database(str(src), str(tmp_path / "s.sqlite"), row_limit=200, timeout_s=10)


def test_select_is_read():
    r = check_sql("SELECT COUNT(*) FROM orders")
    assert r.ok is True
    assert r.kind == "read"
    assert r.error is None


def test_with_select_is_read():
    r = check_sql("WITH x AS (SELECT * FROM orders) SELECT * FROM x")
    assert r.ok is True and r.kind == "read"


def test_two_statements_rejected():
    r = check_sql("SELECT 1; DELETE FROM orders")
    assert r.ok is False
    assert r.error is not None


def test_comment_prefixed_delete_is_write():
    r = check_sql("/* clean up */ DELETE FROM orders")
    assert r.ok is True
    assert r.kind == "write"


def test_with_delete_is_write():
    r = check_sql(
        "WITH x AS (DELETE FROM orders RETURNING order_id) SELECT * FROM x"
    )
    assert r.ok is True
    assert r.kind == "write"


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO orders VALUES ('a','b','c','d','e','f','g','h')",
        "UPDATE orders SET order_status = 'x'",
        "DELETE FROM orders",
        "CREATE TABLE t (n INT)",
        "DROP TABLE t",
        "VACUUM",
    ],
)
def test_other_writes(sql):
    r = check_sql(sql)
    assert r.kind == "write", sql


def test_syntax_error_returns_message():
    r = check_sql("SELEC oops FROM orders")
    assert r.ok is False
    assert r.error is not None and len(r.error) > 0


def test_explain_catches_unknown_table(tmp_path):
    db = make_db(tmp_path)
    err = explain(db, "SELECT * FROM no_such_table")
    assert err is not None
    assert "no_such_table" in err


def test_explain_ok_returns_none(tmp_path):
    db = make_db(tmp_path)
    assert explain(db, "SELECT * FROM orders") is None


def test_flag_empty():
    r = QueryResult(columns=["n"], rows=[], truncated=False, seconds=0.1)
    assert flag_result(r) == ["empty"]


def test_flag_very_large():
    r = QueryResult(columns=["n"], rows=[(1,)], truncated=True, seconds=0.1)
    assert "very_large" in flag_result(r)
    assert "empty" not in flag_result(r)


def test_flag_clean():
    r = QueryResult(columns=["n"], rows=[(1,)], truncated=False, seconds=0.1)
    assert flag_result(r) == []
