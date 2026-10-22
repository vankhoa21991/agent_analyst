# Data Analyst Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A portfolio-grade Data Analyst Agent over the Olist marketplace data, with its own agent loop, RAG schema linking, SQL checks, a clarify step, an approval gate for writes, and a results table from five compared eval runs.

**Architecture:** A thin own loop calls a local OpenAI-compatible model, executes tools against a SQLite database (read-only, with a scratch copy for approved writes), and is traced to Langfuse. RAG, SQL checks and the clarify policy are switched on one at a time by flags in `agent.yaml`, so each eval run differs from the previous one by exactly one flag.

**Tech Stack:** Python 3.12 + uv, pytest, openai SDK (pointed at llama-server), sqlite3, sqlglot, Postgres + pgvector (agent state and vectors only), sentence-transformers (embeddings, bge reranker), Langfuse, Chainlit, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-21-data-analyst-agent-design.md`

## Global Constraints

- Time: 10 h/week, 2 months, about 80 h in total. Hours per task are given below; cut the later tasks first if time runs out, never tasks 0–9.
- Python 3.12, managed with uv. Tests with pytest. One `docker compose up` starts everything.
- Analysed data is SQLite only (Olist, BIRD Mini databases). Postgres + pgvector holds only agent state and vectors.
- Model: local Qwen3.8 27B 4-bit through llama.cpp's OpenAI-compatible server; the model name, base URL and sampling settings come from `agent.yaml`.
- Tools are plain Python functions, not MCP servers. No frameworks for the loop.
- Writes run only on a scratch copy of the database, reset before each eval run, and only after approval.
- Public data only; nothing from the owner's employer.
- Every results table states model file, quantisation, server settings, date, and any benchmark subset used.
- Commit messages end with the line `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

Failure modes the spec implies but the normal tests would miss, most likely first. Each has a test in the task named.

1. The model returns a tool call with invalid JSON arguments or an unknown tool name: the loop reports the error to the model and continues, never crashes (Task 5).
2. SQL passed to `run_sql` hides a write: multiple statements, a leading comment, or `WITH ... DELETE`: rejected as a write (Tasks 2 and 3).
3. A query is very slow or returns millions of rows: it stops at the time limit or the row limit and says so (Task 2).
4. Approval is denied or no approver answers: no write runs and the agent says so (Task 5).
5. The model server is down, or the prompt exceeds the context window: a clear error and a stop reason, not a silent truncation or a hang (Tasks 0 and 5).

## File Structure

```
pyproject.toml, agent.yaml, docker-compose.yml, README.md
src/analyst/config.py      AgentConfig, load_config
src/analyst/llm.py         LLMClient, LLMReply, ToolCall
src/analyst/db.py          Database, QueryResult (read-only, limits, scratch copy)
src/analyst/sqlcheck.py    check_sql, explain, flag_result
src/analyst/tools.py       ToolRegistry, ToolResult
src/analyst/tracing.py     Tracer (Langfuse, or no-op)
src/analyst/loop.py        Agent, AgentResult
src/analyst/rag/index.py   build_index
src/analyst/rag/search.py  Retriever, Chunk
src/analyst/ui/app.py      Chainlit app
scripts/load_olist.py      Olist CSVs to SQLite
scripts/measure_speed.py   tokens/s and s/task on the local model
data/docs/                 data dictionary and business-definition markdown
evals/grading.py           results_match
evals/runner.py            run_suite, RunReport
evals/custom/questions.jsonl
evals/minidev.py, evals/birdinteract.py
evals/run.py               CLI
configs/b0.yaml ... h4.yaml
tests/                     one test file per module
```

---

### Task 0: Scaffold, config, LLM client, speed measurement (about 5 h)

**Files:**
- Create: `pyproject.toml`, `agent.yaml`, `src/analyst/config.py`, `src/analyst/llm.py`, `scripts/measure_speed.py`, `tests/test_config.py`, `tests/test_llm.py`
- Run once: `git init` in the project folder, with a `.gitignore` for `data/*.sqlite`, `.venv`, `__pycache__`, `.env`

**Interfaces:**
- Produces:
  - `AgentConfig` (dataclass): `model: str`, `base_url: str`, `api_key: str`, `max_steps: int`, `max_total_tokens: int`, `context_window: int`, `db_path: str`, `scratch_path: str`, `row_limit: int`, `timeout_s: float`, `rag: bool`, `sql_checks: bool`, `clarify: bool`.
  - `load_config(path: str) -> AgentConfig`.
  - `ToolCall(id: str, name: str, raw_arguments: str)`.
  - `LLMReply(content: str | None, tool_calls: list[ToolCall], prompt_tokens: int, completion_tokens: int, seconds: float)`.
  - `LLMClient(cfg: AgentConfig).chat(messages: list[dict], tools: list[dict] | None = None) -> LLMReply`.
  - `class ModelUnavailable(Exception)`.

- [x] **Step 1: Write failing tests.** `test_config.py::test_load_config_reads_flags` loads a temp yaml and asserts `rag is False`, `sql_checks is False`, `clarify is False` by default and `max_steps == 12`. `test_llm.py::test_chat_parses_tool_call` fakes the openai client and asserts `reply.tool_calls[0].name == "run_sql"` and token counts are copied. `test_llm.py::test_server_down_raises_model_unavailable` points `base_url` at a closed port and asserts `ModelUnavailable`.
- [x] **Step 2: Run** `uv run pytest tests/test_config.py tests/test_llm.py -v`. Expected: FAIL (modules missing).
- [x] **Step 3: Implement** the config and client with the signatures above. Use the openai SDK with a custom `base_url`; wrap connection errors in `ModelUnavailable`. `agent.yaml` defaults: `max_steps: 12`, `row_limit: 200`, `timeout_s: 10`, all three flags false.
- [x] **Step 4: Run** the tests again. Expected: PASS.
- [x] **Step 5: Write `scripts/measure_speed.py`**: sends 5 fixed prompts through `LLMClient` and prints mean tokens/s and seconds per reply. Run it against the real llama-server and record the numbers in `docs/notes/speed.md`. Use them in Task 6 to set sample sizes.
- [x] **Step 6: Commit** (`git add -A`; message `chore: scaffold project, config and LLM client`).

### Task 1: Olist database and documentation (about 5 h)

**Files:**
- Create: `scripts/load_olist.py`, `data/docs/data_dictionary.md`, `data/docs/business_definitions.md`, `tests/test_load_olist.py`

**Interfaces:**
- Produces: `load_olist(csv_dir: str, sqlite_path: str) -> dict[str, int]` (table name to row count).

- [x] **Step 1: Write failing test** `test_load_olist_creates_tables` using a 3-row fixture CSV set in `tests/fixtures/olist/`: asserts `orders` and `order_items` exist with the fixture row counts.
- [x] **Step 2: Run** `uv run pytest tests/test_load_olist.py -v`. Expected: FAIL.
- [x] **Step 3: Implement** `load_olist` with `sqlite3` and the standard library `csv`; one table per CSV, table names from file names. Download the Olist public dataset (Kaggle, CC BY-NC-SA) by hand into `data/olist/` (git-ignored).
- [x] **Step 4: Run** the test. Expected: PASS. Then run the script on the real data and check `SELECT COUNT(*) FROM orders` is about 99 441.
- [x] **Step 5: Write the docs** (by hand, these feed RAG later): `data_dictionary.md` with one section per table and one line per column; `business_definitions.md` with at least 8 definitions that make questions ambiguous (for example "revenue", "active seller", "late delivery", "repeat customer").
- [x] **Step 6: Commit.**

### Task 2: Database layer (about 5 h)

**Files:**
- Create: `src/analyst/db.py`, `tests/test_db.py`

**Interfaces:**
- Produces:
  - `QueryResult(columns: list[str], rows: list[tuple], truncated: bool, seconds: float)`.
  - `Database(path: str, scratch_path: str, row_limit: int, timeout_s: float)`.
  - `Database.query(sql: str) -> QueryResult`: opens `file:{path}?mode=ro`, enforces the row limit (`truncated=True` when hit) and the time limit via a progress handler; raises `QueryError` on SQL errors or timeout.
  - `Database.tables() -> list[str]`; `Database.describe(table: str, samples: int = 3) -> dict` with `columns` (name, type) and `sample_rows`.
  - `Database.reset_scratch() -> None` copies the source file over the scratch file.
  - `Database.execute_write(sql: str) -> int` runs on the scratch copy only and returns the row count changed.

- [x] **Step 1: Write failing tests**: `test_query_rejects_write_on_readonly` (`DELETE FROM orders` raises `QueryError`); `test_query_row_limit_truncates` (limit 5 on a 100-row table gives 5 rows, `truncated is True`); `test_query_timeout` (a recursive CTE counting to 10^9 with `timeout_s=0.5` raises `QueryError` within 2 s); `test_write_goes_to_scratch_only` (after `execute_write`, the scratch count changed and the source did not); `test_reset_scratch_restores`.
- [x] **Step 2: Run** `uv run pytest tests/test_db.py -v`. Expected: FAIL.
- [x] **Step 3: Implement** `db.py` with the signatures above.
- [x] **Step 4: Run** the tests. Expected: PASS.
- [x] **Step 5: Commit.**

### Task 3: SQL checks (about 4 h)

**Files:**
- Create: `src/analyst/sqlcheck.py`, `tests/test_sqlcheck.py`

**Interfaces:**
- Consumes: `Database`, `QueryResult` (Task 2).
- Produces:
  - `SqlCheck(ok: bool, kind: str, error: str | None)` where `kind` is `"read"` or `"write"`.
  - `check_sql(sql: str, dialect: str = "sqlite") -> SqlCheck`: parse with sqlglot; exactly one statement; `kind="write"` for INSERT/UPDATE/DELETE/DDL, including a write inside a CTE; unparseable SQL gives `ok=False` with the parser message.
  - `explain(db: Database, sql: str) -> str | None` returns the error text from `EXPLAIN QUERY PLAN` or `None`.
  - `flag_result(result: QueryResult) -> list[str]` returns `"empty"` for zero rows and `"very_large"` when `truncated`.

- [x] **Step 1: Write failing tests**: `test_two_statements_rejected`; `test_comment_prefixed_delete_is_write`; `test_with_delete_is_write`; `test_select_is_read`; `test_syntax_error_returns_message`; `test_explain_catches_unknown_table`; `test_flag_empty`; `test_flag_very_large`.
- [x] **Step 2: Run** `uv run pytest tests/test_sqlcheck.py -v`. Expected: FAIL.
- [x] **Step 3: Implement** with `sqlglot.parse` and `sqlglot.exp` node types.
- [x] **Step 4: Run** the tests. Expected: PASS.
- [x] **Step 5: Commit.**

### Task 4: Tools and tracing (about 6 h)

**Files:**
- Create: `src/analyst/tools.py`, `src/analyst/tracing.py`, `docker-compose.yml` (Langfuse + its Postgres first; the app and agent Postgres arrive in Task 11), `tests/test_tools.py`, `tests/test_tracing.py`

**Interfaces:**
- Consumes: `Database` (Task 2), `check_sql`, `explain`, `flag_result` (Task 3), `AgentConfig` (Task 0).
- Produces:
  - `ToolResult(text: str, needs_approval: bool = False, pending_sql: str | None = None)`.
  - `ToolRegistry(db: Database, cfg: AgentConfig, retriever: object | None = None)`.
  - `ToolRegistry.specs() -> list[dict]` in OpenAI tool format for `list_tables`, `describe_table`, `run_sql`, `execute_write`, plus `search_docs` only when a retriever is given and `ask_user` only when `cfg.clarify`.
  - `ToolRegistry.call(name: str, arguments: dict) -> ToolResult`. `run_sql` rejects `kind == "write"` with a message telling the model to use `execute_write`. With `cfg.sql_checks` it runs `explain` first and appends `flag_result` notes. `execute_write` returns `needs_approval=True` and `pending_sql` and runs nothing.
  - `ToolRegistry.run_approved_write(sql: str) -> ToolResult`.
  - `Tracer.span(name: str, **attrs)` as a context manager; records SQL text, row count, seconds and errors; a no-op when Langfuse keys are absent.

- [x] **Step 1: Write failing tests**: `test_list_tables`; `test_describe_table_includes_samples`; `test_run_sql_rejects_write_with_hint`; `test_execute_write_requires_approval_and_changes_nothing`; `test_run_approved_write_changes_scratch`; `test_sql_checks_flag_adds_empty_note` (flag on, query returns zero rows, text contains "empty"); `test_unknown_tool_returns_error_text`; `test_tracer_noop_without_keys`.
- [x] **Step 2: Run** `uv run pytest tests/test_tools.py tests/test_tracing.py -v`. Expected: FAIL.
- [x] **Step 3: Implement** both modules. Tool errors are returned as `ToolResult.text`, never raised, so the model can read them and retry.
- [x] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5 (PENDING: no docker in this env, verify manually): Start Langfuse** with `docker compose up -d`, create a project, put the keys in `.env`, and confirm a trace from a small script appears in the UI.
- [x] **Step 6: Commit.**

### Task 5: Agent loop (about 8 h)

**Files:**
- Create: `src/analyst/loop.py`, `tests/test_loop.py`

**Interfaces:**
- Consumes: `LLMClient`, `LLMReply`, `ToolCall`, `ModelUnavailable` (Task 0); `ToolRegistry`, `ToolResult` (Task 4); `Tracer` (Task 4).
- Produces:
  - `AgentResult(answer: str, sql: str | None, steps: int, prompt_tokens: int, completion_tokens: int, clarifications: int, stop_reason: str)`; `stop_reason` is one of `"answered"`, `"max_steps"`, `"token_budget"`, `"model_unavailable"`, `"context_overflow"`.
  - `Agent(cfg, llm, tools, tracer, ask_user: Callable[[str], str], approve: Callable[[str], bool])`.
  - `Agent.run(question: str) -> AgentResult`.

- [x] **Step 1: Write failing tests** with a scripted fake `LLMClient`: `test_answers_after_tool_call` (list_tables, then final text, gives `stop_reason == "answered"` and `steps == 2`); `test_malformed_arguments_reported_to_model` (invalid JSON, then a valid call; no exception; the error text is in the next messages); `test_unknown_tool_name_reported`; `test_max_steps_stops`; `test_token_budget_stops`; `test_write_waits_for_approval` (approver returns True: write executed on scratch); `test_write_denied_runs_nothing` (approver returns False: scratch unchanged, answer mentions it was not run); `test_ask_user_counts_clarification`; `test_model_down_returns_stop_reason`; `test_context_overflow_stops_before_calling` (messages estimated beyond `context_window` give `"context_overflow"`).
- [x] **Step 2: Run** `uv run pytest tests/test_loop.py -v`. Expected: FAIL.
- [x] **Step 3: Implement** the loop: system prompt (role, rules, "show the SQL you used"), message list, repeat model call then tool calls then results until the model replies with no tool calls. Parse `raw_arguments` with `json.loads`. Estimate context size as characters / 3. The last successful `run_sql` text becomes `AgentResult.sql`.
- [x] **Step 4: Run** the tests. Expected: PASS.
- [x] **Step 5: Manual check (Langfuse UI part pending docker)** against the real model: `uv run python -m analyst.loop "How many orders are there?"` prints an answer and the trace shows in Langfuse.
- [x] **Step 6: Commit.**

### Task 6: Eval harness, custom suite, runs B0 and H1 (about 12 h)

**Files:**
- Create: `evals/grading.py`, `evals/runner.py`, `evals/run.py`, `evals/custom/questions.jsonl`, `configs/b0.yaml`, `configs/h1.yaml`, `tests/test_grading.py`, `tests/test_runner.py`, `docs/results/`

**Interfaces:**
- Consumes: `Agent`, `AgentResult` (Task 5), `Database` (Task 2).
- Produces:
  - `results_match(expected: QueryResult, actual: QueryResult, tol: float = 1e-6, ordered: bool = False) -> bool`.
  - `RunReport(suite: str, config_name: str, model: str, date: str, per_question: list[dict], success_rate: float, pass_k: float, mean_tokens: float, mean_steps: float, sql_error_rate: float, mean_clarifications: float)`.
  - `run_suite(questions: list[dict], make_agent: Callable[[], Agent], repeats: int) -> RunReport`.
  - CLI: `uv run python -m evals.run --suite custom --config configs/h1.yaml --repeats 3 [--limit N]`, which writes JSON to `docs/results/`.
  - `questions.jsonl` line: `{"id", "question", "gold_sql", "ambiguous": bool, "write": bool}`.

- [x] **Step 1: Write failing tests**: `test_match_ignores_row_order_by_default`; `test_match_numeric_tolerance`; `test_match_different_columns_false`; `test_runner_pass_k_counts_only_questions_solved_every_repeat` (fake agent right 2 of 3 times gives `pass_k == 0`).
- [x] **Step 2: Run** `uv run pytest tests/test_grading.py tests/test_runner.py -v`. Expected: FAIL.
- [x] **Step 3: Implement** grading, runner and CLI. B0 is the same loop with no tools: the full schema (from `Database.describe`) goes in the prompt and the model answers with SQL directly. H1 uses the schema tools. Each run resets the scratch copy first.
- [x] **Step 4: Run** the tests. Expected: PASS.
- [x] **Step 5: Write the custom suite**: 50 questions to start, up to 100 if time allows; at least 10 ambiguous (using the Task 1 definitions) and 5 write questions (for example "Fix the category spelling of X"). Draft candidates with the model, then verify every `gold_sql` by hand and keep the verified ones. Add `tests/test_write_approval.py`, which runs every `write: true` question with an approver that always denies and asserts the scratch database is unchanged.
- [x] **Step 6: Run B0 and H1** on the suite with `--repeats 3`; use the speed notes from Task 0 to pick `--limit` if a run would take over 3 hours. Save the reports and record the model string, quantisation and date.
- [x] **Step 7: Commit.**

### Task 7: BIRD Mini-Dev runner and BIRD-Interact spike (about 8 h)

**Files:**
- Create: `evals/minidev.py`, `evals/birdinteract.py`, `docs/notes/birdinteract-spike.md`, `tests/test_minidev.py`

**Interfaces:**
- Consumes: `run_suite` and `RunReport` (Task 6). Both new modules turn their benchmark's tasks into the question dict format so `run_suite` is reused.
- Produces: `load_minidev(path: str, limit: int | None) -> list[dict]`; `load_birdinteract_mini(path: str, limit: int | None) -> list[dict]`; `--suite minidev|birdinteract` in `evals/run.py`.

- [x] **Step 1: Spike first (about 3 h, week 1–2 deadline).** Download BIRD-Interact Mini (SQLite) and read how its simulated user and knowledge base are called. Run one task end to end with the own loop, with the simulated user wired to `ask_user`. Write the result in `docs/notes/birdinteract-spike.md`.
- [x] **Step 2: Decide go or no-go.** If one task does not run by the end of week 2, drop BIRD-Interact as the main benchmark as the spec says: Mini-Dev plus the custom suite become the headline, and H4 is scored on the custom suite's ambiguous questions. Record the decision in the spike note.
- [x] **Step 3: Write failing test** `test_load_minidev_maps_fields` against a 2-task fixture file: asserts each dict has `id`, `question`, `gold_sql`, and the database path.
- [x] **Step 4: Run** `uv run pytest tests/test_minidev.py -v`. Expected: FAIL.
- [~] **Step 5: Implement** `minidev.py` (SQLite split only) and, if go, `birdinteract.py`.
- [ ] **Step 6: Run** the test (PASS), then run B0 and H1 on Mini-Dev (and on BIRD-Interact Mini if go) with a disclosed subset sized from the speed notes.
- [ ] **Step 7: Commit.**

### Task 8: RAG schema linking, run H2 (about 10 h)

**Files:**
- Create: `src/analyst/rag/index.py`, `src/analyst/rag/search.py`, `configs/h2.yaml`, `tests/test_rag.py`
- Modify: `docker-compose.yml` (add Postgres with pgvector for the agent), `src/analyst/tools.py` (pass the retriever, expose `search_docs`), `src/analyst/loop.py` (when `cfg.rag`, send only retrieved table descriptions, not the full schema)

**Interfaces:**
- Consumes: `Database` (Task 2), `ToolRegistry` (Task 4).
- Produces:
  - `Chunk(text: str, source: str, score: float)`.
  - `build_index(docs_dir: str, db: Database, pg_dsn: str) -> int` indexes the markdown docs plus one chunk per table (columns, types, sample values) and returns the chunk count.
  - `Retriever(pg_dsn: str)`; `Retriever.search(query: str, k: int = 5) -> list[Chunk]`: Postgres full-text search plus pgvector similarity, merged by reciprocal rank fusion, then reranked.
  - Models (the plan's call, change only if they do not run well locally): embeddings `BAAI/bge-small-en-v1.5`, reranker `BAAI/bge-reranker-base`.

- [ ] **Step 1: Write failing tests** (against the compose Postgres, marked `integration`): `test_index_counts_chunks`; `test_search_finds_refund_table` (a question about late deliveries returns the delivery-date table chunk in the top 3); `test_search_finds_business_definition` ("active seller" returns the definition chunk first); `test_keyword_only_hit_survives_fusion` (an exact column name the embedding misses still appears).
- [ ] **Step 2: Run** `uv run pytest tests/test_rag.py -m integration -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the index and retriever with the signatures above.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Wire it** behind `cfg.rag`, write `configs/h2.yaml` (H1 plus `rag: true`), and run H2 on every suite used so far with the same questions and repeats as H1. Record prompt tokens per task next to H1.
- [ ] **Step 6: Commit.**

### Task 9: SQL checks in the loop, run H3 (about 5 h)

**Files:**
- Create: `configs/h3.yaml`
- Modify: `src/analyst/loop.py` (when `cfg.sql_checks`: `check_sql` before execution, `explain` before running, `flag_result` notes, and a limit of 2 retries with the error text on failure), `tests/test_loop.py`

**Interfaces:**
- Consumes: `check_sql`, `explain`, `flag_result` (Task 3), already used by `ToolRegistry` when `cfg.sql_checks` is on (Task 4).

- [ ] **Step 1: Write failing tests**: `test_retry_with_error_text_then_success` (fake model first sends SQL with an unknown column, then fixed SQL; second call happens and succeeds); `test_retry_limit_two` (a third failure ends with the error in the answer); `test_checks_off_means_no_explain` (the flag off, `explain` is never called).
- [ ] **Step 2: Run** `uv run pytest tests/test_loop.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the retry policy in the loop.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Run H3** (`configs/h3.yaml` is H2 plus `sql_checks: true`) on all suites; record SQL error rate next to H2.
- [ ] **Step 6: Commit.**

### Task 10: Clarify policy, run H4 (about 5 h)

**Files:**
- Create: `configs/h4.yaml`
- Modify: `src/analyst/loop.py` (system prompt rule used when `cfg.clarify`: ask once before querying when the question has more than one reasonable meaning, and use the confirmed meaning afterwards), `tests/test_loop.py`

**Interfaces:**
- Consumes: `ToolRegistry` exposes `ask_user` only when `cfg.clarify` (Task 4); `Agent.ask_user` callback (Task 5).

- [ ] **Step 1: Write failing tests**: `test_clarify_off_never_offers_ask_user_tool`; `test_clarify_on_passes_answer_back_into_messages` (scripted model calls `ask_user`, callback returns "net of refunds", next model call sees that text); `test_at_most_one_clarification_per_run_by_default`.
- [ ] **Step 2: Run** `uv run pytest tests/test_loop.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the policy and prompt rule. For evals the callback is the benchmark's simulated user (BIRD-Interact) or, for the custom suite, a stored `clarification_answer` field on each ambiguous question; add that field to the ambiguous questions.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Run H4** (`configs/h4.yaml` is H3 plus `clarify: true`). Report success rate on ambiguous questions separately, and clarifications per task.
- [ ] **Step 6: Commit.**

### Task 11: Chainlit UI and Docker packaging (about 6 h)

**Files:**
- Create: `src/analyst/ui/app.py`, `Dockerfile`
- Modify: `docker-compose.yml` (services: `app`, `postgres` with pgvector, `langfuse` and its database), `tests/test_ui_smoke.py`

**Interfaces:**
- Consumes: `Agent` with `ask_user` and `approve` callbacks (Task 5).
- Produces: a Chainlit app where `ask_user` posts a question message and waits for the reply, and `approve` shows the pending SQL with Approve and Reject buttons; answers show the SQL in a collapsible block.

- [ ] **Step 1: Write failing smoke test** `test_app_module_builds_agent` (importing `analyst.ui.app` and calling its `build_agent()` returns an `Agent` using `agent.yaml`).
- [ ] **Step 2: Run** `uv run pytest tests/test_ui_smoke.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the app and the Dockerfile; add the app and agent Postgres to compose; the app reaches llama-server on the host (`host.docker.internal`).
- [ ] **Step 4: Run** the test (PASS). Then `docker compose up` from a clean checkout, ask three real questions, trigger one clarification and one approved write, and check each appears in Langfuse.
- [ ] **Step 5: Commit.**

### Task 12: README, results table, demo, final cleanup (about 6 h)

**Files:**
- Create: `README.md`, `docs/results/results.md`, `docs/adr/` (one short record each for: own loop, SQLite-only data, flag-per-feature runs)
- Modify: none in `src/`

- [ ] **Step 1: Build the results table** `docs/results/results.md` from the saved reports: rows B0 and H1–H4, columns for each suite (success rate, pass^3, tokens/task, steps/task, SQL error rate, clarifications/task). Under it: model file, quantisation, server settings, date, benchmark subsets, and the list of disputed gold answers found in error analysis.
- [ ] **Step 2: Run the error-analysis loop** once on H4 failures: tag each (wrong table, wrong join, wrong definition, wrong aggregation, should have asked, disputed gold answer), and add 5 or more of them to the custom suite.
- [ ] **Step 3: Write the README** with, in this order: one sentence on what it is, a screenshot or GIF, the results table, the architecture in a short diagram, the one-command run, what was left out and why. A recruiter must get the point without scrolling past the table.
- [ ] **Step 4: Record a 2–3 minute video**: a question, a clarification, the SQL shown, an approved write, a Langfuse trace.
- [ ] **Step 5: Verify from scratch**: clone to a temp folder, follow the README exactly, confirm `docker compose up` and one eval run work.
- [ ] **Step 6: Commit.**

---

## Schedule (about 8–9 weeks at 10 h/week)

| Weeks | Tasks | Hours |
| --- | --- | --- |
| 1 | 0, 1 and the BIRD-Interact spike (Task 7, step 1) | 13 |
| 2 | 2, 3 | 9 |
| 3 | 4, start 5 | 14 |
| 4 | finish 5, start 6 | 14 |
| 5 | finish 6 (B0, H1 runs) | 6 + run time |
| 6 | 7 (Mini-Dev, go/no-go already decided), 8 | 14 |
| 7 | finish 8 (H2), 9 | 11 |
| 8 | 10, 11 | 11 |
| 9 | 12 | 6 |

Total about 83 h plus unattended eval run time. If behind by week 6, drop the 100-question stretch of the custom suite and run Mini-Dev subsets only.

## Self-review notes

- Spec coverage: loop, clarify, approval (Tasks 5, 10); RAG (8); tools (4); SQL checks (3, 9); evals B0–H4 with custom suite, Mini-Dev, BIRD-Interact and the go/no-go rule (6, 7); Langfuse tracing from Task 4 onward; Docker, UI, README, video (11, 12). Non-goals are not built.
- Names used across tasks match their Interfaces blocks (`Database`, `ToolRegistry`, `Agent`, `run_suite`, `AgentConfig` flags `rag`, `sql_checks`, `clarify`).
- Calls I made that the spec left open: embedding and reranker models (Task 8), the 2-retry limit (Task 9), one clarification per run (Task 10), characters/3 as the context estimate (Task 5), and `stop_reason` names.
