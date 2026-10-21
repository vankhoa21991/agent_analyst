"""SQLite data layer: loading Olist CSVs (Task 1) and the query API (Task 2)."""

from __future__ import annotations

import csv
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


class QueryError(Exception):
    """The SQL could not be run: syntax error, a write on the read-only
    connection, or the time limit was hit."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    seconds: float


class Database:
    """Read-only access to the source database plus an isolated scratch copy
    for approved writes. Writes never touch the source file."""

    def __init__(self, path: str, scratch_path: str, row_limit: int, timeout_s: float):
        self.path = path
        self.scratch_path = scratch_path
        self.row_limit = row_limit
        self.timeout_s = timeout_s

    # -- read-only queries -------------------------------------------------

    def _read_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)

    def query(self, sql: str) -> QueryResult:
        t0 = time.perf_counter()
        con = self._read_connection()
        deadline = t0 + self.timeout_s
        state = {"expired": False}

        def progress() -> int:
            if time.perf_counter() > deadline:
                state["expired"] = True
                return 1  # abort the query
            return 0

        try:
            con.set_progress_handler(progress, 10_000)
            cur = con.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(self.row_limit + 1)
            truncated = len(rows) > self.row_limit
            if truncated:
                rows = rows[: self.row_limit]
            return QueryResult(
                columns=columns, rows=rows, truncated=truncated,
                seconds=time.perf_counter() - t0,
            )
        except sqlite3.Error as e:
            if state["expired"]:
                raise QueryError(
                    f"query timed out after {self.timeout_s}s" ) from e
            raise QueryError(f"SQL error: {e}") from e
        finally:
            con.close()

    def tables(self) -> list[str]:
        con = self._read_connection()
        try:
            return [
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master"
                    " WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    " ORDER BY name"
                )
            ]
        finally:
            con.close()

    def describe(self, table: str, samples: int = 3) -> dict:
        con = self._read_connection()
        try:
            cols = [
                {"name": r[1], "type": r[2]}
                for r in con.execute(f'PRAGMA table_info("{table}")')
            ]
            if not cols:
                raise QueryError(f"unknown table: {table}")
            sample_rows = [
                tuple(r)
                for r in con.execute(f'SELECT * FROM "{table}" LIMIT {samples}')
            ]
            return {"columns": cols, "sample_rows": sample_rows}
        except sqlite3.Error as e:
            raise QueryError(f"SQL error: {e}") from e
        finally:
            con.close()

    # -- scratch copy for approved writes ----------------------------------

    def reset_scratch(self) -> None:
        shutil.copyfile(self.path, self.scratch_path)

    def execute_write(self, sql: str) -> int:
        """Run a data-changing statement on the scratch copy only."""
        con = sqlite3.connect(self.scratch_path)
        try:
            cur = con.execute(sql)
            con.commit()
            return cur.rowcount
        except sqlite3.Error as e:
            raise QueryError(f"SQL error: {e}") from e
        finally:
            con.close()


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
