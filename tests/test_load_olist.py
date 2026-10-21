import sqlite3
from pathlib import Path

from analyst.db import load_olist

FIXTURES = Path(__file__).parent / "fixtures" / "olist"


def test_load_olist_creates_tables(tmp_path):
    db = tmp_path / "t.sqlite"
    counts = load_olist(str(FIXTURES), str(db))
    con = sqlite3.connect(db)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "orders" in tables
    assert "order_items" in tables
    assert "customers" in tables
    assert counts["orders"] == 3
    assert counts["order_items"] == 3
    assert counts["customers"] == 3
    con.close()


def test_load_olist_infers_numeric_types(tmp_path):
    db = tmp_path / "t.sqlite"
    load_olist(str(FIXTURES), str(db))
    con = sqlite3.connect(db)
    # price must be numeric so SUM/AVG and comparisons behave
    price = con.execute(
        "SELECT SUM(price) FROM order_items").fetchone()[0]
    assert abs(price - 35.5) < 1e-9
    # empty strings become NULL
    nulls = con.execute(
        "SELECT COUNT(*) FROM orders WHERE order_delivered_customer_date IS NULL"
    ).fetchone()[0]
    assert nulls == 2
    con.close()


def test_load_olist_is_idempotent(tmp_path):
    db = tmp_path / "t.sqlite"
    load_olist(str(FIXTURES), str(db))
    counts = load_olist(str(FIXTURES), str(db))
    assert counts["orders"] == 3  # not 6
