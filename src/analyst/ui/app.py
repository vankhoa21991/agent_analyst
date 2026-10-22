"""Chainlit UI for the analyst agent.

Run from the project root:

    chainlit run src/analyst/ui/app.py

The agent runs the synchronous loop in a worker thread
(``asyncio.to_thread``); the two interactive callbacks (clarify + write
approval) hop back onto the app's event loop with
``asyncio.run_coroutine_threadsafe`` so the UI can block on user input.
"""

from __future__ import annotations

import asyncio

import chainlit as cl
from chainlit.action import Action
from chainlit.message import AskActionMessage, AskUserMessage, Message

from analyst.config import load_config
from analyst.db import Database
from analyst.llm import LLMClient
from analyst.loop import Agent
from analyst.tools import ToolRegistry
from analyst.tracing import Tracer

CONFIG_PATH = "agent.yaml"

# Captured at app startup; the callbacks run on a worker thread and must
# schedule their (async) UI calls onto this loop.
_app_loop: asyncio.AbstractEventLoop | None = None


@cl.on_app_startup
async def _capture_loop() -> None:
    global _app_loop
    _app_loop = asyncio.get_running_loop()


def _on_loop(coro):
    """Run a coroutine on the app loop from a worker thread and wait."""
    if _app_loop is None or _app_loop.is_closed():
        raise RuntimeError("app loop not ready")
    return asyncio.run_coroutine_threadsafe(coro, _app_loop).result()


async def _ask_user_async(question: str) -> str:
    msg = AskUserMessage(f"❓ {question}", timeout=300)
    res = await msg.send()
    if res is None:
        return "No answer given; use your best judgement and state your assumption."
    return res.get("output") or "No answer given; use your best judgement."


async def _approve_async(sql: str) -> bool:
    msg = AskActionMessage(
        "Pending write (runs only on the isolated scratch copy):\n\n"
        f"```sql\n{sql}\n```",
        actions=[
            Action(name="approve", payload={}, label="Approve", icon="✅",
                   tooltip="Run this write on the scratch database"),
            Action(name="reject", payload={}, label="Reject", icon="❌",
                   tooltip="Do not run this write"),
        ],
        timeout=300,
    )
    res = await msg.send()
    return bool(res) and res.get("name") == "approve"


def build_agent(config_path: str = CONFIG_PATH) -> Agent:
    """Build the agent from agent.yaml, wiring the UI callbacks."""
    cfg = load_config(config_path)
    db = Database(cfg.db_path, cfg.scratch_path, cfg.row_limit, cfg.timeout_s)
    db.reset_scratch()
    retriever = None
    if cfg.rag and cfg.rag_index:
        from analyst.rag.search import Retriever

        retriever = Retriever(cfg.rag_index)
    tools = ToolRegistry(db, cfg, retriever=retriever)
    return Agent(
        cfg,
        LLMClient(cfg),
        tools,
        Tracer(),
        ask_user=lambda q: _on_loop(_ask_user_async(q)),
        approve=lambda sql: _on_loop(_approve_async(sql)),
    )


@cl.on_message
async def on_message(message: str) -> None:
    agent = build_agent()
    await Message(content="Working…", author="analyst").send()
    res = await asyncio.to_thread(agent.run, message.content)

    content = res.answer or ""
    if res.sql:
        content += f"\n\n```sql\n{res.sql}\n```"
    if res.write_sql:
        if res.write_rows is not None:
            content += (
                f"\n\n✅ Write executed on the scratch database: "
                f"{res.write_rows} row(s) changed."
            )
        else:
            content += "\n\n⚠️ Write was not approved; nothing was executed."
    if res.stop_reason != "answered":
        content += f"\n\n_(stopped: {res.stop_reason})_"
    await Message(content=content, author="analyst").send()
