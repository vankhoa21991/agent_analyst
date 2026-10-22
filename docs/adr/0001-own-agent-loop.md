# ADR 0001: Own agent loop instead of a framework

- Status: accepted (2026-10-21)
- Context: the agent needs a small, fixed set of behaviors — a step loop
  with tool calls, hard token/step/context budgets, a clarify callback,
  an approval gate for writes, and tracing hooks. Off-the-shelf agent
  frameworks (LangGraph, CrewAI, …) pull in large dependency trees and
  hide the control flow we need to reason about for evals.
- Decision: implement the loop directly in `src/analyst/loop.py`
  (~300 lines): plain message list, one model call per step, tool
  dispatch through a registry, budgets checked after every step,
  structured `AgentResult` (stop_reason, sql_runs, sql_errors,
  clarifications, tokens).
- Consequences: every behavior is unit-testable with a fake LLM and a
  fake DB; eval configs flip one flag at a time; no framework upgrade
  risk. Cost: we own retries, context trimming, and the tool protocol
  ourselves (kept deliberately small).
