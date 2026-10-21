"""BIRD-Interact Mini spike: run one task end-to-end with the analyst loop.

Wires the official-style LLM user simulator into the agent's ask_user tool,
masks the KB entries the benchmark hides (`deleted_knowledge`), and runs the
task against a copy of the task's SQLite DB.

Usage:
    uv run python scripts/mini_interact_spike.py --task households_1
    uv run python scripts/mini_interact_spike.py --task alien_3 --repeats 2

Notes:
- Public data has empty sol_sql/test_cases (GT is non-public), so this
  script cannot compute the official success rate. It verifies the
  interaction loop works and reports the final SQL for manual review.
- The downloaded DB original is never modified: reads go to the original
  (opened read-only), writes go to an isolated scratch copy.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from analyst.config import load_config  # noqa: E402
from analyst.db import Database  # noqa: E402
from analyst.llm import LLMClient  # noqa: E402
from analyst.loop import Agent  # noqa: E402
from analyst.tools import ToolRegistry  # noqa: E402
from analyst.tracing import Tracer  # noqa: E402

from evals.mini_interact.user_sim import (  # noqa: E402
    MiniInteractUserSimulator,
    load_kb,
    load_task,
)

DATA = ROOT / "data" / "mini-interact"

SYSTEM_PROMPT_TEMPLATE = """You are a data analyst agent for the {db_name} SQLite database.
The user's question is deliberately ambiguous and depends on business knowledge.

# Database schema
{schema}

# Business knowledge base
{kb}

# Rules
1. Explore with list_tables / describe_table / run_sql as needed.
2. The question contains ambiguous terms. Use ask_user to clarify them with
   the user, ONE question at a time, before writing the final SQL.
3. When you are confident, reply with a concise plain-text answer and show
   the final SQL you used in a ```sql code block.
"""

_SQL_BLOCK = re.compile(r"```sql\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_ANY_BLOCK = re.compile(r"```\s*(.*?)```", re.DOTALL)


def build_system_prompt(db_name: str, schema: str, kb_entries: list[dict]) -> str:
    kb_lines = []
    for e in kb_entries:
        children = e.get("children_knowledge")
        child_note = ""
        if isinstance(children, list) and children:
            child_note = f" (depends on knowledge ids: {', '.join(map(str, children))})"
        kb_lines.append(
            f"- [{e['id']}] {e['knowledge']}: {e['description']} "
            f"Definition: {e['definition']}{child_note}"
        )
    kb = "\n".join(kb_lines) if kb_lines else "(empty)"
    return SYSTEM_PROMPT_TEMPLATE.format(
        db_name=db_name, schema=schema.strip(), kb=kb
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="households_1")
    ap.add_argument("--config", default=str(ROOT / "configs/h1.yaml"))
    ap.add_argument("--repeats", type=int, default=1)
    args = ap.parse_args()

    record = load_task(DATA / "mini_interact.jsonl", args.task)
    db_name = record["selected_database"]
    db_dir = DATA / db_name
    original_db = db_dir / f"{db_name}.sqlite"
    scratch_db = db_dir / f"{db_name}_scratch.sqlite"

    cfg = load_config(args.config)
    cfg.db_path = str(original_db)
    cfg.scratch_path = str(scratch_db)
    cfg.clarify = True
    # The benchmark prompt is much larger than Olist's; give the loop room.
    cfg.max_steps = 20
    cfg.max_total_tokens = 80000

    schema = (db_dir / f"{db_name}_schema.txt").read_text()
    deleted_ids = [
        a["deleted_knowledge"]
        for a in record.get("knowledge_ambiguity") or []
        if a.get("deleted_knowledge") is not None
    ]
    kb_entries = load_kb(db_dir / f"{db_name}_kb.jsonl", deleted_ids)
    system_prompt = build_system_prompt(db_name, schema, kb_entries)

    print(f"task: {record['instance_id']}  db: {db_name}")
    print(f"query: {record['amb_user_query']}")
    print(f"masked KB ids: {deleted_ids}")
    print(f"critical ambiguities: "
          f"{[a['term'] for a in record['user_query_ambiguity']['critical_ambiguity']]}")
    print("-" * 70)

    results = []
    for rep in range(args.repeats):
        db = Database(cfg.db_path, cfg.scratch_path, cfg.row_limit, cfg.timeout_s)
        db.reset_scratch()
        tools = ToolRegistry(db, cfg)
        llm = LLMClient(cfg)
        sim = MiniInteractUserSimulator(llm, record)

        def ask_user(q: str, _sim=sim) -> str:
            print(f"  [ask_user] {q}")
            reply = _sim.ask(q)
            print(f"  [user]     {reply}")
            return reply

        agent = Agent(
            cfg, llm, tools, Tracer(), ask_user,
            approve=lambda sql: True, system_prompt=system_prompt,
        )
        res = agent.run(record["amb_user_query"])

        # The task's final SQL is what the agent reports in its answer, not
        # necessarily its last probe query. Prefer the SQL block in the answer.
        final_sql = res.sql
        m = _SQL_BLOCK.search(res.answer or "") or _ANY_BLOCK.search(res.answer or "")
        if m:
            final_sql = m.group(1).strip()
        sql_ok = None
        if final_sql:
            try:
                db.query(final_sql)
                sql_ok = True
            except Exception as e:  # noqa: BLE001
                sql_ok = False
                print(f"  [final SQL error] {e}")

        results.append(
            {
                "repeat": rep,
                "stop_reason": res.stop_reason,
                "steps": res.steps,
                "tokens": res.prompt_tokens + res.completion_tokens,
                "clarifications": res.clarifications,
                "sql_runs": res.sql_runs,
                "sql_errors": res.sql_errors,
                "final_sql": final_sql,
                "final_sql_executes": sql_ok,
                "answer": res.answer,
                "user_simulator_responses": sim.responses,
            }
        )
        print(f"[repeat {rep}] stop={res.stop_reason} steps={res.steps} "
              f"tokens={res.prompt_tokens}+{res.completion_tokens} "
              f"clarifications={res.clarifications} "
              f"sql_runs={res.sql_runs} sql_errors={res.sql_errors} "
              f"final_sql_executes={sql_ok}")
        print(f"  final SQL: {final_sql}")
        print(f"  answer: {res.answer[:300]}")
        print("-" * 70)

    out = ROOT / "docs" / "results" / f"mini_interact_{args.task}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
