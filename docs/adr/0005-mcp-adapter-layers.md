# ADR 0005: Core, service, adapters; MCP is one adapter

- Status: accepted (2026-10-27)
- Context: the Chainlit UI could not show a scrollable, sortable table, and
  every message started a fresh run. We want the Codex desktop app as the
  front end and the option to offer the same agent to other agents later.
- Decision: three layers. `src/analyst/` core (loop, tools, db, rag, llm,
  tracing) knows nothing about transports. `src/analyst/service.py`
  (`AnalystService`) owns sessions, conversation history (JSONL), a scratch
  database per session, run pausing (clarify / write approval via a worker
  thread hand-off) and the result table. `src/analyst/adapters/mcp/` is the
  only code that knows MCP and HTML. Imports go adapter -> service -> core,
  never upward. The eval runner keeps using `Agent` directly, so eval
  numbers are unaffected (eval configs do not set `microcompact_keep`).
- Consequences: another adapter is a new folder under `adapters/` that calls
  `ask` / `reply` / `decide_write`. Chainlit (`ui/app.py`) is no longer
  maintained. Whatever a tool returns is visible to the host's model, so
  this is for non-private data. Write approval depends on the host's
  confirm dialog (see the design doc).
