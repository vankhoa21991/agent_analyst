"""Aggregate docs/results/*.json into docs/results/results.md.

Usage: uv run python scripts/results_table.py

Reads every saved eval report, one row per (suite, config), plus the
ambiguous-subset breakdown and the list of questions that failed.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
RESULTS = ROOT / "docs" / "results"

CONFIG_ORDER = ["b0", "h1", "h2", "h3", "h4"]
CONFIG_LABEL = {
    "b0": "B0", "h1": "H1", "h2": "H2", "h3": "H3", "h4": "H4",
}


def load_reports() -> list[dict]:
    reports = []
    for f in sorted(RESULTS.glob("*.json")):
        r = json.loads(f.read_text())
        if not isinstance(r, dict) or "per_question" not in r:
            continue  # spike traces, not ladder reports
        r["_file"] = f.name
        reports.append(r)
    return reports


def fmt(x: float, pct: bool = True) -> str:
    return f"{x * 100:.1f}%" if pct else f"{x:.1f}"


def main() -> None:
    reports = load_reports()
    if not reports:
        raise SystemExit("no reports in docs/results/")

    lines: list[str] = []
    lines.append("# Eval results\n")
    lines.append(
        "Local model: Qwen3.8-27B (UD-Q4_K_XL) on llama.cpp llama-server "
        "with MTP speculative decoding. One flag per config (see "
        "docs/adr/0004-flag-per-feature-runs.md). Custom suite: 55 "
        "questions (40 unambiguous reads, 10 ambiguous reads, 5 writes) "
        "x 3 repeats. Mini-Dev subset: 2 questions per DB x 11 DBs x 1 "
        "repeat (22 tasks). Grading: strict = answer SQL result equals "
        "gold (aliases ignored); loose = strict or the gold value "
        "appears in the agent's rows.\n"
    )
    lines.append("| Config | Suite | strict | loose | pass^3 | "
                 "tokens/task | steps/task | SQL err | clarify/task |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")

    def sort_key(r):
        c = CONFIG_ORDER.index(r["config_name"]) if r["config_name"] in CONFIG_ORDER else 99
        return (0 if r["suite"] == "custom" else 1, c)

    for r in sorted(reports, key=sort_key):
        if r["config_name"] in ("households_1", "alien_1"):
            continue  # BIRD-Interact spike, not part of the ladder
        lines.append(
            f"| {CONFIG_LABEL.get(r['config_name'], r['config_name'])} "
            f"| {r['suite']} | {fmt(r['success_rate'])} "
            f"| {fmt(r['loose_success_rate'])} | {fmt(r['pass_k'])} "
            f"| {fmt(r['mean_tokens'], pct=False)} "
            f"| {fmt(r['mean_steps'], pct=False)} "
            f"| {fmt(r['sql_error_rate'])} "
            f"| {fmt(r['mean_clarifications'], pct=False)} |"
        )

    # ambiguous subset (custom suite only)
    lines.append("\n## Ambiguous questions (custom suite, 10 questions)\n")
    lines.append("| Config | strict | loose | clarifications/task |")
    lines.append("| --- | --- | --- | --- |")
    for r in sorted(reports, key=sort_key):
        if r["suite"] != "custom":
            continue
        amb = [q for q in r["per_question"] if q.get("ambiguous")]
        if not amb:
            continue
        strict = sum(sum(q["correct"]) for q in amb) / sum(len(q["correct"]) for q in amb)
        loose = sum(sum(q["loose"]) for q in amb) / sum(len(q["loose"]) for q in amb)
        # clarifications are not stored per question; approximate with the
        # run mean (0 when clarify is off)
        clar = r["mean_clarifications"]
        lines.append(
            f"| {CONFIG_LABEL.get(r['config_name'], r['config_name'])} "
            f"| {fmt(strict)} | {fmt(loose)} | {fmt(clar, pct=False)} |"
        )

    # write subset
    lines.append("\n## Write questions (custom suite, 5 questions)\n")
    lines.append("| Config | strict | loose |")
    lines.append("| --- | --- | --- |")
    for r in sorted(reports, key=sort_key):
        if r["suite"] != "custom":
            continue
        wr = [q for q in r["per_question"] if q.get("write")]
        if not wr:
            continue
        strict = sum(sum(q["correct"]) for q in wr) / sum(len(q["correct"]) for q in wr)
        loose = sum(sum(q["loose"]) for q in wr) / sum(len(q["loose"]) for q in wr)
        lines.append(
            f"| {CONFIG_LABEL.get(r['config_name'], r['config_name'])} "
            f"| {fmt(strict)} | {fmt(loose)} |"
        )

    # failing questions per config (custom)
    lines.append("\n## Questions not solved in all repeats (custom)\n")
    for r in sorted(reports, key=sort_key):
        if r["suite"] != "custom":
            continue
        fails = [
            f"{q['id']} ({sum(q['correct'])}/{len(q['correct'])})"
            for q in r["per_question"] if not q["solved_every_repeat"]
        ]
        label = CONFIG_LABEL.get(r["config_name"], r["config_name"])
        lines.append(f"- **{label}**: {', '.join(fails) if fails else 'none'}")

    out = RESULTS / "results.md"
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out} from {len(reports)} report(s)")


if __name__ == "__main__":
    main()
