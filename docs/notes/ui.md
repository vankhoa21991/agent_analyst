# Task 11 — Chainlit UI

## What was built

- `src/analyst/ui/app.py` — Chainlit 2.x app:
  - `build_agent()` builds the agent from `agent.yaml` (full harness:
    RAG + SQL checks + clarify) and wires the two interactive callbacks.
  - **Clarify**: `ask_user` → `AskUserMessage` (the agent blocks until the
    user types an answer; the answer goes straight back into the loop).
  - **Write approval**: `execute_write` → `AskActionMessage` with
    Approve/Reject buttons showing the exact pending SQL. Approve runs the
    write on the isolated scratch copy only; Reject reports that nothing
    was executed.
  - The synchronous agent loop runs in a worker thread
    (`asyncio.to_thread`); the UI callbacks hop onto the app's event loop
    with `asyncio.run_coroutine_threadsafe`, so the chat stays responsive
    while the agent works.
- `Dockerfile` — python:3.12-slim + uv + chainlit; `ANALYST_BASE_URL`
  overrides the LLM endpoint (the compose file points it at
  `host.docker.internal:8080` so the container reaches the host's
  llama-server).
- `docker-compose.yml` — new `app` service on port 8000 (Langfuse
  services unchanged).
- `agent.yaml` — now runs the full harness (`rag`, `sql_checks`,
  `clarify` on) with bigger budgets for interactive use
  (16 steps / 40k tokens / 48k context).
- `tests/test_ui_smoke.py` — the app module imports and `build_agent()`
  returns a working `Agent`; the real `agent.yaml` parses and its
  referenced files exist.

## Verified live (no browser needed)

`scripts/ui_e2e.py` drives the real socket.io protocol
(chainlit mounts the socket at `/ws/socket.io`):

```
chainlit run src/analyst/ui/app.py --port 8765 --headless
uv run python scripts/ui_e2e.py "What is the revenue?"
uv run python scripts/ui_e2e.py --approve "Create a table called recent_orders ..."
```

Observed on the local qwen3.8-27b:

1. Read question → agent answer with the final SQL block.
2. Ambiguous question → `(UI ask: text)` clarification prompt; the typed
   answer is passed back into the loop.
3. Write request → `(UI ask: action)` with the pending SQL and
   Approve/Reject:
   - **Reject** → write not executed (verified: table absent from
     scratch).
   - **Approve** → write committed to `data/scratch.sqlite` (the agent
     read the new rows back in the same run); source DB untouched. The
     live `test_write_approval.py` additionally verifies the scratch-hash
     change with a deny-all approver. (Note: an eval run was in progress
     during this test and its per-question scratch reset later overwrote
     the table — expected isolation behavior, not a bug.)

## Blocked / deferred

- **Docker build & compose up**: no Docker in this environment. The
  Dockerfile/compose are written but unverified; re-run when Docker is
  available (`docker compose up -d --build`).
- **In-browser UX check** (button rendering, message layout): the
  socket-level E2E covers the protocol and the agent path; visual
  polish is deferred.
- **Langfuse traces for UI runs**: set the `LANGFUSE_*` env vars (see
  `.env.example`) once the Langfuse stack is up.
