# ADR 0003: SQLite-only data layer

- Status: accepted (2026-10-21)
- Context: the target data is the public Olist marketplace (10 tables,
  ~100 MB of CSVs) and the BIRD Mini-Dev SQLite benchmark. Postgres was
  on the original design (for pgvector), but Docker is unavailable in
  this environment, and nothing in the analyst workload needs it.
- Decision: the data layer (`src/analyst/db.py`) speaks SQLite only.
  The source DB is opened read-only (`file:...?mode=uri`); every
  read-only `run_sql` runs against it. Writes go to a separate scratch
  copy that is reset from the source before each task, so the source
  can never be modified and writes are isolated per run.
- Consequences: one `sqlite3` dependency, trivially portable, and the
  same code path serves the Olist DB and every Mini-Dev benchmark DB
  (per-question `db_path`). SQL checks (sqlglot) use the `sqlite`
  dialect. If Postgres is ever required, `Database` is the single
  seam to swap.
