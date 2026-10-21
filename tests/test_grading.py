from analyst.db import QueryResult
from evals.grading import results_match


def qr(cols, rows, truncated=False):
    return QueryResult(columns=cols, rows=rows, truncated=truncated, seconds=0.0)


def test_match_identical():
    a = qr(["n"], [(3,)])
    assert results_match(a, a) is True


def test_match_ignores_row_order_by_default():
    e = qr(["city", "n"], [("sao paulo", 10), ("curitiba", 5)])
    a = qr(["city", "n"], [("curitiba", 5), ("sao paulo", 10)])
    assert results_match(e, a) is True


def test_match_ordered_mode_respects_order():
    e = qr(["n"], [(1,), (2,)])
    a = qr(["n"], [(2,), (1,)])
    assert results_match(e, a, ordered=True) is False
    assert results_match(e, qr(["n"], [(1,), (2,)]), ordered=True) is True


def test_match_numeric_tolerance():
    e = qr(["x"], [(0.1 + 0.2,)])
    a = qr(["x"], [(0.30000000000000004,)])
    assert results_match(e, a) is True
    assert results_match(e, qr(["x"], [(0.31,)])) is False


def test_match_numeric_tolerance_relative():
    e = qr(["x"], [(1000.0,)])
    a = qr(["x"], [(1000.5,)])
    assert results_match(e, a) is False  # 0.05% off > 1e-6 relative
    assert results_match(e, a, tol=1e-2) is True


def test_match_column_aliases_ignored():
    # The model chooses its own aliases; only row values matter.
    e = qr(["n"], [(99441,)])
    a = qr(["total_orders"], [(99441,)])
    assert results_match(e, a) is True


def test_match_row_count_difference_false():
    e = qr(["n"], [(1,), (2,)])
    a = qr(["n"], [(1,)])
    assert results_match(e, a) is False


def test_match_none_vs_value_false():
    e = qr(["n"], [(None,)])
    a = qr(["n"], [(0,)])
    assert results_match(e, a) is False


def test_match_duplicates_counted():
    e = qr(["n"], [(1,), (1,), (2,)])
    a = qr(["n"], [(1,), (2,)])
    assert results_match(e, a) is False
