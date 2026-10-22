"""Result-set matching for evals."""

from __future__ import annotations

from analyst.db import QueryResult


def _values_match(ev, av, tol: float) -> bool:
    if ev is None and av is None:
        return True
    if ev is None or av is None:
        return False
    if isinstance(ev, (int, float)) and isinstance(av, (int, float)):
        return abs(ev - av) <= tol * max(1.0, abs(float(ev)), abs(float(av)))
    return str(ev) == str(av)


def _row_key(row: tuple) -> tuple:
    return tuple("" if v is None else str(v) for v in row)


def results_match(
    expected: QueryResult, actual: QueryResult, tol: float = 1e-6, ordered: bool = False
) -> bool:
    """Compare two result sets.

    Row values are compared; column names/aliases are ignored (the model
    chooses its own aliases) and row order is ignored by default (multiset
    comparison). Numeric cells compare with a relative tolerance.
    """
    if len(expected.rows) != len(actual.rows):
        return False

    exp_rows = list(expected.rows)
    act_rows = list(actual.rows)
    if not ordered:
        exp_rows.sort(key=_row_key)
        act_rows.sort(key=_row_key)

    for er, ar in zip(exp_rows, act_rows):
        if len(er) != len(ar):
            return False
        for ev, av in zip(er, ar):
            if not _values_match(ev, av, tol):
                return False
    return True


def value_present(gold: QueryResult, agent: QueryResult, tol: float = 1e-6) -> bool:
    """Supplementary (loose) check for single-value questions: the gold
    value appears somewhere in the agent's result rows. An agent that
    answers "how many X" with a breakdown that contains the right number
    computed the answer even though its SQL shape does not match the
    gold. Only meaningful when the gold result is exactly one row."""
    if len(gold.rows) != 1:
        return False
    for gv in gold.rows[0]:
        for row in agent.rows:
            if any(_values_match(gv, av, tol) for av in row):
                return True
    return False
