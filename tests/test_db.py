import sqlite3
import time
from pathlib import Path

import pytest

from analyst.db import Database, QueryError, QueryResult

FIXTURES = Path(__file__).parent / "fixtures" / "olist"


def make_db(tmp_path, row_limit=200, timeout_s=10.0):
    src = tmp_path / "src.sqlite"
    scratch = tmp_path / "scratch.sqlite"
    from analyst.db import load_olist

    load_olist(str(FIXTURES), str(src))
    db = Database(str(src), str(scratch), row_limit=row_limit, timeout_s=timeout_s)
    db.reset_scratch()
    return db, src, scratch


def test_query_returns_columns_and_rows(tmp_path):
    db, _, _ = make_db(tmp_path)
    res = db.query("SELECT order_id FROM orders ORDER BY order_id")
    assert isinstance(res, QueryResult)
    assert res.columns == ["order_id"]
    assert res.rows == [("o1",), ("o2",), ("o3",)]
    assert res.truncated is False
    assert res.seconds >= 0.0


def test_query_rejects_write_on_readonly(tmp_path):
    db, _, _ = make_db(tmp_path)
    with pytest.raises(QueryError):
        db.query("DELETE FROM orders")


def test_query_row_limit_truncates(tmp_path):
    src = tmp_path / "src.sqlite"
    con = sqlite3.connect(src)
    con.execute("CREATE TABLE big (n INTEGER)")
    con.executemany("INSERT INTO big VALUES (?)", [(i,) for i in range(100)])
    con.commit()
    con.close()
    db = Database(str(src), str(tmp_path / "s.sqlite"), row_limit=5, timeout_s=10)
    res = db.query("SELECT n FROM big")
    assert len(res.rows) == 5
    assert res.truncated is True


def test_query_no_truncation_under_limit(tmp_path):
    db, _, _ = make_db(tmp_path)
    res = db.query("SELECT n FROM (SELECT row_number() OVER () AS n FROM orders)")
    assert res.truncated is False
    assert len(res.rows) == 3


def test_query_timeout(tmp_path):
    db, _, _ = make_db(tmp_path, timeout_s=0.5)
    sql = (
        "WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL "
        "SELECT n+1 FROM c WHERE n < 1000000000) SELECT COUNT(*) FROM c"
    )
    t0 = time.monotonic()
    with pytest.raises(QueryError, match="timed out"):
        db.query(sql)
    assert time.monotonic() - t0 < 2.0


def test_query_syntax_error_raises_query_error(tmp_path):
    db, _, _ = make_db(tmp_path)
    with pytest.raises(QueryError):
        db.query("SELEC oops FROM orders")


def test_tables_and_describe(tmp_path):
    db, _, _ = make_db(tmp_path)
    tables = db.tables()
    assert "orders" in tables and "order_items" in tables
    info = db.describe("orders", samples=2)
    names = [c["name"] for c in info["columns"]]
    assert "order_id" in names and "order_status" in names
    assert len(info["sample_rows"]) == 2


def test_write_goes_to_scratch_only(tmp_path):
    db, src, scratch = make_db(tmp_path)
    db.execute_write("UPDATE orders SET order_status = 'fixed' WHERE order_id = 'o1'")
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    assert con.execute(
        "SELECT order_status FROM orders WHERE order_id='o1'").fetchone()[0] == "delivered"
    con.close()
    con = sqlite3.connect(f"file:{scratch}?mode=ro", uri=True)
    assert con.execute(
        "SELECT order_status FROM orders WHERE order_id='o1'").fetchone()[0] == "fixed"
    con.close()


def test_execute_write_returns_rowcount(tmp_path):
    db, _, _ = make_db(tmp_path)
    n = db.execute_write("UPDATE orders SET order_status = 'x' WHERE order_status = 'delivered'")
    assert n == 1


def test_reset_scratch_restores(tmp_path):
    db, _, scratch = make_db(tmp_path)
    db.execute_write("DELETE FROM orders WHERE order_id = 'o2'")
    con = sqlite3.connect(f"file:{scratch}?mode=ro", uri=True)
    assert con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 2
    con.close()
    db.reset_scratch()
    con = sqlite3.connect(f"file:{scratch}?mode=ro", uri=True)
    assert con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 3
    con.close()
