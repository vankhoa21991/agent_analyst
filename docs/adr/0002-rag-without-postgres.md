# ADR 0002: RAG without Postgres (in-process TF-IDF)

- Status: accepted (2026-10-22)
- Context: the design called for schema/business-doc retrieval. The
  default stack (pgvector in the compose Postgres + an embedding model)
  is unavailable here: no Docker, and the local llama-server runs
  qwen3.8-27b without the `--embeddings` flag, so there is no embedding
  endpoint. The corpus is tiny: two markdown docs (≈20 `##` sections)
  plus 10 table descriptions.
- Decision: retrieval is in-process and dependency-free.
  `scripts/build_rag_index.py` chunks the docs by `##` section plus one
  chunk per table (schema from the live DB) into `data/rag_index.json`
  (28 chunks). `Retriever.search` scores chunks with TF-IDF cosine
  (sublinear TF, smoothed IDF, L2-normalized vectors). Two ways in:
  a `search_docs` tool the agent can call, and the top-5 chunks
  pre-injected into the system prompt.
- Consequences: deterministic, fast, zero extra services or models;
  quality is keyword-driven (fine at this corpus size, see sanity
  checks in `docs/notes/evals.md`). If the corpus grows or real
  embeddings become available, only `rag/index.py` + `rag/search.py`
  change — the `Retriever` interface (search(query, k) -> chunks) is
  stable.
