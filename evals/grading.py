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
