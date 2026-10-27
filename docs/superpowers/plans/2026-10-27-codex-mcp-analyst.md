# Analyst agent as an MCP server for Codex — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the analyst agent behind an MCP server so the Codex desktop app is the chat front end, with readable Langfuse traces, conversation memory per chat, and a sortable, scrollable result table.

**Architecture:** Core agent (`loop`, `tools`, `db`, …) gains transcript-style tracing and a history-aware `Agent.run`. A transport-neutral `AnalystService` owns sessions, pausing for clarify/approve, and the result table. `adapters/mcp/` is the only code that knows MCP; it serves one tool-result page (an MCP App).

**Tech Stack:** Python 3.12, `mcp` SDK (FastMCP), Langfuse SDK 5.x, SQLite, pytest; Playwright (optional, tests only).

**Spec:** `docs/superpowers/specs/2026-10-27-codex-mcp-analyst-design.md` — read it first; this plan only records what it leaves open.

## Global Constraints

- Codex **desktop app only**; stdio MCP server started from `~/.codex/config.toml` with `cwd` = project root (so `.env` is found) and `tool_timeout_sec = 300`.
- Table row cap **10,000** (configurable), separate from the model's `row_limit` of 200.
- A paused run is cancelled after **10 minutes** without a reply.
- Sessions use `microcompact_keep: 3`; eval configs must **not** set it.
- Trace tool results truncated at **2,000 characters**.
- **One active run per session.**
- The MCP App page is **one self-contained HTML file**: no external scripts, fonts or network.
- A write runs only after a human action, only on the scratch copy; the source database is never written.
- Eval configs, scoring and results must not change.
- Tracing failures never break a run.
- `mcp>=1.30` becomes an explicit dependency.
- **Commit only when the user says so** (standing preference): every "Commit" step means "ask first".

## Review Focus

1. Langfuse down or erroring → the run still finishes (Task 1).
2. Model server down mid-session → turn ends with `model_unavailable`, session still usable next turn (Task 3).
3. Codex passes an unknown, stale or expired `session_id` / `run_id` → clear error result, never a crash (Task 3).
4. The answer SQL is a write or fails to parse → never re-run for the table (Task 3).
5. Empty result, NULL cells, or cells containing `<script>`/`&` → page renders safely (Task 5).

## File Structure

| Path | Responsibility |
| --- | --- |
| `src/analyst/tracing.py` (modify) | `Tracer` with `run` / `generation` / `tool` observations, `truncate` |
| `src/analyst/loop.py` (modify) | uses new tracer calls; `run(question, history)`; `heal_history` |
| `src/analyst/service.py` (create) | `AnalystService`, `TurnResult` types, session store, pause/resume |
| `src/analyst/adapters/mcp/result.html` (create) | the MCP App page |
| `src/analyst/adapters/mcp/server.py` (create) | `build_server`, result mapping, `main` |
| `src/analyst/adapters/mcp/__main__.py` (create) | `python -m analyst.adapters.mcp` |
| `configs/mcp.yaml` (create) | config for the Codex server |
| `pyproject.toml` (modify) | `mcp` dependency, `analyst-mcp` script |
| `README.md`, `docs/adr/0005-mcp-adapter-layers.md` (create/modify) | registration, privacy note, decision record |
| tests: `test_trace_transcript.py`, `test_loop_history.py`, `test_service.py`, `test_result_page.py`, `test_mcp_adapter.py` (create) | |

---

### Task 1: Transcript-style tracing

**Files:** Modify `src/analyst/tracing.py`, `src/analyst/loop.py`; Test `tests/test_trace_transcript.py`

**Interfaces:**
- Produces in `tracing.py`:
  - `truncate(text: str, limit: int = 2000) -> str` (appends `… [truncated N chars]`)
  - `Tracer.run(name, input=None, metadata=None)` → context manager yielding `RunObs` with `.end(output: str, stop_reason: str) -> None` and `.trace_id: str | None`
  - `Tracer.generation(step: int, model: str, input: list[dict])` → yields `GenObs` with `.end(output: dict, usage: dict, finish_reason: str | None) -> None` (`output = {"content": str|None, "tool_calls": [{"name","arguments"}]}`, `usage = {"input": int, "output": int}`)
  - `Tracer.tool(name: str, arguments: str)` → yields `ToolObs` with `.end(result: str, ok: bool) -> None`
  - Disabled tracer yields no-op objects with the same methods. Every Langfuse call is wrapped so exceptions are swallowed (logged at debug).
  - `Tracer(session_id=...)` groups runs with `langfuse.propagate_attributes(session_id=...)` around the root observation.
- Consumes: none.

- [ ] **Step 1: Write failing tests** using a recording fake for `Tracer._lf` (a class with `start_as_current_observation`, `start_observation` returning an object with `update`/`end`):
  - `test_generation_records_model_usage_and_output` — `usage_details == {"input": 10, "output": 5}`, `model == "m"`, output has the tool call name.
  - `test_tool_observation_records_arguments_and_result` and `test_tool_result_truncated_at_2000_chars` (`len(recorded) < 2100`, ends with `[truncated`).
  - `test_run_end_records_output_and_stop_reason`.
  - `test_session_id_groups_runs` (the fake records `propagate_attributes` use via monkeypatch).
  - `test_langfuse_errors_do_not_break_agent_run` — fake whose every method raises; `Agent.run` returns `answered` (Review Focus 1).
  - Loop-level, with a recording tracer: `test_run_trace_reads_like_a_transcript` — observation order `generation(step 1) → tool(run_sql) → generation(step 2)`; root `.end` output equals the final answer; generation input excludes the system message; system prompt appears once in run metadata (`metadata["system_prompt"]`).
- [ ] **Step 2: Run** `uv run pytest tests/test_trace_transcript.py -q` — expect FAIL (missing methods).
- [ ] **Step 3: Implement** the interfaces above in `tracing.py`; keep the old `span()` working for compatibility. In `loop.py` replace the `llm call` span with `generation(...)` (input = `messages[1:]` with tool contents truncated), wrap each tool execution in `tool(...)`, and make `finish()` call `run_obs.end(answer, stop_reason)`. Add `system_prompt` to the run metadata. Update the `RecordingTracer` stub in `tests/test_loop_hardening.py` (and any other fake tracer) to the new `run` / `generation` / `tool` interface, or those tests break.
- [ ] **Step 4: Run** the new file and the full fast suite: `uv run pytest -q --ignore=tests/test_write_approval.py` — expect all PASS.
- [ ] **Step 5: Verify live (manual):** run one question via `uv run python -m analyst.loop "how many orders?"` with Langfuse up; in the UI the trace shows generations with output, a `run_sql` tool span with its result, and the answer on the root.
- [ ] **Step 6: Commit** (ask first): `feat: transcript-style Langfuse traces`.

### Task 2: Conversation history in the loop

**Files:** Modify `src/analyst/loop.py`; Test `tests/test_loop_history.py`

**Interfaces:**
- Produces:
  - `Agent.run(question: str, history: list[dict] | None = None) -> AgentResult`; `history` excludes the system message and is not mutated.
  - `AgentResult.messages: list[dict]` — the conversation after this turn, without the system message, including this turn's user message (default `[]`).
  - `heal_history(messages: list[dict]) -> list[dict]` — returns a copy with a tool message `"Tool call interrupted before completing; re-issue it if still needed."` for every assistant tool call lacking one. `finish()` applies it to `messages`.
  - Microcompaction covers earlier turns: seed `sql_results` from history tool messages whose call was `run_sql` and whose content matches `_ROW_COUNT`.
- Consumes: Task 1 tracer calls (unchanged behaviour).

- [ ] **Step 1: Write failing tests:** `test_second_turn_sees_first_turn_messages`, `test_result_messages_exclude_system_and_include_user_turn`, `test_history_argument_not_mutated`, `test_heal_adds_results_for_dangling_tool_calls`, `test_retry_limit_midbatch_leaves_valid_history` (reply with two `run_sql` calls, first two failures hit the limit → `messages` has a tool message for both ids), `test_old_turn_results_elided_with_microcompact_keep` (keep=1 across two turns → turn-1 result replaced by the `elided` marker in turn 3's call).
- [ ] **Step 2: Run** `uv run pytest tests/test_loop_history.py -q` — expect FAIL.
- [ ] **Step 3: Implement** the interfaces; build the initial message list as `[system] + heal_history(history or []) + [user]`.
- [ ] **Step 4: Run** new tests + full fast suite — expect PASS (existing loop tests must be unchanged).
- [ ] **Step 5: Commit** (ask first): `feat: Agent.run accepts and returns conversation history`.

### Task 3: AnalystService

**Files:** Create `src/analyst/service.py`; Test `tests/test_service.py`

**Interfaces:**
- Consumes: `Agent.run(question, history)`, `AgentResult.messages`, `check_sql`, `Database`, `ToolRegistry`, `Retriever`.
- Produces (all dataclasses):
  - `Finished(session_id, answer, sql, columns: list[str], rows: list[list], rows_truncated: bool, stop_reason, steps, prompt_tokens, completion_tokens, trace_id: str | None, write: str | None)` — `write` is `"executed: N row(s)"`, `"rejected"` or `None`.
  - `NeedsInput(session_id, run_id, question)`, `NeedsApproval(session_id, run_id, sql)`, `Failure(message)`; `TurnResult = Finished | NeedsInput | NeedsApproval | Failure`.
  - `AnalystService(cfg, *, sessions_dir, llm=None, tracer_factory=None, retriever=None, wait_timeout_s=600, table_row_limit=10_000)` with `ask(question, session_id=None)`, `reply(run_id, text)`, `decide_write(run_id, approve)`, `close()`.
- Decisions the spec left open: history persisted as `<sessions_dir>/<session_id>.jsonl` (one message per line, atomically rewritten after each turn); each session has its own scratch DB `<session_id>.scratch.sqlite`, reset when the session is created, so a write persists for later turns; session ids are `uuid4().hex[:12]`; an id with no file on disk is a `Failure`. The table is produced by re-running `Finished.sql` read-only on a `Database` built with `table_row_limit`, only when `check_sql(sql)` is ok and `kind == "read"`.
- Pausing: each turn runs `Agent.run` in a worker thread; the `ask_user` / `approve` callbacks put `NeedsInput` / `NeedsApproval` on the run's outcome queue and block on an answer queue; on timeout call `agent.cancel()` and mark the run expired.

- [ ] **Step 1: Write failing tests** (fake LLM from `tests/test_loop_hardening.py` style, fixture DB from `tests/fixtures/olist`):
  `test_ask_returns_finished_with_table_for_answer_sql`, `test_new_session_id_reported_and_reused`, `test_follow_up_sees_earlier_turn`, `test_session_persists_across_service_instances`, `test_clarifying_question_pauses_then_reply_resumes`, `test_write_pauses_then_decide_write_true_runs_on_session_scratch` (a later turn in the same session sees the changed row), `test_decide_write_false_rejects_and_scratch_unchanged`, `test_second_ask_while_paused_returns_failure`, `test_paused_run_cancelled_after_timeout` (`wait_timeout_s=0.2`; later `reply` → `Failure` containing "expired"), `test_unknown_session_and_run_ids_return_failure` (Review Focus 3), `test_model_unavailable_finishes_and_session_still_usable` (Review Focus 2), `test_write_or_unparseable_answer_sql_not_rerun_for_table` (Review Focus 4: `columns == []`, source and scratch unchanged), `test_table_limit_is_separate_from_model_limit` (`row_limit=2`, `table_row_limit=10` → 3 rows, not truncated; `table_row_limit=2` → truncated).
- [ ] **Step 2: Run** `uv run pytest tests/test_service.py -q` — expect FAIL (module missing).
- [ ] **Step 3: Implement** `service.py` per the interfaces; keep thread hand-off in one small `_Run` class.
- [ ] **Step 4: Run** the new file and the fast suite — expect PASS.
- [ ] **Step 5: Commit** (ask first): `feat: transport-neutral AnalystService with sessions`.

### Task 4: Approval spike (manual decision gate)

**Files:** Scratchpad copy of `mcp_spike/` (not in the repo); Modify the design doc's "Write approval" section with the outcome.

**Interfaces:** Produces one decision recorded in the spec, consumed by Tasks 5–6.

- [ ] **Step 1:** Extend the spike server with `danger_model` (model-visible, `readOnlyHint` false) and `danger_app_only` (`_meta.ui.visibility = ["app"]`), each returning a timestamp; extend the page with a button that sends `{"jsonrpc":"2.0","id":N,"method":"tools/call","params":{"name":"danger_app_only","arguments":{}}}` to the host and shows the response.
- [ ] **Step 2:** In `~/.codex/config.toml` set `tools.danger_model.approval_mode = "prompt"`; restart Codex; ask Codex to call `danger_model` once with **"Approve for me" off** and once **on**. Record: dialog shown? auto-approved?
- [ ] **Step 3:** Click the page button. Record: does the call succeed, and can the model see/call `danger_app_only`?
- [ ] **Step 4: Decide and record:** if the page can call tools → adapter uses an app-only `approve_write` with an Approve button in the page; otherwise → model-visible `approve_write` with `approval_mode = "prompt"`, and the README states what "Approve for me" does to it. Write the outcome into the spec; commit (ask first).

### Task 5: Result page

**Files:** Create `src/analyst/adapters/mcp/result.html`; Test `tests/test_result_page.py` (`pytest.importorskip("playwright.sync_api")`; fake host as in the spike's `check_spike.py`)

**Interfaces:**
- Consumes: the handshake verified in Codex (`ui/initialize` → `ui/notifications/initialized`; `ui/notifications/tool-result` with `structuredContent`).
- Produces: a page for `structuredContent` shaped `{"status": "finished"|"needs_input"|"needs_approval"|"error", "answer", "sql", "columns", "rows", "rows_truncated", "question", "session_id", "run_id", "message", "write", "trace_id"}` (`write` as in `Finished.write`).

- [ ] **Step 1: Write failing tests:** `test_table_renders_all_rows_and_scrolls`, `test_click_header_sorts_text_and_numbers_both_directions`, `test_empty_result_shows_zero_rows`, `test_null_cells_render_empty` , `test_html_in_cells_is_escaped` (cell `<script>x</script>&"'` appears as text, no script element — Review Focus 5), `test_truncation_notice_shown`, `test_needs_input_state_shows_question`, `test_needs_approval_state_shows_sql` (plus Approve/Reject buttons only if Task 4 chose the app-only design), `test_standalone_status_outside_host`, `test_follows_host_theme`.
- [ ] **Step 2: Run** `uv run pytest tests/test_result_page.py -q` — expect FAIL (file missing) or SKIP if Playwright is absent (then install it in a scratch venv to run).
- [ ] **Step 3: Implement** by growing the spike's `table.html`: escape `& < > " '`, show answer text and SQL block above the table, status states above.
- [ ] **Step 4: Run** the tests — expect PASS. Save a screenshot to the scratchpad and look at it.
- [ ] **Step 5: Commit** (ask first): `feat: MCP App result page`.

### Task 6: MCP adapter server

**Files:** Create `src/analyst/adapters/__init__.py`, `adapters/mcp/__init__.py`, `adapters/mcp/server.py`, `adapters/mcp/__main__.py`, `configs/mcp.yaml`; Modify `pyproject.toml`; Test `tests/test_mcp_adapter.py`

**Interfaces:**
- Consumes: `AnalystService`, `TurnResult` types (Task 3); `result.html` (Task 5).
- Produces:
  - `build_server(service) -> FastMCP`; `to_call_tool_result(r: TurnResult) -> CallToolResult`; `main() -> None` (reads `ANALYST_MCP_CONFIG`, default `configs/mcp.yaml`; sessions in `data/sessions/`).
  - Resource `ui://analyst/result` (`text/html;profile=mcp-app`).
  - Tools: `ask_analyst(question: str, session_id: str | None = None)`, `reply(run_id: str, text: str)`, `approve_write(run_id: str, approve: bool)` — first two read-only with `_meta.ui.resourceUri`; `approve_write` not read-only, visibility per Task 4's decision.
  - Result: `content` = short text (answer, SQL, row count, first 5 rows, capped at 1,500 chars); `structuredContent` = the page shape from Task 5. Pause results say in text what the model must do next.
  - Server `instructions`: first 512 chars self-contained — pass `session_id` back on follow-ups in the same chat; on `needs_input` ask the user then call `reply`; on `needs_approval` show the SQL and call `approve_write` only after the user clearly agrees.
  - `configs/mcp.yaml`: `agent.yaml` plus `run_name: codex`, `microcompact_keep: 3`, `max_total_tokens: 60000`, `context_window: 100000`.
  - `pyproject.toml`: dependency `mcp>=1.30`; script `analyst-mcp = "analyst.adapters.mcp.server:main"`.

- [ ] **Step 1: Write failing tests** (in-memory client↔server via the SDK's `create_connected_server_and_client_session`, `build_server` given a fake service): `test_tools_declare_ui_resource_and_readonly_flags`, `test_resource_is_html_with_mcp_app_mime`, `test_finished_result_has_short_text_and_structured_rows`, `test_text_preview_bounded_for_10000_rows`, `test_needs_input_text_tells_model_to_call_reply`, `test_needs_approval_contains_exact_sql`, `test_failure_maps_to_error_status_not_exception`, `test_instructions_first_512_chars_mention_session_id`.
- [ ] **Step 2: Run** `uv run pytest tests/test_mcp_adapter.py -q` — expect FAIL.
- [ ] **Step 3: Implement** the interfaces; run service calls with `asyncio.to_thread` so the server stays responsive while a turn blocks.
- [ ] **Step 4: Run** new tests, the full fast suite, and `uv run python -c "import analyst.adapters.mcp.server"` — expect PASS / no error.
- [ ] **Step 5: Commit** (ask first): `feat: MCP adapter for Codex`.

### Task 7: Register, document, verify live, clean up

**Files:** Modify `README.md`; Create `docs/adr/0005-mcp-adapter-layers.md`; Modify `~/.codex/config.toml` (user's file; back it up first)

- [ ] **Step 1:** README section "Use from Codex" with the exact `[mcp_servers.analyst]` block (`command` = project `.venv/bin/python`, `args = ["-m", "analyst.adapters.mcp"]`, `cwd` = project root, `tool_timeout_sec = 300`, `tools.approve_write.approval_mode = "prompt"` unless Task 4 chose the app-only design) and the privacy note (Codex's cloud model sees whatever tools return; fine for the public Olist data). Short ADR 0005: core → service → adapters rule, why MCP is an adapter.
- [ ] **Step 2:** Apply the config block, remove the spike (`/usr/lib/chatgpt/resources/codex mcp remove spike-table`), start a new Codex chat.
- [ ] **Step 3: Live check with the user,** each must hold: (a) "how many orders were delivered?" returns an inline table/answer; sort and scroll work; (b) "add the order id to that table" in the same chat is understood; (c) a vague question triggers a clarifying question in chat and the answer resumes the run; (d) a write request shows the SQL and the approval step, and rejecting changes nothing; (e) the Langfuse trace shows generations, tool spans with results, and the final answer under the chat's session.
- [ ] **Step 4: Final run:** `uv run pytest -q` including the live `tests/test_write_approval.py` (about 10 minutes, needs llama-server) — expect all PASS; report any failure by name.
- [ ] **Step 5: Commit** (ask first), including the spec and this plan: `docs: Codex MCP design, plan, README`.
