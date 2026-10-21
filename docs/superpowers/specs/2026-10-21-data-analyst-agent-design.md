# Data Analyst Agent — Portfolio Design

Date: 2026-10-21. Source: the user's plan "AI Agent Project Plan — Marketplace Seller Copilot.md", cut down in conversation.

## Intent and why

A portfolio project to help the owner get an interview for a **part-time AI engineer job**. The first reader is a **recruiter**, who spends a few minutes on the README, a demo, and a results table. A technical reviewer may follow up and read the repo and the evals.

So the project must show two things quickly:
1. A working agent that answers real business questions about a marketplace database.
2. Measured evidence that each feature added improved it (an eval table), not just a demo.

## What it is

A Data Analyst Agent. The user asks a business question about the public Olist marketplace dataset. The agent finds the relevant tables, asks a clarifying question if the request is ambiguous, writes and checks SQL, runs it read-only, and answers with the query shown. SQL that changes data waits for the user's approval.

## Constraints

- **Time:** 10 hours a week for 2 months, about 80 hours. Scope must fit with slack.
- **Data:** public only. Olist for the demo; BIRD databases for scoring. Nothing from the owner's employer.
- **Run:** one `docker compose up` starts everything locally.
- **Models:** the owner runs a local **Qwen3.8 27B, 4-bit quantised** (as the owner named it). Money cost per call is zero; the limits are speed, context length and how reliably the model calls tools. The agent talks to any OpenAI-compatible endpoint, so the local server (the owner runs it with llama.cpp, which I assume means `llama-server`, my guess; its speed is not yet measured) is the default and 9Router (as in the original plan) is optional, for a later hosted-model comparison.

## Goals

1. **Own agent loop** (model call, tool calls, results, repeat) with step and token budgets, a clarify step (ask before querying when the question is ambiguous) and an approval gate before any data-changing SQL.
2. **RAG for schema linking:** index the data dictionary, column descriptions, sample values and docs; hybrid (keyword + vector) search plus a reranker picks relevant tables and docs, instead of pasting the whole schema into the prompt.
3. **Tools:** list tables, describe table (columns, types, sample values), search docs, run read-only SQL (read-only role, row and time limits), execute write (behind approval).
4. **SQL checks:** parse and lint with sqlglot, run EXPLAIN before executing, flag empty or suspiciously large results, retry with the error message on failure.
5. **Evaluation and tracing:**
   - BIRD-Interact Mini (SQLite) as the main benchmark: it scores clarification, RAG and write actions together.
   - BIRD Mini-Dev as a fast regression check.
   - A custom suite of 50–100 questions on Olist with verified SQL and answers, plus tests that writes always wait for approval.
   - Langfuse traces with SQL text, rows returned, time and errors.
6. **Chat UI, README, Docker, short demo video.**

### Experiment runs (one feature at a time, same model and tasks, each compared with the previous)

| Run | Agent | Compared with |
| --- | --- | --- |
| B0 | Plain model, full schema in the prompt | Published results |
| H1 | Own harness with schema tools | B0 |
| H2 | + RAG schema linking | H1 |
| H3 | + SQL checks and retry | H2 |
| H4 | + clarify policy | H3 |

Metrics per run: success rate, execution accuracy on Mini-Dev, tokens and turns per task, SQL error rate, clarifying questions per task, and pass^k over 3 runs.

Fair-comparison rules (from the original plan): use each benchmark's own user-simulator settings and splits; pin one model and turn off fallback combos during runs; report model string and date with every result; check the 9Router connection on one task before a full run.

Error analysis: tag each failure (wrong table, wrong join, wrong definition, wrong aggregation, should have asked, disputed gold answer), fix the cause, and add the case to the custom suite. The BIRD gold answers contain known annotation errors (CIDR 2026 study), so read failures before blaming the agent and keep a list of disputed answers.

## Non-goals (out for now)

Three-layer memory, Python sandbox, charts, analysis skills, DABstep, MCP servers, Helm, Terraform, cloud deployment, multi-tenant features, reranker comparisons, a semantic layer. Any of these may come back later but none is needed for v1.

## Anti-goals

- A deployment or infrastructure showcase that eats the time for the agent and evals.
- A demo with no numbers.
- Scores presented without the model string, date, and the caveat about gold answers.
- Data-changing SQL ever running without approval.

## Decisions the owner made

- Audience is a recruiter; the goal is a part-time AI engineer job.
- Keep: the harness (own loop, clarify, approval) and RAG.
- Keep: evaluation like the original plan (BIRD-Interact main, Mini-Dev, custom suite, step-by-step runs), plus tracing.
- Docker only; no Helm or Terraform.
- Duration: 2 months at 10 h/week.
- Defaults accepted as stated: Python 3.12, Postgres + pgvector, models via 9Router, Chainlit UI, project folder `~/marketplace-analyst`.

## Calls made by the designer (to check)

- Tools are plain Python functions, not MCP servers.
- Start with a plain own loop; adopt a framework only if clearly worth it.
- Runs reduced from 8 to 5 (B0, H1–H4) because memory, skills and the cheaper-model run are out.
- DABstep dropped because it needs the Python sandbox.
- A short demo video is part of the deliverable.
- **BIRD-Interact go/no-go:** in week 1, run one BIRD-Interact Mini task end to end with the own loop. If that can't be done by the end of week 2, drop it as the main benchmark; Mini-Dev plus the custom suite become the headline results, and the clarify policy (H4) is scored on the custom suite's ambiguous questions.
- **Data backends:** the analysed data is SQLite (Olist loaded into SQLite, BIRD Mini databases as shipped). Postgres + pgvector holds only the agent's own state and vectors. One data dialect, so sqlglot and the read-only role handle one backend.
- **Writes:** data-changing SQL runs only against a scratch copy of the database, reset before each eval run so runs are repeatable. Olist write cases are part of the custom suite.
- **Custom suite grading:** result-set match against verified SQL (numeric answers compared with a tolerance). The owner verifies the gold SQL, with model-drafted candidates to speed it up; this time is inside the 80 hours (about 8–10 h).
- **Run size:** subsets of Mini-Dev and BIRD-Interact are acceptable if the subset and sample are disclosed. B0 is the owner's own baseline on the same model; published numbers are shown for context only, not as an equal comparison.
- **Demo:** `docker compose up`, plus screenshots/GIFs and a short video in the README. No hosted demo in v1.

## Left to the builder

Week-by-week sequencing within the 80 hours (tracing from the first day is advisable); which embedding model, reranker and hybrid-search method; chunking of docs; exact tool schemas; how the approval gate and clarify step show in Chainlit; which model through 9Router; how to adapt BIRD-Interact Mini's simulated-user harness to the own loop; the README structure; whether to try a framework after the own loop works.

## Open risks

- BIRD-Interact Mini's harness may be awkward to integrate; budget time early to run one task end to end.
- **Run time, not money, is the budget.** 5 configurations × 3 repeats over three question sets on a local 27B model may take many hours of GPU time. Measure tokens per second and seconds per task on day one, then decide the sample sizes (the design allows disclosed subsets).
- **Tool-calling and context:** a 4-bit 27B model may call tools less reliably and have a shorter usable context than hosted models. The loop should validate tool-call output and retry; RAG is more valuable here because the full schema may not fit. Pin the exact model file, quantisation and server settings in every results table.
- **User simulator:** BIRD-Interact's simulated user is another model; use the same local model unless the benchmark requires otherwise, and say which in the results.
