"""Tests for the in-process RAG index and retriever.

The plan specified Postgres + pgvector + bge embeddings/reranker; this
environment has no Docker and the local server has no embeddings
endpoint, so the retriever is dependency-free TF-IDF cosine over the
same chunk layout (ADR: docs/adr/0002-rag-without-postgres.md).
"""

from pathlib import Path

import pytest

from analyst.db import Database, load_olist
from analyst.rag.index import build_index
from analyst.rag.search import Retriever

FIXTURES = Path(__file__).parent / "fixtures"
DOCS = FIXTURES / "docs"


@pytest.fixture()
def db(tmp_path):
    src = tmp_path / "src.sqlite"
    load_olist(str(FIXTURES / "olist"), str(src))
    return Database(str(src), str(tmp_path / "s.sqlite"), row_limit=200, timeout_s=10)


@pytest.fixture()
def index(tmp_path, db):
    out = tmp_path / "index.json"
    n = build_index(str(DOCS), db, str(out))
    return out, n


def test_index_counts_chunks(index, db):
    out, n = index
    assert n > 0
    # one chunk per ## section of each doc + one chunk per table
    import re
    from pathlib import Path
    sections = 0
    for md in Path("tests/fixtures/docs").glob("*.md"):
        sections += sum(1 for line in md.read_text().splitlines() if line.startswith("## "))
    assert n == sections + len(db.tables())


def test_search_finds_delivery_table(index):
    out, _ = index
    r = Retriever(str(out))
    top3 = [c.source for c in r.search("which orders were delivered late?", k=3)]
    assert "orders" in top3 or "data_dictionary.md#orders" in top3


def test_search_finds_business_definition(index):
    out, _ = index
    r = Retriever(str(out))
    chunks = r.search("what counts as an active seller?", k=3)
    assert any("active seller" in c.text.lower() for c in chunks[:1])


def test_exact_column_name_hit(index):
    """A rare exact column name must still surface the right chunks —
    the keyword path works even for terms no embedding has seen.
    Both the table chunk and the doc section that uses the column are
    legitimate top hits."""
    out, _ = index
    r = Retriever(str(out))
    top3 = [c.source for c in r.search("order_delivered_customer_date", k=3)]
    assert "orders" in top3
    assert "business_definitions.md#late delivery" in top3


def test_search_scores_descending(index):
    out, _ = index
    r = Retriever(str(out))
    chunks = r.search("revenue of delivered orders", k=5)
    scores = [c.score for c in chunks]
    assert scores == sorted(scores, reverse=True)
    assert all(c.score > 0 for c in chunks)


def test_search_empty_query_returns_empty(index):
    out, _ = index
    r = Retriever(str(out))
    assert r.search("", k=5) == []
