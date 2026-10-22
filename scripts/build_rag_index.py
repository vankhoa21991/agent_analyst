"""Build the RAG index (data dictionary + business definitions + one chunk
per table) from the real Olist database and docs.

Usage:
  uv run python scripts/build_rag_index.py \
      --docs data/docs --db data/olist.sqlite --out data/rag_index.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyst.db import Database  # noqa: E402
from analyst.rag.index import build_index  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", default="data/docs")
    ap.add_argument("--db", default="data/olist.sqlite")
    ap.add_argument("--out", default="data/rag_index.json")
    args = ap.parse_args()

    db = Database(args.db, args.db + ".scratch-for-index.sqlite", 200, 10)
    n = build_index(args.docs, db, args.out)
    print(f"indexed {n} chunks -> {args.out}")


if __name__ == "__main__":
    main()
