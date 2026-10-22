"""Chunk the markdown docs plus one chunk per table, and build a TF-IDF
index stored as JSON.

Chunk layout:
- Each ``##`` section of each markdown doc becomes one chunk
  (source = ``<file>#<section>``).
- Each table becomes one chunk (source = table name) with its columns,
  types, and sample values — the same text the baseline prompt uses.

The index file stores, per chunk, a term-frequency vector plus the
corpus IDF, so search needs no database access.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, asdict
from pathlib import Path

from analyst.db import Database

_TOKEN = re.compile(r"[a-z0-9_]+")


@dataclass
class Chunk:
    text: str
    source: str
    score: float = 0.0


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _tf(tokens: list[str]) -> dict[str, float]:
    counts: dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    total = max(1, len(tokens))
    return {t: c / total for t, c in counts.items()}


def _doc_chunks(docs_dir: str) -> list[tuple[str, str]]:
    """Yield (source, text) for each ## section of each markdown file."""
    out: list[tuple[str, str]] = []
    for md in sorted(Path(docs_dir).glob("*.md")):
        current_title = None
        current_lines: list[str] = []

        def flush() -> None:
            if current_title and current_lines:
                text = "\n".join(current_lines).strip()
                if text:
                    out.append((f"{md.name}#{current_title}", text))

        for line in md.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                flush()
                current_title = line[3:].strip().lower()
                current_lines = [line]
            elif line.startswith("# ") and current_title is None:
                # H1 title line: skip (not a section)
                continue
            else:
                if current_title is not None:
                    current_lines.append(line)
        flush()
    return out


def _table_chunks(db: Database) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for table in db.tables():
        info = db.describe(table, samples=2)
        cols = ", ".join(f"{c['name']} {c['type']}" for c in info["columns"])
        text = f"{table}({cols})"
        if info["sample_rows"]:
            text += " samples: " + "; ".join(
                str(tuple(r)) for r in info["sample_rows"]
            )
        out.append((table, text))
    return out


def build_index(docs_dir: str, db: Database, out_path: str) -> int:
    """Build the TF-IDF index and write it to out_path. Returns chunk count."""
    chunks = _doc_chunks(docs_dir) + _table_chunks(db)
    tfs = [_tf(_tokens(text)) for _, text in chunks]

    # document frequency per term
    df: dict[str, int] = {}
    for tf in tfs:
        for term in tf:
            df[term] = df.get(term, 0) + 1
    n = len(chunks)
    idf = {t: math.log((1 + n) / (1 + d)) + 1.0 for t, d in df.items()}

    payload = {
        "chunks": [
            {
                "text": text,
                "source": source,
                "tf": tf,
            }
            for (source, text), tf in zip(chunks, tfs)
        ],
        "idf": idf,
    }
    Path(out_path).write_text(json.dumps(payload, ensure_ascii=False))
    return n
