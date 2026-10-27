# Analyst agent as an MCP server for the Codex desktop app

Status: **draft for approval** (2026-10-27). Nothing in this document is built yet,
except the throwaway spike described under "What is already verified".

## Why

The analyst agent works, but its front end is the weak part. Three problems, in the
user's words:

1. **Langfuse shows too little.** A trace shows an `agent run` and `llm call` spans
   with only durations. You cannot see what the model said at each step, which tools it
   called, what they returned, or the final answer.
2. **The Chainlit UI cannot show a result table you can work with.** The user wants to
   scroll through *all* rows and sort by column.
3. **The agent forgets.** Each chat message starts a fresh run, so "add the order id to
   this table" is meaningless to it.

Rather than build a better UI, the user wants to use the **Codex desktop app** as the
chat front end, with this agent running behind it as an MCP server, and the code
organized so the same agent can later be offered to other agents (my guess: Hermes, Pi,
Claude Code, which are already on this machine) through other thin adapters.

## Goals

1. **Codex desktop app is the front end.** The user chats in Codex; Codex calls the
   analyst through MCP. Stdio server, started by Codex from `~/.codex/config.toml`.
2. **A results table you can scroll and sort.** Shown inline in the Codex chat by an
   MCP App page (sortable columns, sticky header, scroll through every returned row,
   follows light/dark theme).
3. **Conversation memory per chat.** The agent remembers the whole conversation
   (questions, SQL, result tables) until the user starts a new Codex chat.
4. **Readable Langfuse traces.** Opening a trace reads like a transcript of the run
   (details below).
5. **A layout where MCP is one adapter.** Core agent → transport-neutral service →
   adapters. A second adapter must be addable without touching the core or the
   service.
6. **Clarifying questions and write approval happen in the Codex chat.** (Approval has
   a safety rule; see Anti-goals.)

## Non-goals

- No further work on the Chainlit UI. (My call, below, on what happens to its code.)
- No other adapters now. The layout must allow them; none are built.
- No multi-user support, authentication, or remote hosting. Local stdio only.
- No streaming progress into Codex. A call blocks until the run finishes or pauses.
- No CSV/Excel export from the table page.
- No change to how evals run or score. Eval configs and numbers must not change.

## Anti-goals (a failure even if everything else works)

- **The Codex model must never be able to execute a write on its own.** A write runs
  only after a human action. Codex's own model sits between the user and our tools, so
  an approval that is just a chat message can be forged by that model.
- Writes still run only on the isolated scratch copy; the source database is never
  written.
- A change that shifts the custom or Mini-Dev eval results without an eval run to show
  it.
- Silent data loss in tracing: a run must not fail because Langfuse is down.

## What is already verified

A throwaway spike (scratchpad folder `mcp_spike/`, not in the repo) proved on
2026-10-27, in the user's Codex desktop app (build 26.1023.50603):

- Codex discovers an MCP server registered with `codex mcp add`.
- A tool whose `_meta.ui.resourceUri` points to a `ui://` resource with MIME type
  `text/html;profile=mcp-app` is shown **inline in the chat** as an "Opened …" panel
  with an expand control, and the page received the tool's `structuredContent` through
  the standard handshake (`ui/initialize`, `ui/notifications/initialized`,
  `ui/notifications/tool-result`).
- A 500-row table rendered and scrolled. Sorting was verified in headless Chromium
  against a fake host, not yet by eye in Codex.

## What is NOT verified (builder: test these early, they can change the design)

- **"Approve for me."** The Codex composer shows this setting. It may auto-approve
  tool confirmations. Whether it would auto-approve our write confirmation is
  unknown. See "Write approval".
- **Whether the page can call tools in Codex** (needed for the stronger approval
  design below).
- Whether `tools.<tool>.output_token_limit` also applies to `structuredContent`.
- Whether Codex sends `structuredContent` to its model as well as to the page.
- Whether Codex reliably passes `session_id` back on follow-up calls (see Memory).
- Whether Codex restarts the MCP server process between chats or app restarts.

## Constraints

- Codex desktop app only (no terminal Codex). Config: `~/.codex/config.toml`, same file
  the app and CLI share. The server must set `cwd` to the project root so `.env`
  (`ANALYST_API_KEY`, Langfuse keys) is found.
- Codex kills a tool call after `tool_timeout_sec` (default 60 s). Runs take 10–60 s
  on the local model, so the registration must raise it.
- MCP Apps run in a sandboxed iframe with a restrictive default CSP: the page must be
  one self-contained HTML file, no external scripts, fonts or network.
- **Privacy:** the analysis still runs on the local Qwen model, but whatever a tool
  returns to Codex is visible to Codex's cloud model. Acceptable here because the
  Olist data is a public Kaggle dataset; it matters for any private data later. State
  this in the README.
- Local model server at `localhost:8080` needs the API key from `.env`. Langfuse runs
  from `docker-compose.yml` (already fixed: quoted `ENCRYPTION_KEY`, 8+ character
  password, S3 region).
- The `mcp` Python SDK (1.30 is installed transitively) becomes an explicit
  dependency.

## Design

### Layers

```
src/analyst/                core, as today: loop, tools, db, rag, llm, tracing, sqlcheck, config
src/analyst/service.py      transport-neutral service: sessions, run lifecycle, results
src/analyst/adapters/mcp/   MCP server + table page; the only code that knows MCP
```

Rules: adapters import the service; the service imports the core; nothing imports
upward. The core and service know nothing about MCP, Codex or HTML. The existing eval
runner keeps using `Agent` directly.

### The service

One class with a small, boring interface (names are suggestions):

- `ask(question, session_id=None) -> TurnResult` starts a turn (creates a session when
  `session_id` is None).
- `reply(run_id, text) -> TurnResult` answers a clarifying question.
- `decide_write(run_id, approve: bool) -> TurnResult` approves or rejects a pending
  write.

`TurnResult` is one of:

- **Finished:** `session_id`, `answer`, `sql`, `columns`, `rows`, `rows_truncated`,
  `stop_reason`, `steps`, token counts, `trace_id`, write outcome if any.
- **NeedsInput:** `session_id`, `run_id`, `question`.
- **NeedsApproval:** `session_id`, `run_id`, `sql` (the exact pending statement).

How a run can pause without changing the loop: each turn runs `Agent.run` in a worker
thread. The agent's existing `ask_user` and `approve` callbacks block on a per-run
hand-off. The service returns `NeedsInput` / `NeedsApproval` to the caller and keeps
the thread parked; `reply` / `decide_write` release it and wait for the next outcome.
A run left waiting is cancelled (using `Agent.cancel()`) after a timeout.

### Conversation memory

- A session holds the full message history across turns: questions, tool calls, tool
  results, answers.
- `Agent.run` must accept prior history and return the new history, and heal tool calls
  left without results by a crashed or cancelled turn (otherwise the next prompt is
  rejected by the API).
- Old `run_sql` results in a session must be shrunk or the context fills up: use the
  existing `microcompact_keep` for sessions.
- Sessions are persisted (JSONL per session under `data/sessions/`) so a server restart
  does not lose the conversation. A new Codex chat has no `session_id` and therefore
  starts a new session; the server's MCP `instructions` tell the model to pass the
  returned `session_id` on every follow-up in the same chat.

### Result table

- The agent's answer SQL is re-run read-only by the service with a **separate, larger
  row limit** (the model still sees at most 200 rows). If that cap is hit, the page and
  text say so.
- Only read queries produce a table. A write's outcome is shown as text.

### MCP adapter

Tools (read-only ones marked so; `approve_write` marked not read-only):

- `ask_analyst(question, session_id?)` and `reply(run_id, text)`: return a result with
  `_meta.ui.resourceUri` pointing at the table page.
- `approve_write(run_id, approve)`: the only tool that can run a write; registered
  with Codex approval mode `prompt` (see below).

Result shape: `content` has a **short** text (answer, SQL, row count, first few rows)
so any host and the Codex model get something useful; `structuredContent` carries
`columns`, `rows` and the rest for the page. Pause results say plainly, in text, what
the model must do next ("ask the user: …, then call reply with run_id …").

The table page is the spike's page grown up: same handshake, same sticky-header sort
and scroll, plus the answer text, the SQL, a truncation notice, and a needs-input /
needs-approval state. It must keep working when opened outside a host (standalone
status line), as the spike does.

### Write approval

Agreed with the user: **a one-click confirmation in the Codex app**, with the SQL also
shown in the chat. Mechanism: `approve_write` is a separate tool and the Codex
registration sets `tools.approve_write.approval_mode = "prompt"`, so the app shows its
own dialog the model cannot click.

**Outcome (tested in Codex, 2026-10-27):** a model-visible `approve_write` with
`approval_mode = "prompt"` showed **no dialog**; a typed "yes" ran the write, so the
model could approve for itself. The page **can** call tools in Codex: the Approve
button in the panel called an app-only `approve_write` (`visibility: ["app"]`) and the
write ran. The adapter now uses that design.

Original open risk (superseded): if "Approve for me" auto-approves that dialog, the guarantee is gone.
Stronger alternative to test first: an **Approve button inside our page** backed by an
app-only tool (`visibility: ["app"]`, hidden from the model). Then only a real click can
call it, whatever Codex's approval setting. If Codex lets the page call tools, prefer
this; if not, keep the dialog and document the "Approve for me" caveat in the README.

### Langfuse traces (core, benefits every adapter)

A trace should read like a transcript. Per run:

- **Root `agent run`:** input = question; output = final answer; stop reason; run name,
  model, tool list, flags (already recorded); `session_id` set to the chat session so
  Langfuse groups a conversation.
- **Per model step: a `generation` observation** (not a plain span) with model name,
  token usage, finish reason, input messages, and output (text plus requested tool
  calls).
- **Per tool call: a span** named after the tool with the arguments, the result text,
  `ok`/error, and duration. This includes `search_docs` (shows retrieved chunks),
  `ask_user` (question and answer) and `execute_write` / approval outcome.
- System prompt (including any pre-injected RAG chunks) visible once per run, not
  repeated on every step.
- Long values are truncated for the trace only; the agent still sees full values.
- Tracing failures never break a run.

## Left to the builder

- Package layout inside `adapters/mcp/`, module names, console-script name.
- The exact `TurnResult` field names and the hand-off mechanism between threads.
- How to test it: service tests with a fake LLM; MCP protocol tests with the SDK client
  (the spike's check script is a template); a headless-browser test for the page
  (Playwright is installed locally but is not a project dependency, so skip when
  missing); trace tests with a recording tracer.
- README section: how to register the server in Codex, the `cwd` and timeout settings,
  and the privacy note.
- Removing the spike entry (`codex mcp remove spike-table`).

## My calls (not agreed with the user; check these)

1. **Chainlit:** leave `ui/app.py` untouched and unmaintained; if the service refactor
   breaks it, delete it rather than fix it.
2. **Table row cap:** 10,000 rows (configurable), separate from the model's 200.
3. **Run waits:** a paused run is cancelled after 10 minutes without a reply.
4. **Tool timeout:** register with `tool_timeout_sec = 300`.
5. **Sessions persist to disk** (JSONL), not only in memory.
6. **Microcompaction on for sessions** (`microcompact_keep: 3`). It is not yet
   evaluated for answer quality; evals stay unaffected because eval configs do not
   set it.
7. **Trace input size:** log full message history per generation with tool results
   truncated at 2,000 characters; switching to "only new messages" is allowed if traces
   get heavy.
8. **Implementation order:** (1) trace detail, (2) service + sessions + memory,
   (3) MCP adapter + table page, (4) cleanup and README. Each is useful alone.
9. **Page-approval test** (the app-only Approve button) comes before locking the
   approval design.
10. **One active run per session.** A new `ask_analyst` while a run in that session is
    paused returns an error text telling the model to call `reply` / `approve_write`,
    or to start a new chat.
