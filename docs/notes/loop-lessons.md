# Agent loop — lessons from two reference loops, and what to improve

Compared `src/analyst/loop.py` against two loops:

- **decode** — `~/projects/building-a-coding-agent-from-scratch-course`
  (`src/decode/agent/loop.py`, `harness/runner.py`, `permissions/gate.py`;
  compaction read from docstrings only).
- **pi** — `~/projects/public/pi/packages/agent/src/agent-loop.ts` and the
  hook types in `types.ts` (`agent.ts` and the coding-agent package not read).

Status: items 1–7 of the improvement table are **implemented** (see
"Implemented" below). Items 8–9 are still proposals.

## Where our loop sits

| | analyst (ours) | decode | pi |
| --- | --- | --- | --- |
| Shape | one `while steps < max_steps` function | outer loop of "legs" around the framework's `agent.iter()` | nested `while` loops, policy supplied via hooks |
| Policy (budgets, retries, approval) | inline in the loop | in the loop + permission gate | none in the loop; all hooks |
| History across user messages | none (fresh run each message) | kept + JSONL log, `--resume` | kept by the `Agent` wrapper |
| Interruption | none | cooperative abort at boundaries, steering/follow-up queues | `AbortSignal` through model, tools, hooks |
| Context management | `chars/3` estimate, hard stop | provider-reported tokens; microcompaction + LLM summary | `transformContext` / `prepareNextTurn` hooks |
| Progress to the UI | none (final result only) | typed event stream | typed event stream, streaming deltas |

Our design is deliberate: an unattended eval run needs budgets and a retry cap
inside the loop (ADR 0001, own loop). pi has no budgets because a human can
stop it. That is a reason to keep our inline policy, not to copy pi's hooks
wholesale.

## What we learned

### From pi

1. **Truncated output means do not run the tools.** If the model stops with
   `stopReason == "length"`, any tool call in that message may have cut-off
   arguments, and a salvaging JSON parser can make them look valid. pi fails
   every call in the batch with "re-issue with complete arguments".
2. **Every tool call goes through one pipeline:** find tool → schema-validate
   args → `beforeToolCall` (can block, with a reason that goes back to the
   model) → execute → `afterToolCall` (can rewrite the result). Every failure
   becomes an error result; nothing raises into the loop.
3. **Abort is first-class.** Un-run tool calls still get an "Operation aborted"
   result so history stays valid.
4. **Tools can terminate the run** (`terminate: true`), saving a model call.
5. **The tool loadout is recorded in the transcript**, so a replay reproduces
   exactly which tools the model had.

### From decode

6. **Count context from provider usage, not a character guess.** Take the
   *last* response's input tokens as context occupancy; summing usage across
   tool rounds overcounts about N times for N rounds (still the right number
   for a *cost* budget).
7. **Two-tier compaction.** First blank old tool-output bodies in memory (no
   LLM call); only then summarize older history. Degrade, don't break, if the
   summarizer fails.
8. **A permission gate returns a reason**, and a denial reaches the model as
   `ToolDenied(reason)`. Order: deny rules → allow rules → mode × tool kind →
   ask.
9. **Heal dangling tool calls.** A crash or abort after the model asked for
   tools leaves calls with no results, and the API then rejects every later
   prompt. Synthesize "interrupted" results.
10. **A typed event stream** decouples the loop from the UI.

### What not to copy

Steering/follow-up queues, the single-flight phase machine, parallel tool
execution, per-turn model swapping, API-key refresh, sandboxing and subagents.
They solve problems of a long-running interactive coding agent. Parallel tool
calls buy nothing against SQLite.

## Gaps in our loop these point at

Verified against our code:

- `LLMClient.chat` (`llm.py`) never reads `finish_reason`. A `run_sql` or
  `execute_write` cut off by the token limit would run as-is. The dangerous
  case is a write whose `WHERE` was truncated away. (It still needs approval,
  but the user would be approving a different statement than the model meant.)
- `ToolRegistry` reads `arguments.get("sql", "")`: no argument validation, and
  a missing argument silently becomes an empty string.
- `_estimate_tokens` counts only `content`; tool-call arguments and reasoning
  text are ignored. Each `run_sql` result can be up to 200 rows and is re-sent
  on every later step.
- `approve` is `Callable[[str], bool]`; a rejection cannot carry a reason.
- A rejected write costs one more model call just to say it was rejected.
- No cancellation: the Chainlit UI runs `agent.run` in a thread that cannot
  be stopped.
- The trace does not record which tools and flags a run had, so an eval run
  is not self-describing.
- In `ui/app.py`, `write_sql` is only set after approval, so a rejected write
  shows no ✅/⚠️ line, and an approved-but-failed write shows "Write was not
  approved". Display bug, not a loop bug.

## Implemented (items 1–7)

Tests: `tests/test_loop_hardening.py` (20 tests, each written and seen failing
first) plus one in `tests/test_llm.py`.

1. `LLMReply.finish_reason`; on `length` every tool call in the reply gets an
   error result and none is executed (`loop.py`, `TRUNCATED_CALL`).
2. `ToolRegistry.call` checks required keys and string types against the tool
   schema and names the problem. A failed `run_sql` check counts as a SQL
   error for the retry limit.
3. `approve` may return a string: that rejects the write and the reason goes
   to the model. `bool` callers are unchanged. `AgentResult.write_rejected`
   added; `ui/app.py` `write_status_line()` now distinguishes executed,
   rejected and approved-but-failed.
4. `microcompact_keep` (config, default **0 = off**): only the newest N
   successful `run_sql` results stay verbatim. **Not yet evaluated** — run the
   custom 55q × 3 suite with it on before enabling it in any config.
5. The context guard uses the last reply's `prompt_tokens + completion_tokens`
   plus a `chars/3` estimate of messages added since. Falls back to the
   estimate when the server reports no usage or after microcompaction shrank
   history. `max_total_tokens` still uses the cumulative sum (cost budget).
6. `Agent.cancel()` (thread-safe, one run): checked at the top of each step
   and before each tool call; skipped calls get "Operation aborted" and the
   run ends with `stop_reason="cancelled"`. The Chainlit `@cl.on_stop` handler
   calls it. **The Stop wiring is not tested live**, and a pending
   clarify/approve prompt still has to be answered before the run can stop.
7. The run span's metadata now carries `run_name`, `model`, the tool names
   actually offered, and the `rag`/`sql_checks`/`clarify`/`microcompact_keep`
   flags (`Tracer.run(..., metadata=)`).

## Proposed improvements, in priority order

| # | Change | Where | Effort | Why |
| --- | --- | --- | --- | --- |
| 1 | Expose `finish_reason` on `LLMReply`; on `length`, return an error result for every tool call and do not execute any | `llm.py`, `loop.py` | small | Safety: never run a truncated statement. Test: fake reply with `finish_reason="length"` and a `run_sql`/`execute_write` call; assert nothing executes and the model gets the re-issue message. |
| 2 | Validate tool arguments against the declared JSON schema before dispatch; precise errors ("missing required property `sql`") | `tools.py` | small | Replaces silent empty-string defaults. |
| 3 | Make `approve` return allow-or-reason; pass the reason into the rejection message. Fix the UI write-status display while there | `loop.py`, `ui/app.py`, tests in `test_write_approval.py` | small | The model can respond to *why* a write was refused. |
| 4 | Microcompaction: after each step, replace the body of `run_sql` results older than the last N with `(result elided: N rows)` | `loop.py` | medium | Cuts re-sent tokens and delays `context_overflow`. Needs an eval run, since eliding results could hurt answers that refer back to them. |
| 5 | Use the last reply's `prompt_tokens` as context occupancy; keep the cumulative sum for `max_total_tokens` | `loop.py` | small | Accuracy; the two numbers answer different questions. |
| 6 | A `cancel` flag checked at the top of each iteration; remaining tool calls get "Operation aborted" results | `loop.py`, `ui/app.py` | small | UI stop button. |
| 7 | Record the tool list and feature flags (`rag`, `sql_checks`, `clarify`) on the trace's root span | `tracing.py`, `loop.py` | small | Self-describing eval runs. |
| 8 | Optional `on_event` callback (step, tool start/end) so the UI can show progress | `loop.py`, `ui/app.py` | medium | UI shows progress during a ~30 s run instead of "Working…". |
| 9 | Optional: carry `messages` across UI messages, with the dangling-call heal step | `loop.py`, `ui/app.py` | medium | Follow-ups like "and for last year?". Changes behavior, so decide separately. |

Order rationale: 1–3 are small, change safety or error quality, and need no
re-evaluation. 4 and 5 touch what the model sees and need an eval run
(custom 55q × 3 repeats) before and after. 6–9 are UI/observability.

## Not decided

- Whether #9 (cross-message memory) belongs in scope; it makes the "one
  question, one run" eval assumption diverge from UI behavior.
- Whether #8 should wait for streaming responses (`llm.py` is non-streaming
  today).
