# Custom suite evaluation: B0 vs H1

Model: `qwen3.8-27b` (llama.cpp, CPU) at `http://localhost:8080/v1`,
~37 tok/s, ~2.5 s/reply (see `speed.md`). Date: 2026-10-22.

## Setup

- Suite: `evals/custom/questions.jsonl` — 55 questions (40 unambiguous
  reads, 10 ambiguous reads, 5 writes), every `gold_sql` verified by
  hand against `data/olist.sqlite`.
- 3 repeats per question. `LANGFUSE_ENABLED=0` during evals.
- **B0**: one model call, full schema (all tables, columns, 2 sample
  rows each) in the prompt, no tools. The model's single SQL is
  executed read-only and graded.
- **H1**: the full agent — schema tools (`list_tables`,
  `describe_table`, `run_sql`), SQL checks, approval-gated
  `execute_write`, clarify disabled. `max_steps=12`,
  `max_total_tokens=20000`.

## Grading

- **Strict** (`success_rate`): the answer SQL's result must match the
  gold result row-for-row (aliases ignored, row order ignored, numeric
  tolerance 1e-6). Writes: rowcount equality on a throwaway copy.
- **Loose** (`loose_success_rate`): strict, plus single-value
  questions where the gold value appears anywhere in the agent's
  result rows (right number, different presentation shape).
- The graded SQL is the one the agent *presents as its answer* (last
  fenced sql block of the final message), not the last exploratory
  query. First H1 run (2026-10-22 00:48) graded the last exploratory
  query and scored 35.2% on reads; analysis showed 73% of those
  failures contained the correct value in the breakdown rows. Both
  configs were re-run with the fixed code for a fair comparison.

## Results (final, re-run)

_(filled in after the re-run completes)_

| config | success | loose | pass^3 | tokens/task | steps/task | sql err | reads | amb | writes |
|---|---|---|---|---|---|---|---|---|---|
| B0 | | | | | | | | | |
| H1 | | | | | | | | | |

## Interpretation

_(to be written with the final numbers)_

## Artifacts

- `docs/results/custom_b0_2026-10-22.json`
- `docs/results/custom_h1_2026-10-22.json`
- `tests/test_write_approval.py` (deny-all approver, scratch
  byte-identical — live, ~2 min)
