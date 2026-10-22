"""Eval CLI.

Usage:
  uv run python -m evals.run --suite custom --config configs/h1.yaml \
      --repeats 3 [--limit N]

Writes the report as JSON to docs/results/ and prints a summary.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from analyst.config import load_config
from analyst.db import Database

from .minidev import load_minidev
from .runner import make_agent_factory, run_suite

SUITES = {
    "custom": "evals/custom/questions.jsonl",
    "minidev": "data/bird/minidev/MINIDEV/mini_dev_sqlite.json",
}


def load_questions(path: str, limit: int | None) -> list[dict]:
    questions = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                questions.append(json.loads(line))
    if limit:
        questions = questions[:limit]
    return questions


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", required=True, choices=sorted(SUITES))
    ap.add_argument("--config", required=True)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--per-db", type=int, default=None,
                    help="minidev only: keep at most N questions per database")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.suite == "minidev":
        questions = load_minidev(
            SUITES[args.suite], limit=args.limit, per_db=args.per_db
        )
    else:
        questions = load_questions(SUITES[args.suite], args.limit)
    print(
        f"suite={args.suite} run={cfg.run_name} questions={len(questions)} "
        f"repeats={args.repeats} model={cfg.model}"
    )

    # Suites whose questions carry their own db_path (Mini-Dev) need no
    # shared gold db; the custom suite uses the configured one.
    gold_db = None
    if not any(q.get("db_path") for q in questions):
        gold_db = Database(cfg.db_path, cfg.scratch_path, cfg.row_limit, cfg.timeout_s)
    report = run_suite(
        questions,
        make_agent_factory(cfg),
        repeats=args.repeats,
        gold_db=gold_db,
        cfg=cfg,
        suite=args.suite,
        config_name=cfg.run_name,
        model=cfg.model,
    )

    out_dir = Path("docs/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.suite}_{cfg.run_name}_{report.date}.json"
    out.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))

    print(f"\n=== {cfg.run_name} on {args.suite} ===")
    print(f"success rate:        {report.success_rate:.1%}")
    print(f"loose success rate:  {report.loose_success_rate:.1%}  (gold value present in agent rows)")
    print(f"pass^{args.repeats}:              {report.pass_k:.1%}")
    print(f"tokens/task:         {report.mean_tokens:.0f}")
    print(f"steps/task:          {report.mean_steps:.2f}")
    print(f"sql error rate:      {report.sql_error_rate:.1%}")
    print(f"clarifications/task: {report.mean_clarifications:.2f}")
    print(f"report: {out}")


if __name__ == "__main__":
    main()
