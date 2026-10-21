"""SQL checks: classify read vs write, EXPLAIN before running, flag results."""

from __future__ import annotations

import sqlite3

import sqlglot
from sqlglot import exp

from .db import Database, QueryResult

# Node types that change data or schema. Anything containing one of these is
# a write, including a write hidden inside a CTE or subquery.
_WRITE_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,  # VACUUM, ANALYZE, ATTACH, EXPLAIN ..., anything exotic
)


class SqlCheck:
    def __init__(self, ok: bool, kind: str, error: str | None):
        self.ok = ok
        self.kind = kind  # "read" or "write"
        self.error = error

    def __repr__(self) -> str:  # pragma: no cover
        return f"SqlCheck(ok={self.ok}, kind={self.kind!r}, error={self.error!r})"


def check_sql(sql: str, dialect: str = "sqlite") -> SqlCheck:
    """Parse sql and classify it.

    ok=False when the SQL does not parse or is not exactly one statement.
    kind="write" when the statement (or anything inside it) changes data or
    schema; "read" otherwise.
    """
    try:
        statements = [s for s in sqlglot.parse(sql, dialect=dialect) if s is not None]
    except sqlglot.errors.ParseError as e:
        return SqlCheck(ok=False, kind="read", error=f"parse error: {e}")

    if len(statements) != 1:
        return SqlCheck(
            ok=False,
            kind="write",
            error=f"expected exactly one SQL statement, got {len(statements)}",
        )

    statement = statements[0]
    for node in statement.walk():
        if isinstance(node, _WRITE_NODES):
            return SqlCheck(ok=True, kind="write", error=None)
    return SqlCheck(ok=True, kind="read", error=None)


def explain(db: Database, sql: str) -> str | None:
    """Run EXPLAIN QUERY PLAN without executing. Returns the error text when
    the plan cannot be built (e.g. unknown table or column), else None."""
    con = sqlite3.connect(f"file:{db.path}?mode=ro", uri=True)
    try:
        con.execute(f"EXPLAIN QUERY PLAN {sql}")
        return None
    except sqlite3.Error as e:
        return str(e)
    finally:
        con.close()


def flag_result(result: QueryResult) -> list[str]:
    """Suspicious-result flags the model should read before answering."""
    flags: list[str] = []
    if not result.rows:
        flags.append("empty")
    if result.truncated:
        flags.append("very_large")
    return flags
