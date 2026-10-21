"""Fixture: 2-task BIRD Mini-Dev (SQLite) file + a tiny database."""

import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _make_db() -> None:
    db = HERE / "dev_databases" / "testdb" / "testdb.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE pets (id INTEGER PRIMARY KEY, name TEXT, species TEXT)")
    con.executemany(
        "INSERT INTO pets (name, species) VALUES (?, ?)",
        [("Rex", "dog"), ("Whiskers", "cat"), ("Fido", "dog")],
    )
    con.commit()
    con.close()


def _write_fixture() -> None:
    recs = [
        {
            "question_id": 1,
            "db_id": "testdb",
            "question": "How many dogs are there?",
            "evidence": "dogs are rows where species = 'dog'.",
            "SQL": "SELECT COUNT(*) FROM pets WHERE species = 'dog'",
            "difficulty": "simple",
        },
        {
            "question_id": 2,
            "db_id": "testdb",
            "question": "List all pet names.",
            "evidence": "",
            "SQL": "SELECT name FROM pets",
            "difficulty": "simple",
        },
    ]
    (HERE / "mini_dev_fixture.json").write_text(json.dumps(recs, indent=1))


_make_db()
_write_fixture()
