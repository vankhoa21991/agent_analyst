"""BIRD Mini-Dev (SQLite) loader: maps benchmark records to the eval
question-dict format used by run_suite.

Dataset: https://huggingface.co/datasets/birdsql/bird_mini_dev (CC BY 4.0,
BIRD team). The SQLite databases ship in the "Mini-Dev Complete Package"
zip (Google Drive link on the dataset card); each DB lives at
`dev_databases/<db_id>/<db_id>.sqlite` with per-table description CSVs in
`dev_databases/<db_id>/database_description/`.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_minidev(
    path: str | Path,
    limit: int | None = None,
    db_root: str | Path | None = None,
) -> list[dict]:
    """Load Mini-Dev SQLite records as question dicts.

    Each dict has the runner contract keys (`id`, `question`, `gold_sql`,
    `ambiguous`, `write`) plus `db_id`, `db_path`, `evidence`,
    `difficulty`. BIRD's `evidence` (external knowledge) is appended to
    the question, as the benchmark intends the model to use it.
    """
    path = Path(path)
    records = json.loads(path.read_text())
    if limit is not None:
        records = records[:limit]
    if db_root is None:
        db_root = path.parent / "dev_databases"
    db_root = Path(db_root)

    questions = []
    for r in records:
        question = r["question"]
        if r.get("evidence"):
            question = f"{question}\n(External knowledge: {r['evidence']})"
        questions.append(
            {
                "id": f"minidev_{r['question_id']}",
                "question": question,
                "gold_sql": r["SQL"],
                "ambiguous": False,
                "write": False,
                "db_id": r["db_id"],
                "db_path": str(db_root / r["db_id"] / f"{r['db_id']}.sqlite"),
                "evidence": r.get("evidence", ""),
                "difficulty": r.get("difficulty", ""),
            }
        )
    return questions
