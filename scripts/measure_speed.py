"""Measure tokens/s and seconds per reply on the local model (Task 0, step 5).

Usage: uv run python scripts/measure_speed.py [path/to/agent.yaml]

Sends 5 fixed prompts through LLMClient (one of them a tool-calling prompt,
since that is the agent's real workload) and prints mean tokens/s and mean
seconds per reply. Record the output in docs/notes/speed.md.
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

from analyst.config import load_config
from analyst.llm import LLMClient

RUN_SQL_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": "Run a read-only SQL query against the database",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
            },
        },
    }
]

PROMPTS: list[tuple[str, list[dict] | None, str]] = [
    ("short", None, "Reply with the single word: OK"),
    ("math", None, "What is 17 * 23? Answer with just the number."),
    (
        "medium",
        None,
        "List the 5 steps of a typical data analysis project, one per line.",
    ),
    (
        "tool_call",
        RUN_SQL_TOOL,
        "How many orders are there? Use the run_sql tool.",
    ),
    (
        "business",
        None,
        "Summarise in one sentence why a marketplace analyst would care "
        "about delivery times.",
    ),
]


def main() -> None:
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "agent.yaml"
    cfg = load_config(cfg_path)
    client = LLMClient(cfg)

    rows = []
    for name, tools, prompt in PROMPTS:
        reply = client.chat(
            [{"role": "user", "content": prompt}], tools=tools
        )
        tok_per_s = (
            reply.completion_tokens / reply.seconds if reply.seconds > 0 else 0.0
        )
        rows.append((name, reply.seconds, reply.completion_tokens, tok_per_s))
        print(
            f"{name:<12} {reply.seconds:8.2f}s  "
            f"{reply.completion_tokens:5d} tokens  {tok_per_s:7.2f} tok/s"
        )

    print(
        f"mean: {statistics.mean(r[1] for r in rows):.2f}s per reply, "
        f"{statistics.mean(r[3] for r in rows):.2f} tokens/s"
    )


if __name__ == "__main__":
    main()
