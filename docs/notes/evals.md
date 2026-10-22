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

## BIRD Mini-Dev (SQLite) — disclosed subset

22 questions (2 per database × 11 DBs: 12 simple, 9 moderate,
1 challenging), 1 repeat, 2026-10-22. BIRD's `evidence` (external
knowledge) is appended to the question, as the benchmark intends.

| config | success | tokens/task | steps/task | sql err |
|---|---|---|---|---|
| B0 | 72.7% (16/22) | 3399 | 1.00 | 0.0% |
| H1 | 86.4% (19/22) | 9168 | 4.86 | 2.3% |

On the external benchmark **H1 beats B0 by 13.7 pp** — the opposite of
the custom suite's strict-read split. BIRD questions are
multi-table and knowledge-heavy, exactly where exploration + tools
pay off.

Error analysis (all failures):

- `minidev_340`, `minidev_341` (card_games, both configs): the agent
  returned `name` where the gold returns `id` ("which cards…"). Same
  rows, different projection — **disputed gold** (name is arguably the
  better answer).
- `minidev_341` (B0 only): the gold's own filter is suspicious
  (`cardKingdomId IS NULL OR cardKingdomId IS NULL` — duplicated
  column, presumably meant `cardKingdomFoilId`). **Gold bug.**
- `minidev_197` (toxicology, both configs): the gold's
  `atom JOIN bond ON molecule_id` multiplies each atom by the
  molecule's bond count, inflating the per-molecule oxygen count;
  the agent's `SUM(element='o')` per molecule is the cleaner
  reading. **Gold quirk.**
- B0-only genuine errors: `minidev_1472` (wrong year handling),
  `minidev_1149` (wrong in/out-patient logic), `minidev_89`
  (wrong district/frequency mapping).

Net: H1's 3 failures are all projection/gold-quirk issues, not
reasoning errors; B0's 6 include 3 genuine reasoning errors.

## BIRD-Interact Mini (SQLite)

Status: **spike validated, formal scoring blocked on non-public GT.**

- The public `birdsql/mini-interact` data (300 tasks, 26 SQLite DBs)
  ships with empty `sol_sql` and `test_cases`; official GT and test
  cases are distributed by email only (`bird.bench25@gmail.com`).
- The spike (2 tasks: `households_1`, `alien_1`) ran the full loop
  end to end with the official-style LLM user simulator: the agent
  clarified the annotated ambiguities, the simulator answered from
  the annotations and refused off-annotation questions, final SQL
  executed. Details in `docs/notes/birdinteract-spike.md`.
- Per the plan's fallback rule, **Mini-Dev plus the custom suite are
  the headline benchmarks**; BIRD-Interact is reported as a
  qualitative validation of the clarify + simulated-user wiring.
  `load_birdinteract_mini` (gradable questions) is deferred until GT
  is obtained.

## H2 (H1 + RAG, TF-IDF)

Custom suite, 55 questions x 3 repeats (same questions/repeats as H1):

| | B0 | H1 | H2 |
| --- | --- | --- | --- |
| strict | 69.7% | 72.7% | **83.6%** |
| loose | 77.6% | 84.2% | **94.5%** |
| pass^3 | 60.0% | 67.3% | **76.4%** |
| tokens/task | 2327 | 6187 | 7525 |
| steps/task | 1.0 | 4.5 | 3.5 |
| SQL error rate | 9.1% | 0.8% | 0.0% |

RAG is a clear win on the custom suite: **+10.9 pp strict over H1**,
zero regressions. It fixed 5 questions H1 failed — 4 of them ambiguous
(a01 revenue, a02 active sellers, a05 AOV, a08 fulfillment time),
where the retrieved business definitions pin the interpretation H1
guessed wrong. Ambiguous subset: H1 16.7% -> H2 **50.0%** strict.
Cost: +1338 tokens/task (retrieved context in the system prompt), but
steps/task drop 4.5 -> 3.5 (less schema exploration needed).

Remaining H2 failures and their nature:

- a07 (best seller): tie at 5.0 average; gold's HAVING>=5 + implicit
  tie-break picks a specific seller — gold-quirk.
- a09 (top category): agent returns the right category (cama_mesa_banho,
  9417 distinct orders) but omits the count column — projection shape.
- a06 (cancellation rate): agent reports 0.63% (percent), gold expects
  the fraction 0.0063 — unit convention.
- a03/a10: 2/3 — stochastic, definition applied correctly when asked.

## Artifacts

- `docs/results/custom_b0_2026-10-22.json`
- `docs/results/custom_h1_2026-10-22.json`
- `docs/results/custom_h2_2026-10-22.json`
- `docs/results/minidev_b0_2026-10-22.json`
- `docs/results/minidev_h1_2026-10-22.json`
- `tests/test_write_approval.py` (live, ~2 min)
