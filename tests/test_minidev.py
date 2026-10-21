"""Tests for the BIRD Mini-Dev (SQLite) loader."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.minidev import load_minidev  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "minidev"
FIXTURE = FIXTURE_DIR / "mini_dev_fixture.json"


def test_load_minidev_maps_fields():
    questions = load_minidev(FIXTURE)
    assert len(questions) == 2
    for q in questions:
        # the runner's question-dict contract
        for key in ("id", "question", "gold_sql", "ambiguous", "write"):
            assert key in q, f"missing {key}"
        # mini-dev specifics
        assert q["db_id"] == "testdb"
        assert Path(q["db_path"]).name == "testdb.sqlite"
        assert q["db_path"].endswith("dev_databases/testdb/testdb.sqlite")
        assert q["write"] is False
        assert q["ambiguous"] is False
    assert questions[0]["id"] == "minidev_1"
    assert "dogs are rows where species" in questions[0]["question"]
    assert questions[1]["evidence"] == ""


def test_load_minidev_limit():
    questions = load_minidev(FIXTURE, limit=1)
    assert len(questions) == 1
    assert questions[0]["id"] == "minidev_1"


def test_load_minidev_explicit_db_root():
    questions = load_minidev(FIXTURE, db_root=FIXTURE_DIR)
    assert questions[0]["db_path"] == str(
        FIXTURE_DIR / "testdb" / "testdb.sqlite"
    )
