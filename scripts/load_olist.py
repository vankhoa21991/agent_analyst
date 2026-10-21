"""Load the Olist CSVs into a SQLite database.

Usage: uv run python scripts/load_olist.py [csv_dir] [sqlite_path]
Defaults: data/olist data/olist.sqlite
"""

from __future__ import annotations

import sys

from analyst.db import load_olist


def main() -> None:
    csv_dir = sys.argv[1] if len(sys.argv) > 1 else "data/olist"
    sqlite_path = sys.argv[2] if len(sys.argv) > 2 else "data/olist.sqlite"
    counts = load_olist(csv_dir, sqlite_path)
    for table, n in sorted(counts.items()):
        print(f"{table:<32} {n:>8,} rows")
    print(f"total: {sum(counts.values()):,} rows -> {sqlite_path}")


if __name__ == "__main__":
    main()
