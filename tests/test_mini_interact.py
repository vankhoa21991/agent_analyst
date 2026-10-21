"""Unit tests for the BIRD-Interact Mini helpers (no live model)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.mini_interact.user_sim import (  # noqa: E402
    MiniInteractUserSimulator,
    load_kb,
    load_task,
)

import pytest  # noqa: E402

DATA = ROOT / "data" / "mini-interact"

pytestmark = pytest.mark.skipif(
    not (DATA / "mini_interact.jsonl").exists(),
    reason="mini-interact data not downloaded (scripts: hf download birdsql/mini-interact)",
)


class FakeLLM:
    """Returns a canned reply and records the messages it was given."""

    def __init__(self, reply: str = "I want the average, per region."):
        self.reply = reply
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None):
        self.calls.append(messages)

        class R:
            content = self.reply
            prompt_tokens = 10
            completion_tokens = 5

        return R()


def test_load_task_finds_record():
    rec = load_task(DATA / "mini_interact.jsonl", "households_1")
    assert rec["selected_database"] == "households"
    assert rec["amb_user_query"]


def test_load_task_missing_raises():
    try:
        load_task(DATA / "mini_interact.jsonl", "nope_999")
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_load_kb_masks_deleted_ids():
    kb = load_kb(DATA / "households" / "households_kb.jsonl")
    assert any(e["id"] == 15 for e in kb)
    masked = load_kb(
        DATA / "households" / "households_kb.jsonl", deleted_ids=[15]
    )
    assert not any(e["id"] == 15 for e in masked)
    assert len(masked) == len(kb) - 1


def test_simulator_uses_ambiguity_snippets_in_prompt():
    rec = load_task(DATA / "mini_interact.jsonl", "households_1")
    llm = FakeLLM()
    sim = MiniInteractUserSimulator(llm, rec)
    assert sim.reference_sql == "(not available)"  # public data has no GT
    terms = [c["ambiguity_term"] for c in sim.clarifications]
    assert "typical bathroom ratio" in terms

    answer = sim.ask("What do you mean by typical bathroom ratio?")
    assert answer == "I want the average, per region."
    assert len(llm.calls) == 1
    user_msg = llm.calls[0][1]["content"]
    assert "typical bathroom ratio" in user_msg
    # the sql snippet for the ambiguous term is part of the hidden info
    assert "Bath_Count" in user_msg
    # dialogue history grew: query, agent question, user answer
    assert len(sim.dialogue_history) == 3


def test_simulator_reference_sql_when_present():
    rec = load_task(DATA / "mini_interact.jsonl", "households_1")
    rec = dict(rec)
    rec["sol_sql"] = ["SELECT 1"]
    sim = MiniInteractUserSimulator(FakeLLM(), rec)
    assert sim.reference_sql == "SELECT 1"
