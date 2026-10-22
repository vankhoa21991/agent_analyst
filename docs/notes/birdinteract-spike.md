# BIRD-Interact Mini spike

Date: 2026-10-22 · Model: qwen3.8-27b (Q4_K_XL, llama-server, ~37 tok/s)

## What is BIRD-Interact Mini

- Dataset: [`birdsql/mini-interact`](https://huggingface.co/datasets/birdsql/mini-interact)
  (CC BY-SA 4.0, BIRD Team & Google Cloud). 300 tasks, 26 SQLite databases,
  each with `*.sqlite`, `*_schema.txt` (DDL + 3 example rows per table),
  `*_kb.jsonl` (business knowledge base) and `*_column_meaning_base.json`.
- Tasks are **ambiguous BI queries** over that KB. Each record
  (`mini_interact.jsonl`) carries:
  - `amb_user_query` — the ambiguous question the user asks;
  - `user_query_ambiguity.critical_ambiguity` — annotated ambiguity terms,
    each with the gold `sql_snippet` that resolves it;
  - `knowledge_ambiguity[].deleted_knowledge` — KB entry ids that are
    **masked from the agent** (the knowledge that would resolve the
    ambiguity is removed from the KB; the agent must ask the user).
- The full BIRD-Interact (lite/full) is PostgreSQL-only; Mini is the
  SQLite variant the plan targets. No Docker needed.

## Ground truth is non-public

The public jsonl has **empty `sol_sql` and `test_cases`** for all 300
records (verified). GT + executable test cases are only sent by email to
`bird.bench25@gmail.com` (tag `[mini-interact GT&Test Cases]`). Consequences:

- The official success rate **cannot** be computed locally.
- The official user simulator's hidden info includes the reference SQL;
  without it the simulator is driven only by the per-term `sql_snippet`s.
  Those snippets carry the clarification content, so the interaction is
  still faithful (the simulator answers from the same annotations).
- Grading in the spike = does the final SQL execute + manual review of
  whether it matches the annotated snippets.

## How the official evaluation works (repo: bird-bench/BIRD-Interact,
`mini_interact/knowledge_based/mini_interact_agent`)

- Agent mode: ReAct loop with a "user patience" budget (bird-coins).
  Action space: `execute(sql)`, `get_schema()`, `get_column_meaning(t, c)`,
  `get_knowledge_definition(name)` (env); `ask(question)`, `submit(sql)`
  (user). Costs: execute 1, ask 2, submit 3, lookups 0.5–1.
- User simulator: `us_env.py` + `us_prompt.py` — an LLM prompted with the
  ambiguous query + hidden info (reference SQL + ambiguity snippets) that
  must answer in ≤2 sentences, natural language, "I don't know" if the
  answer isn't in the hidden info, never exposing the reference SQL.
- Conversational mode (`mini_interact_conv`): fixed workflow, patience =
  #ambiguities + 3, reward 1.0 first try / 0.5 after one debug round.

## What we built

- `evals/mini_interact/user_sim.py` — `MiniInteractUserSimulator`:
  official prompt template (verbatim), driven by our `LLMClient`;
  `load_task`, `load_kb(deleted_ids)` for KB masking.
  Unit-tested in `tests/test_mini_interact.py` (5 tests, fake LLM).
- `src/analyst/loop.py` — `Agent` now accepts an optional
  `system_prompt` override (benchmark prompts differ from Olist's).
- `scripts/mini_interact_spike.py` — end-to-end runner:
  loads a task, masks the KB ids in `deleted_knowledge`, builds a system
  prompt with schema + visible KB, wires the simulator to `ask_user`,
  runs the agent, extracts the final SQL from the answer (```sql block),
  checks it executes, saves `docs/results/mini_interact_<task>.json`.
  The downloaded DB is opened read-only; writes go to an isolated
  scratch copy (never the original).

## First run: households_1 (2026-10-22, run 1)

Query: "Find the typical bathroom ratio for each area. Show the area code
and the average ratio." Masked KB: id 15 ("Bathroom Ratio").

The loop worked end to end:

1. Agent explored the schema, then asked:
   *"How should 'bathroom ratio' be defined … (a) Bath_Count ÷
   residentcount … (b) Bath_Count ÷ Room_Count …?"*
   Simulator: *"I mean bathrooms per resident, so use the bathroom count
   divided by the resident count for each household."* — **matches the
   gold snippet** `AVG(... Bath_Count ... / NULLIF(residentcount, 0))`.
2. Agent asked about "area code" (locregion vs loczone).
   Simulator: *"I mean the region name, `locregion`."* — **matches the
   gold snippet** `h.locregion`.
3. Then it hit `max_total_tokens=20000` (4 steps, ~27k tokens) before
   answering. The Olist-sized budget is too small for benchmark prompts
   (schema + KB ≈ 7k prompt tokens per call).

Fixes applied: spike now uses `max_steps=20`, `max_total_tokens=80000`,
and extracts the final SQL from the answer's ```sql block instead of the
last probe query.

## Run 2 (after fixes, 2026-10-22 ~01:00)

**households_1** (repeat of run 1): clarifications again correct
(bathrooms per resident; `locregion`), but the run still did not converge:
9 steps, 83k cumulative tokens → `token_budget` at 80k. The system prompt
(schema + 44 KB entries ≈ 8k tokens) is re-sent every step, so cumulative
prompt tokens grow fast. The "final SQL" in the answer was a partial
exploration query, not the answer. The task is solvable in principle
(run 1 proved the agent finds the right interpretation), it just needs a
tighter prompt (schema only, KB via search) or a bigger budget.

**alien_1** (new task): stop=`answered`, 4 steps, 67k tokens, 1
clarification. Instructive failure mode, faithfully simulated:

- Agent asked which observatory field "atmospheric conditions" means.
  That term is **not** in the annotated ambiguities (`signal quality`,
  `good signals`), so the simulator — per the official rules — answered
  "I don't know which observatory field you mean." Correct behavior.
- The agent then guessed `weathprofile` and, crucially, **never asked
  about "good signals"** (the annotated ambiguity for usable-signal
  counting); it invented `snqi > 0`. The final SQL executes and returns
  a plausible table, but without GT we cannot confirm it matches the
  gold. This is exactly the benchmark's intended pressure: ask about the
  *annotated* terms, not your own hypotheses.

Takeaways for the H4-style run:

1. The interaction wiring is solid: the simulator answers from the
   annotations, refuses off-annotation questions, and never leaks the
   reference SQL.
2. Token cost is the real constraint: ~7–8k tokens/step × 4–9 steps.
   For a benchmark run, keep the system prompt lean (schema only, KB via
   a search tool) or accept 1 repeat only.
3. Grading without GT: "final SQL executes" + manual snippet review.

## Go / no-go (plan Task 7, Step 2)

**GO** — one task runs end to end with the simulated user wired to
`ask_user`, well inside the week-2 deadline. Caveats for the full
benchmark:

1. No public GT/test cases → official SR needs the emailed GT set, or we
   report "SQL executes + snippet-consistency" as a proxy and disclose it.
2. The simulator without reference SQL is slightly weaker than the
   official one (it can only lean on the per-term snippets).
3. Cost: each task ≈ 4–8 model calls × (schema+KB in context). A 300-task
   full run at ~37 tok/s is days, not hours — the plan's "disclosed
   subset" approach is mandatory (e.g. 10–20 tasks).

## Status (final, 2026-10-22)

- [x] Run-2 results recorded above (households_1, alien_1).
- [x] BIRD Mini-Dev loader built and run: `evals/minidev.py`, 22-task
      disclosed subset (2/DB × 11 DBs), B0 72.7% vs H1 86.4% — see
      `docs/notes/evals.md`.
- [ ] Optionally email for GT + test cases to enable official
      BIRD-Interact grading (`bird.bench25@gmail.com`).
- [ ] `evals/birdinteract.py`: map Mini tasks to the question-dict
      format for `run_suite` (only if GT obtained; else keep the spike
      script). Deferred: without GT there is nothing to grade against.

Headline benchmarks per the plan's fallback rule: **Mini-Dev + custom
suite**; BIRD-Interact Mini stands as a qualitative validation of the
clarify + simulated-user wiring.
