# Data Analyst Agent

A portfolio-grade data analyst agent that answers business questions
over a SQLite database: it explores the schema, retrieves definitions
from business docs, writes and checks SQL, asks a clarifying question
when the request is ambiguous, and only touches data after you approve
the exact write. Built around a local 27B model (qwen3.8-27b) with a
hand-rolled agent loop, an eval harness, and full tracing.

<!-- screenshot/GIF: one question, one clarification, the SQL, one
     approved write — added in Task 12 step 4 -->

## Results

Local model: Qwen3.8-27B (UD-Q4_K_XL, 17.5 GB) on llama.cpp
`llama-server` with MTP speculative decoding, `http://localhost:8080/v1`
(~37 tok/s; [docs/notes/speed.md](docs/notes/speed.md)). One flag per
row; same questions and repeats for every run.

| Config | Flags | Custom 55q (3×) strict | loose | pass^3 | Mini-Dev 22 (1×) | tokens/task | steps/task | SQL err |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B0 | baseline prompt, no tools | | | | | | | |
| H1 | harness + tools | | | | | | | |
| H2 | + RAG | | | | | | | |
| H3 | + SQL checks | | | | | | | |
| H4 | + clarify | | | | | | | |

(Full numbers, per-suite breakdowns, and the list of disputed gold
answers: [docs/results/results.md](docs/results/results.md).)

## Architecture

```
 question
    │
    ▼
┌─────────────────────────── analyst loop (own, ~300 lines) ───────────────────────────┐
│  budgets: max steps · total tokens · context window · 2-retry SQL limit            │
│                                                                                     │
│  tools:  list_tables · describe_table · run_sql (read-only) · search_docs (TF-IDF) │
│          execute_write (approval-gated) · ask_user (≤1 per run)                     │
│                                                                                     │
│  data:   olist.sqlite (read-only) ── scratch copy for approved writes               │
│  trace:  Langfuse (per-step spans, token counts)                                    │
└─────────────────────────────────────────────────────────────────────────────────────┘
    │
    ▼
 answer + the exact SQL that computed it
```

Details and trade-offs: [docs/adr/](docs/adr/).

## Run it

```bash
# 1. data + index (one time)
uv run python scripts/load_olist.py
uv run python scripts/build_rag_index.py

# 2. start the local model (see docs/notes/speed.md for the exact flags)
#    llama-server -m Qwen3.8-27B-UD-Q4_K_XL.gguf -ngl 99 --port 8080 ...

# 3. chat UI (clarify + approve/reject buttons)
chainlit run src/analyst/ui/app.py

# or the eval harness
uv run python -m evals.run --suite custom --config configs/h4.yaml --repeats 3
```

## What was left out, and why

- **Postgres/pgvector RAG** → in-process TF-IDF (no Docker, no
  embeddings endpoint; ADR 0002).
- **BIRD-Interact official scoring** → GT/test cases are non-public;
  the spike validated loop behavior qualitatively (Task 7).
- **Docker build verification** → no Docker in the dev environment;
  Dockerfile + compose are written and the app was verified live via
  the socket protocol instead (docs/notes/ui.md).
