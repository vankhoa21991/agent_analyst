# Custom suite evaluation: B0 vs H1

Model: `qwen3.8-27b` (llama.cpp, CPU) at `http://localhost:8080/v1`,
~37 tok/s, ~2.5 s/reply (see `speed.md`). Date: 2026-10-22.

## Setup

- Suite: `evals/custom/questions.jsonl` — 55 questions (40 unambiguous
  reads, 10 ambiguous reads, 5 writes), every `gold_sql` verified by
  hand against `data/olist.sqlite`.
- 3 repeats per question. `LANGFUSE_ENABLED=0` during evals.
- **B0**: one model call, full schema (all tables, columns, 2 sample
  rows each) + the Olist business context in the prompt, no tools. The
  model's single SQL is executed read-only and graded.
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
  query. An earlier H1 run (2026-10-22 00:48) graded the last
  exploratory query and scored 35.2% overall; analysis showed 73% of
  its read failures contained the correct value in breakdown rows.
  Both configs were re-run with the fixed code so the comparison is
  fair.

## Results (2026-10-22, 3 repeats)

| config | success | loose | pass^3 | tokens/task | steps/task | sql err | reads (strict) | amb (strict) | writes (strict) |
|---|---|---|---|---|---|---|---|---|---|
| B0 | 69.7% | 77.6% | 60.0% | 2327 | 1.00 | 9.1% | 91% | 20% | 0% |
| H1 | 72.7% | 84.2% | 67.3% | 6187 | 4.52 | 0.8% | 83% | 17% | 100% |

Loose per category — reads: B0 99% / H1 97%; amb: B0 30% / H1 27%;
writes: 0% / 100%.

## Interpretation

- **H1 wins overall** (+3.0 pp strict, +6.6 pp loose, +7.3 pp pass^3),
  driven by the write questions (100% vs 0%: the baseline has no write
  path at all) and the loose read rate.
- **On strict unambiguous reads B0 is still ahead (91% vs 83%)**: a
  single well-prompted call writes a query whose shape matches the
  gold (one number, top-5 list). The agent's queries are more
  elaborate (breakdowns, extra columns) and fail the strict shape
  match even when the number is right — hence the near-parity on the
  loose metric (99% vs 97%). The harness's value on reads is not in
  the answer but in **reliability plumbing**: SQL error rate drops
  from 9.1% to 0.8% (checks + tool feedback let the agent fix itself),
  and it can recover from schema surprises the baseline cannot see.
- **Ambiguous questions are bad for both (20%/17% strict)** because
  clarify is disabled in both configs; the model guesses an
  interpretation that rarely matches the gold's. This is exactly the
  gap the clarify step (H2) and the knowledge base (H3/H4) are meant
  to close — the next experiment.
- **Cost**: H1 uses 2.7× the tokens (6187 vs 2327 per task) and 4.5×
  the model calls. On this CPU box that is ~45 s vs ~15 s per task.
- **Safety**: `tests/test_write_approval.py` (live, deny-all approver)
  passes — denied writes never touch the scratch DB (byte-identical),
  and the source DB is read-only by construction.

## Artifacts

- `docs/results/custom_b0_2026-10-22.json`
- `docs/results/custom_h1_2026-10-22.json`
- `tests/test_write_approval.py` (live, ~2 min)
