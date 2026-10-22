"""Browser-less E2E test for the Chainlit UI over socket.io.

Starts no server of its own; the app must already be running:

    chainlit run src/analyst/ui/app.py --port 8765 --headless

Then:

    uv run python scripts/ui_e2e.py [question]

Drives the real protocol (connect -> client_message -> agent loop ->
new_message / ask / task_end) and auto-answers any clarifying question
or write-approval prompt. Exits 0 if an assistant answer arrives.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timezone

import socketio

# chainlit 2.x mounts the socket.io server at /ws/socket.io
HOST = "http://localhost:8765"
SOCKET_PATH = "ws/socket.io"
APPROVE = False  # set True (or pass --approve) to auto-approve writes


async def main(question: str) -> int:
    sio = socketio.AsyncClient()
    got_answer = asyncio.Event()
    answers: list[str] = []

    @sio.on("new_message")
    async def on_new_message(data):
        author = data.get("name", "?")
        content = data.get("output") or ""
        print(f"[{author}] {content[:400]}", flush=True)
        if author == "analyst" and content and content != "Working…":
            answers.append(content)
            got_answer.set()

    @sio.on("ask")
    async def on_ask(data):
        spec = (data or {}).get("spec", {})
        kind = spec.get("type")
        print(f"  (UI ask: {kind})", flush=True)
        if kind == "text":
            return {"output": "Use the standard definition from the business "
                              "definitions doc; state your assumption."}
        if kind == "action":
            choice = APPROVE and "approve" or "reject"
            label = choice.capitalize()
            return {"name": choice, "label": label,
                    "payload": {}, "tooltip": "", "forId": "", "id": "e2e"}
        return None

    @sio.on("task_end")
    async def on_task_end(data=None):
        print("  (task_end)", flush=True)

    await sio.connect(
        HOST,
        socketio_path=SOCKET_PATH,
        auth={"sessionId": str(uuid.uuid4()), "clientType": "web_app"},
    )
    await sio.emit("connection_successful")
    print(f"asking: {question}", flush=True)
    await sio.emit(
        "client_message",
        {
            "message": {
                "id": str(uuid.uuid4()),
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "output": question,
                "type": "user_message",
            },
            "fileReferences": None,
        },
    )
    try:
        await asyncio.wait_for(got_answer.wait(), timeout=240)
    except TimeoutError:
        print("FAIL: no answer within 240s", flush=True)
        await sio.disconnect()
        return 1
    await sio.disconnect()
    print(f"OK: {len(answers)} assistant message(s)", flush=True)
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--approve"]
    APPROVE = "--approve" in sys.argv[1:]
    q = args[0] if args else "How many orders are there?"
    raise SystemExit(asyncio.run(main(q)))
