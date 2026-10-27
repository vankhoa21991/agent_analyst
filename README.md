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
| B0 | baseline prompt, no tools | 69.7% | 77.6% | 60.0% | 72.7% | 2327 | 1.0 | 9.1% |
| H1 | harness + tools | 72.7% | 84.2% | 67.3% | 86.4% | 6187 | 4.5 | 0.8% |
| H2 | + RAG | **83.6%** | 94.5% | 76.4% | 77.3%* | 7525 | 3.5 | 0.0% |
| H3 | + SQL checks | 81.2% | **95.2%** | **78.2%** | 77.3%* | 7357 | 3.4 | 0.5% |
| H4 | + clarify | 79.4% | 92.7% | 72.7% | 77.3%* | 8264 | 3.5 | 1.0% |

\* RAG is built for the Olist DB, so on Mini-Dev (per-question DBs) it is
off by design — those rows are H1 re-runs; the 86.4% vs 77.3% spread is
single-repeat variance. H3/H4 vs H2 on the custom suite is also inside
run-to-run variance of the local model; the features are safety nets,
not score drivers (details in [docs/results/results.md](docs/results/results.md)).

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

## Use from Codex (MCP)

The analyst also runs as an MCP server, so the Codex desktop app can be the
chat front end. Answers come back with an inline result table (scroll, click
a header to sort). The server remembers the whole conversation until you
start a new Codex chat, asks clarifying questions in the chat, and runs a
write only after you click Approve (and only on the scratch copy).

Register it in `~/.codex/config.toml` (replace the paths):

```toml
[mcp_servers.analyst]
command = "/path/to/agent_analyst/.venv/bin/python"
args = ["-m", "analyst.adapters.mcp"]
cwd = "/path/to/agent_analyst"       # so .env (model + Langfuse keys) is found
tool_timeout_sec = 300                # a run takes 10-60 s on the local model
```

Config for the server is `configs/mcp.yaml` (override with `ANALYST_MCP_CONFIG`).
Sessions are stored in `data/sessions/`. Langfuse traces group by chat session.

Caveats:

- **Privacy:** the analysis runs on the local model, but whatever a tool
  returns is visible to Codex's cloud model. That is fine for the public Olist
  data; do not point this at private data.
- **Write approval:** Codex showed no confirm dialog for a model-visible
  approval tool, so a typed "yes" was the only gate and the model could type
  it. `approve_write` is therefore app-only (hidden from the model); the
  result panel's **Approve / Reject** buttons are the only way to run a write.
  If the host does not let the page call tools, writes simply cannot be
  approved. Not yet confirmed in Codex; see the design doc.

## What was left out, and why

- **Postgres/pgvector RAG** → in-process TF-IDF (no Docker, no
  embeddings endpoint; ADR 0002).
- **BIRD-Interact official scoring** → GT/test cases are non-public;
  the spike validated loop behavior qualitatively (Task 7).
- **Docker build verification** → no Docker in the dev environment;
  Dockerfile + compose are written and the app was verified live via
  the socket protocol instead (docs/notes/ui.md).
