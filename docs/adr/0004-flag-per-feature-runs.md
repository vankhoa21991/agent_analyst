# ADR 0004: Flag-per-feature eval runs

- Status: accepted (2026-10-22)
- Context: the harness adds several behaviors (RAG, SQL checks, clarify)
  on top of a bare baseline. To attribute score changes to a single
  behavior, each eval config must differ from the previous one by
  exactly one flag, and runs must be comparable (same questions, same
  repeats, same model/server).
- Decision: configs are a ladder — `b0.yaml` (baseline: single prompt
  with the full schema, no tools, no loop), `h1.yaml` (own harness:
  step loop + schema tools, feature flags off), `h2.yaml` = H1 + `rag`,
  `h3.yaml` = H2 + `sql_checks`, `h4.yaml` = H3 + `clarify`. Every run uses the same 55-question custom suite (or
  the same Mini-Dev subset) with 3 repeats, `LANGFUSE_ENABLED=0` to
  avoid export noise, and the report is written to `docs/results/` as
  JSON. The results table in `docs/results/results.md` lines the
  configs up so each row isolates one flag.
- Consequences: clean attribution and a reproducible comparison; cost
  is total run time (each config is a full pass over the suite). Local
  model output is stochastic, so per-question pass^3 and repeats are
  reported alongside the mean.
