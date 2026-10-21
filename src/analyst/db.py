"""SQLite data layer: loading Olist CSVs (Task 1) and the query API (Task 2)."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path


def _table_name(csv_file: str) -> str:
    """olist_orders_dataset.csv -> orders; product_category_name_translation.csv
    -> product_category_name_translation."""
    stem = Path(csv_file).stem
    if stem.startswith("olist_"):
        stem = stem[len("olist_"):]
    if stem.endswith("_dataset"):
        stem = stem[: -len("_dataset")]
    return stem


def _column_types(values: list[str]) -> str:
    """Infer a SQLite column type so numeric math and comparisons behave."""
    seen_int = seen_float = False
    for v in values:
        if v == "":
            continue
        try:
            int(v)
            seen_int = True
            continue
        except ValueError:
            pass
        try:
            float(v)
            seen_float = True
            continue
        except ValueError:
            return "TEXT"
    if seen_float:
        return "REAL"
    if seen_int:
        return "INTEGER"
    return "TEXT"


def _convert(value: str, coltype: str):
    if value == "":
        return None
    if coltype == "INTEGER":
        return int(value)
    if coltype == "REAL":
        return float(value)
    return value


def load_olist(csv_dir: str, sqlite_path: str) -> dict[str, int]:
    """Load every CSV in csv_dir into sqlite_path. One table per CSV, table
    name derived from the file name. Returns {table: row_count}. Re-running
    replaces the tables."""
    csv_dir = Path(csv_dir)
    counts: dict[str, int] = {}
    con = sqlite3.connect(sqlite_path)
    try:
        for csv_file in sorted(csv_dir.glob("*.csv")):
            table = _table_name(csv_file.name)
            with open(csv_file, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.reader(f))
            if not rows:
                continue
            header, data = rows[0], rows[1:]
            coltypes = [_column_types([r[i] for r in data if i < len(r)])
                        for i in range(len(header))]
            con.execute(f'DROP TABLE IF EXISTS "{table}"')
            cols = ", ".join(f'"{c}" {t}' for c, t in zip(header, coltypes))
            con.execute(f'CREATE TABLE "{table}" ({cols})')
            placeholders = ", ".join("?" * len(header))
            con.executemany(
                f'INSERT INTO "{table}" VALUES ({placeholders})',
                [
                    tuple(_convert(r[i] if i < len(r) else "", t)
                          for i, t in enumerate(coltypes))
                    for r in data
                ],
            )
            counts[table] = len(data)
        con.commit()
    finally:
        con.close()
    return counts
