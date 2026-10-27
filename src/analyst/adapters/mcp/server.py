"""MCP adapter: exposes AnalystService to MCP hosts (Codex desktop app).

The only module that knows MCP. Tool results carry a short text for the
host's model and ``structuredContent`` for the result page.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from ...config import load_config
from ...service import (
    AnalystService, Failure, Finished, NeedsApproval, NeedsInput, TurnResult,
)

RESULT_URI = "ui://analyst/result"
PAGE = Path(__file__).with_name("result.html")
TEXT_LIMIT = 1500
PREVIEW_ROWS = 5

INSTRUCTIONS = (
    "Data analyst over the Olist e-commerce SQLite database. Pass the "
    "session_id from each result back on every follow-up in the same chat. "
    "If a result needs input, ask the user, then call reply with the run_id. "
    "If it needs approval, show the SQL and tell the user to click Approve "
    "or Reject in the result panel; you cannot approve writes. Each answer "
    "includes a sortable result table in the chat; do not repeat the rows."
)


def _cap(text: str) -> str:
    return text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 1] + "…"


def _preview(r: Finished) -> str:
    n = len(r.rows)
    lines = [r.answer.strip()]
    if r.sql:
        lines.append(f"SQL: {r.sql.strip()}")
    if r.columns:
        cap = " (capped; the query returns more)" if r.rows_truncated else ""
        lines.append(f"{n} rows{cap}. Columns: {', '.join(r.columns)}")
        for row in r.rows[:PREVIEW_ROWS]:
            lines.append(" | ".join("" if v is None else str(v)[:40] for v in row))
    if r.write:
        lines.append(f"Write: {r.write}")
    lines.append(f"session_id: {r.session_id} (pass it on follow-ups in this chat)")
    return _cap("\n".join(lines))


def to_call_tool_result(r: TurnResult) -> CallToolResult:
    def result(text: str, structured: dict, is_error: bool = False) -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=structured,
            isError=is_error,
        )

    if isinstance(r, Finished):
        return result(_preview(r), {
            "status": "finished", "answer": r.answer, "sql": r.sql,
            "columns": r.columns, "rows": r.rows, "rows_truncated": r.rows_truncated,
            "session_id": r.session_id, "write": r.write, "trace_id": r.trace_id,
            "stop_reason": r.stop_reason,
        })
    if isinstance(r, NeedsInput):
        text = (
            f"The analyst needs clarification. Ask the user: {r.question}\n"
            f"Then call reply with run_id={r.run_id} and the user's answer as text.\n"
            f"session_id: {r.session_id}"
        )
        return result(text, {"status": "needs_input", "question": r.question,
                             "run_id": r.run_id, "session_id": r.session_id})
    if isinstance(r, NeedsApproval):
        text = (
            "The analyst wants to run this write on the scratch copy of the "
            f"database (the source data is never changed):\n{r.sql}\n"
            "Show this SQL to the user and tell them to click Approve or Reject "
            "in the result panel. You cannot approve it yourself and have no "
            "tool for it; do not ask the user to type yes. After they click, "
            "the panel shows the outcome.\n"
            f"session_id: {r.session_id}"
        )
        return result(text, {"status": "needs_approval", "sql": r.sql,
                             "run_id": r.run_id, "session_id": r.session_id})
    return result(_cap(r.message), {"status": "error", "message": r.message}, True)


def build_server(service) -> FastMCP:
    mcp = FastMCP("analyst", instructions=INSTRUCTIONS)
    ui = {"ui": {"resourceUri": RESULT_URI}}

    @mcp.resource(RESULT_URI, name="analyst-result", mime_type="text/html;profile=mcp-app",
                  meta={"ui": {"prefersBorder": True}})
    def result_page() -> str:
        return PAGE.read_text(encoding="utf-8")

    @mcp.tool(meta=ui, annotations=ToolAnnotations(readOnlyHint=True))
    async def ask_analyst(question: str, session_id: str | None = None) -> CallToolResult:
        """Ask the data analyst a question about the Olist data. Omit session_id
        to start a new conversation; pass the returned session_id on follow-ups."""
        return to_call_tool_result(await asyncio.to_thread(service.ask, question, session_id))

    @mcp.tool(meta=ui, annotations=ToolAnnotations(readOnlyHint=True))
    async def reply(run_id: str, text: str) -> CallToolResult:
        """Give the analyst the user's answer to its clarifying question."""
        return to_call_tool_result(await asyncio.to_thread(service.reply, run_id, text))

    # App-only: hidden from the model, callable only by the result page, so
    # only a real click can run a write.
    @mcp.tool(meta={"ui": {"visibility": ["app"]}},
              annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def approve_write(run_id: str, approve: bool) -> CallToolResult:
        """Approve or reject the write the analyst proposed (runs on a scratch
        copy only). Called by the result page's Approve/Reject buttons."""
        return to_call_tool_result(await asyncio.to_thread(service.decide_write, run_id, approve))

    return mcp


def main() -> None:
    cfg = load_config(os.environ.get("ANALYST_MCP_CONFIG", "configs/mcp.yaml"))
    service = AnalystService(cfg, sessions_dir="data/sessions")
    build_server(service).run()
