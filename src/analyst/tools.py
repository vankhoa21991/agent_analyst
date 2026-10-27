"""Tools the agent can call. Plain Python functions behind a registry.

Tool errors are returned as ToolResult.text, never raised, so the model can
read them and retry.
"""

from __future__ import annotations

from dataclasses import dataclass

from .db import Database, QueryError, QueryResult
from .sqlcheck import check_sql, explain, flag_result
from .config import AgentConfig


@dataclass
class ToolResult:
    text: str
    needs_approval: bool = False
    pending_sql: str | None = None
    ok: bool = True
    rows: int | None = None


def _cell(v) -> str:
    return "" if v is None else str(v)


def _format_result(res: QueryResult) -> str:
    lines = [" | ".join(res.columns)]
    lines.extend(" | ".join(_cell(v) for v in row) for row in res.rows)
    suffix = f"({len(res.rows)} rows, {res.seconds:.2f}s)"
    if res.truncated:
        suffix += ", TRUNCATED at row limit"
    lines.append(suffix)
    return "\n".join(lines)


_FLAG_NOTES = {
    "empty": "result is empty - check the filters and table choices",
    "very_large": (
        "result hit the row limit and was truncated - aggregate or add LIMIT"
    ),
}


def _check_arguments(schema: dict, arguments: dict) -> str | None:
    """Return a description of the first schema violation, or None."""
    for key in schema.get("required", []):
        if key not in arguments:
            return f"missing required argument '{key}'"
    for key, spec in schema.get("properties", {}).items():
        if (
            key in arguments
            and spec.get("type") == "string"
            and not isinstance(arguments[key], str)
        ):
            return f"argument '{key}' must be a string"
    return None


class ToolRegistry:
    def __init__(
        self,
        db: Database,
        cfg: AgentConfig,
        retriever: object | None = None,
        ask_user=None,
    ):
        self._db = db
        self._cfg = cfg
        self._retriever = retriever
        self.ask_user = ask_user  # wired by the Agent before a run

    # -- OpenAI tool specs ---------------------------------------------------

    def specs(self) -> list[dict]:
        specs = [
            self._spec(
                "list_tables",
                "List the tables in the database.",
                {},
            ),
            self._spec(
                "describe_table",
                "Show the columns, types and a few sample rows of one table.",
                {
                    "table": {
                        "type": "string",
                        "description": "table name from list_tables",
                    }
                },
                ["table"],
            ),
            self._spec(
                "run_sql",
                "Run a read-only SQL query (SELECT) against the database. "
                "Data-changing SQL is rejected; use execute_write instead.",
                {
                    "sql": {
                        "type": "string",
                        "description": "a single SELECT statement",
                    }
                },
                ["sql"],
            ),
            self._spec(
                "execute_write",
                "Queue a data-changing SQL statement (INSERT/UPDATE/DELETE/DDL). "
                "It runs only on a scratch copy of the database, and only "
                "after the user approves it.",
                {
                    "sql": {
                        "type": "string",
                        "description": "a single data-changing statement",
                    }
                },
                ["sql"],
            ),
        ]
        if self._retriever is not None:
            specs.append(
                self._spec(
                    "search_docs",
                    "Search the data dictionary and business definitions for "
                    "terms relevant to the question.",
                    {
                        "query": {
                            "type": "string",
                            "description": "search terms",
                        }
                    },
                    ["query"],
                )
            )
        if self._cfg.clarify:
            specs.append(
                self._spec(
                    "ask_user",
                    "Ask the user a clarifying question when the request has "
                    "more than one reasonable meaning. Ask at most once per "
                    "question, before running any SQL.",
                    {
                        "question": {
                            "type": "string",
                            "description": "the clarifying question",
                        }
                    },
                    ["question"],
                )
            )
        return specs

    @staticmethod
    def _spec(name, description, properties, required=None) -> dict:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required or [],
                },
            },
        }

    # -- dispatch -------------------------------------------------------------

    def call(self, name: str, arguments: dict) -> ToolResult:
        schemas = {
            s["function"]["name"]: s["function"]["parameters"]
            for s in self.specs()
        }
        if name in schemas:
            problem = _check_arguments(schemas[name], arguments)
            if problem is not None:
                return ToolResult(
                    f"Invalid arguments for {name}: {problem}. "
                    "Retry the tool call with valid arguments.",
                    ok=False,
                )
        try:
            if name == "list_tables":
                return self._list_tables()
            if name == "describe_table":
                return self._describe_table(arguments)
            if name == "run_sql":
                return self._run_sql(arguments)
            if name == "execute_write":
                return self._execute_write(arguments)
            if name == "search_docs":
                return self._search_docs(arguments)
            if name == "ask_user":
                return self._ask_user(arguments)
            return ToolResult(f"Unknown tool: {name}.")
        except Exception as e:  # tools never raise into the loop
            return ToolResult(f"Tool error in {name}: {e}", ok=False)

    def run_approved_write(self, sql: str) -> ToolResult:
        try:
            n = self._db.execute_write(sql)
            return ToolResult(
                f"Write executed on the scratch database: {n} row(s) changed.",
                rows=n,
            )
        except QueryError as e:
            return ToolResult(f"Write failed: {e}", ok=False)

    # -- tool bodies -----------------------------------------------------------

    def _list_tables(self) -> ToolResult:
        return ToolResult("Tables: " + ", ".join(self._db.tables()))

    def _describe_table(self, arguments: dict) -> ToolResult:
        table = arguments.get("table", "")
        try:
            info = self._db.describe(table)
        except QueryError as e:
            return ToolResult(str(e), ok=False)
        lines = [f"Table {table}:"]
        lines.extend(
            f"  {c['name']} ({c['type']})" for c in info["columns"]
        )
        if info["sample_rows"]:
            lines.append("  sample rows:")
            for row in info["sample_rows"]:
                lines.append("  " + " | ".join(_cell(v) for v in row))
        return ToolResult("\n".join(lines))

    def _run_sql(self, arguments: dict) -> ToolResult:
        sql = arguments.get("sql", "")
        check = check_sql(sql)
        if not check.ok:
            return ToolResult(f"SQL rejected: {check.error}", ok=False)
        if check.kind == "write":
            return ToolResult(
                "This SQL changes data, so it cannot run here. Use the "
                "execute_write tool instead; it asks the user for approval.",
                ok=False,
            )
        if self._cfg.sql_checks:
            err = explain(self._db, sql)
            if err is not None:
                return ToolResult(
                    f"SQL check failed before running: {err}", ok=False
                )
        try:
            res = self._db.query(sql)
        except QueryError as e:
            return ToolResult(str(e), ok=False)
        text = _format_result(res)
        if self._cfg.sql_checks:
            notes = [
                f"note: {_FLAG_NOTES[f]}" for f in flag_result(res)
            ]
            if notes:
                text += "\n[" + "; ".join(notes) + "]"
        return ToolResult(text)

    def _execute_write(self, arguments: dict) -> ToolResult:
        sql = arguments.get("sql", "")
        check = check_sql(sql)
        if not check.ok:
            return ToolResult(f"SQL rejected: {check.error}", ok=False)
        if check.kind != "write":
            return ToolResult(
                "This SQL does not change data; use run_sql for reads.",
                ok=False,
            )
        return ToolResult(
            "Write queued. It will only run if the user approves it; do not "
            "run it again.",
            needs_approval=True,
            pending_sql=sql,
        )

    def _search_docs(self, arguments: dict) -> ToolResult:
        if self._retriever is None:
            return ToolResult("search_docs is not enabled in this configuration.")
        query = arguments.get("query", "")
        chunks = self._retriever.search(query, k=5)
        if not chunks:
            return ToolResult(f"No documentation found for: {query}")
        lines = []
        for i, c in enumerate(chunks, 1):
            lines.append(f"{i}. [{c.source}] {c.text} (score {c.score:.3f})")
        return ToolResult("\n".join(lines))

    def _ask_user(self, arguments: dict) -> ToolResult:
        if self.ask_user is None:
            return ToolResult(
                "The user cannot be asked in this configuration.", ok=False
            )
        question = arguments.get("question", "")
        answer = self.ask_user(question)
        return ToolResult(f"User answer: {answer}")
