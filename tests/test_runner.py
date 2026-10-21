from pathlib import Path

from analyst.config import AgentConfig
from analyst.db import Database, load_olist
from analyst.loop import AgentResult
from evals.runner import RunReport, run_suite

FIXTURES = Path(__file__).parent / "fixtures" / "olist"


def make_gold_db(tmp_path):
    src = tmp_path / "src.sqlite"
    load_olist(str(FIXTURES), str(src))
    return Database(str(src), str(tmp_path / "s.sqlite"), row_limit=200, timeout_s=10)


def make_cfg(tmp_path, gold_db=None):
    return AgentConfig(
        model="m", base_url="http://localhost:1/v1", api_key="k",
        db_path=gold_db.path if gold_db else str(tmp_path / "unused.sqlite"),
        scratch_path=gold_db.scratch_path if gold_db else str(tmp_path / "unused_s.sqlite"),
    )


def fake_agent_factory(sqls):
    """sqls: list of one SQL per attempt, in order."""
    state = {"i": 0}

    class Fake:
        def run(self, question):
            i = state["i"]
            state["i"] += 1
            sql = sqls[i % len(sqls)]
            return AgentResult(
                answer="a", sql=sql, steps=1, prompt_tokens=10,
                completion_tokens=5, clarifications=0, stop_reason="answered",
            )

    return lambda q: Fake()


def test_runner_pass_k_counts_only_questions_solved_every_repeat(tmp_path):
    gold_db = make_gold_db(tmp_path)
    questions = [
        {
            "id": "q1",
            "question": "how many orders?",
            "gold_sql": "SELECT COUNT(*) AS n FROM orders",
        }
    ]
    good = "SELECT COUNT(*) AS n FROM orders"
    bad = "SELECT 2 AS n"
    # 3 repeats: right, wrong, right -> not solved every repeat
    report = run_suite(
        questions, fake_agent_factory([good, bad, good]), repeats=3,
        gold_db=gold_db, cfg=make_cfg(tmp_path, gold_db),
        suite="t", config_name="t", model="m",
    )
    assert isinstance(report, RunReport)
    assert report.pass_k == 0.0
    assert report.success_rate == 2 / 3
    assert report.per_question[0]["correct"] == [True, False, True]
    assert report.per_question[0]["solved_every_repeat"] is False


def test_runner_all_correct_gives_full_pass(tmp_path):
    gold_db = make_gold_db(tmp_path)
    questions = [
        {
            "id": "q1",
            "question": "how many orders?",
            "gold_sql": "SELECT COUNT(*) AS n FROM orders",
        }
    ]
    good = "SELECT COUNT(*) AS n FROM orders"
    report = run_suite(
        questions, fake_agent_factory([good]), repeats=3,
        gold_db=gold_db, cfg=make_cfg(tmp_path, gold_db),
        suite="t", config_name="t", model="m",
    )
    assert report.pass_k == 1.0
    assert report.success_rate == 1.0
    assert report.mean_steps == 1.0
    assert report.mean_tokens == 15.0
    assert report.sql_error_rate == 0.0


def test_runner_write_question_needs_approved_write(tmp_path):
    gold_db = make_gold_db(tmp_path)
    questions = [
        {
            "id": "w1",
            "question": "mark o1 as x",
            "gold_sql": "UPDATE orders SET order_status = 'x' WHERE order_id = 'o1'",
            "write": True,
        }
    ]

    class Fake:
        def run(self, question):
            return AgentResult(
                answer="done", sql=None, steps=1, prompt_tokens=10,
                completion_tokens=5, clarifications=0, stop_reason="answered",
                write_sql="UPDATE orders SET order_status = 'x' WHERE order_id = 'o1'",
                write_rows=1,
            )

    report = run_suite(
        questions, lambda q: Fake(), repeats=1,
        gold_db=gold_db, cfg=make_cfg(tmp_path, gold_db),
        suite="t", config_name="t", model="m",
    )
    assert report.success_rate == 1.0


def test_runner_write_question_fails_without_write(tmp_path):
    gold_db = make_gold_db(tmp_path)
    questions = [
        {
            "id": "w1",
            "question": "mark o1 as x",
            "gold_sql": "UPDATE orders SET order_status = 'x' WHERE order_id = 'o1'",
            "write": True,
        }
    ]

    class Fake:
        def run(self, question):
            return AgentResult(
                answer="ok", sql=None, steps=1, prompt_tokens=10,
                completion_tokens=5, clarifications=0, stop_reason="answered",
            )

    report = run_suite(
        questions, lambda q: Fake(), repeats=1,
        gold_db=gold_db, cfg=make_cfg(tmp_path, gold_db),
        suite="t", config_name="t", model="m",
    )
    assert report.success_rate == 0.0


def test_runner_uses_per_question_db_path(tmp_path):
    """Mini-Dev style: the question carries its own db_path; no gold_db."""
    import sqlite3

    dbfile = tmp_path / "other.sqlite"
    con = sqlite3.connect(dbfile)
    con.execute("CREATE TABLE t (n INTEGER)")
    con.execute("INSERT INTO t VALUES (7)")
    con.commit()
    con.close()

    questions = [
        {
            "id": "q1",
            "question": "the number?",
            "gold_sql": "SELECT n FROM t",
            "db_path": str(dbfile),
        }
    ]

    report = run_suite(
        questions,
        fake_agent_factory(["SELECT n FROM t"]),
        repeats=1,
        gold_db=None,
        cfg=make_cfg(tmp_path, None),
        suite="t", config_name="t", model="m",
    )
    assert report.success_rate == 1.0
    # and a wrong answer on that db fails
    report = run_suite(
        questions,
        fake_agent_factory(["SELECT 8 FROM t"]),
        repeats=1,
        gold_db=None,
        cfg=make_cfg(tmp_path, None),
        suite="t", config_name="t", model="m",
    )
    assert report.success_rate == 0.0
