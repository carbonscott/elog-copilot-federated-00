# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "psycopg2-binary",
# ]
# ///
"""
Migrate data from centralized SQLite elog database to PostgreSQL.

Usage:
    UV_CACHE_DIR=/sdf/data/lcls/ds/prj/prjdat21/results/cwang31/.UV_CACHE \
      uv run scripts/migrate_sqlite_to_postgres.py

    # Or with custom paths:
    uv run scripts/migrate_sqlite_to_postgres.py \
        --sqlite /path/to/elog.db \
        --pg-host /tmp \
        --pg-port 5434 \
        --pg-db elog_prototype
"""

import argparse
import sqlite3
import time
import sys

import psycopg2
import psycopg2.extras


# Default paths
# Canonical symlink (always points to latest):
#   /sdf/group/lcls/ds/dm/apps/dev/data/elog-copilot/elog-copilot.db
# Explicit snapshot used for initial testing:
SQLITE_PATH = "/sdf/group/lcls/ds/dm/apps/dev/data/elog-copilot/elog_2026_0204_1800.db"
PG_HOST = "/tmp"
PG_PORT = 5434
PG_DB = "elog_prototype"
BATCH_SIZE = 5000


# Migration order (respects foreign key dependencies)
# Format: (sqlite_table, pg_table, sqlite_columns, pg_columns, serial_col)
TABLES = [
    {
        "sqlite_table": "Experiment",
        "pg_table": "experiments",
        "columns": [
            "experiment_id", "name", "instrument", "start_time", "end_time",
            "pi", "pi_email", "leader_account", "description",
            "slack_channels", "analysis_queues", "urawi_proposal",
        ],
        "serial_col": None,  # TEXT primary key, no serial
    },
    {
        "sqlite_table": "Detector",
        "pg_table": "detectors",
        "columns": ["detector_id", "detector_name", "description"],
        "serial_col": "detector_id",
        "serial_seq": "detectors_detector_id_seq",
    },
    {
        "sqlite_table": "Run",
        "pg_table": "runs",
        "columns": [
            "run_id", "run_number", "experiment_id", "start_time", "end_time",
        ],
        "serial_col": "run_id",
        "serial_seq": "runs_run_id_seq",
    },
    {
        "sqlite_table": "RunProductionData",
        "pg_table": "run_production_data",
        "columns": [
            "run_data_id", "run_id", "n_events", "n_damaged", "n_dropped",
            "prod_start", "prod_end", "number_of_files", "total_size_bytes",
        ],
        "serial_col": "run_data_id",
        "serial_seq": "run_production_data_run_data_id_seq",
    },
    {
        "sqlite_table": "Logbook",
        "pg_table": "logbook",
        "columns": [
            "log_id", "experiment_id", "run_id", "timestamp",
            "content", "tags", "author",
        ],
        "serial_col": "log_id",
        "serial_seq": "logbook_log_id_seq",
    },
    {
        "sqlite_table": "RunDetector",
        "pg_table": "run_detectors",
        "columns": ["run_detector_id", "run_id", "detector_id", "status"],
        "serial_col": "run_detector_id",
        "serial_seq": "run_detectors_run_detector_id_seq",
    },
    {
        "sqlite_table": "Questionnaire",
        "pg_table": "questionnaire",
        "columns": [
            "questionnaire_id", "experiment_id", "proposal", "category",
            "field_id", "field_name", "field_value",
            "modified_time", "modified_uid", "created_time",
        ],
        "serial_col": "questionnaire_id",
        "serial_seq": "questionnaire_questionnaire_id_seq",
    },
    {
        "sqlite_table": "Workflow",
        "pg_table": "workflows",
        "columns": [
            "workflow_id", "experiment_id", "mongo_id", "name", "executable",
            "trigger", "location", "parameters",
            "run_param_name", "run_param_value", "run_as_user",
        ],
        "serial_col": "workflow_id",
        "serial_seq": "workflows_workflow_id_seq",
    },
]


def migrate_table(sqlite_conn, pg_conn, table_spec):
    """Migrate a single table from SQLite to PostgreSQL."""
    sqlite_table = table_spec["sqlite_table"]
    pg_table = table_spec["pg_table"]
    columns = table_spec["columns"]
    serial_col = table_spec.get("serial_col")
    serial_seq = table_spec.get("serial_seq")

    # Read from SQLite
    sqlite_cols = ", ".join(columns)
    cursor = sqlite_conn.execute(f"SELECT {sqlite_cols} FROM {sqlite_table}")

    # Build PG insert
    # Quote "trigger" since it's a reserved word
    pg_cols = ", ".join(
        f'"{c}"' if c == "trigger" else c for c in columns
    )
    placeholders = ", ".join(["%s"] * len(columns))
    insert_sql = f"INSERT INTO {pg_table} ({pg_cols}) VALUES %s"

    pg_cursor = pg_conn.cursor()
    total_rows = 0
    t0 = time.time()

    while True:
        rows = cursor.fetchmany(BATCH_SIZE)
        if not rows:
            break
        # Convert sqlite3.Row to tuples, stripping NUL bytes from strings
        # (PostgreSQL TEXT columns cannot contain NUL characters)
        tuples = [
            tuple(
                v.replace("\x00", "") if isinstance(v, str) else v
                for v in row
            )
            for row in rows
        ]
        psycopg2.extras.execute_values(
            pg_cursor, insert_sql, tuples, template=f"({placeholders})"
        )
        total_rows += len(tuples)
        elapsed = time.time() - t0
        rate = total_rows / elapsed if elapsed > 0 else 0
        print(f"  {pg_table}: {total_rows:>10,} rows  ({rate:,.0f} rows/s)", end="\r")

    pg_conn.commit()
    elapsed = time.time() - t0

    # Reset serial sequence if applicable
    if serial_col and serial_seq:
        pg_cursor.execute(
            f"SELECT setval('{serial_seq}', COALESCE(MAX({serial_col}), 1)) FROM {pg_table}"
        )
        pg_conn.commit()

    print(f"  {pg_table}: {total_rows:>10,} rows  ({elapsed:.1f}s)          ")
    return total_rows


def verify_counts(sqlite_conn, pg_conn, table_spec):
    """Verify row counts match between SQLite and PostgreSQL."""
    sqlite_table = table_spec["sqlite_table"]
    pg_table = table_spec["pg_table"]

    sqlite_count = sqlite_conn.execute(
        f"SELECT COUNT(*) FROM {sqlite_table}"
    ).fetchone()[0]

    pg_cursor = pg_conn.cursor()
    pg_cursor.execute(f"SELECT COUNT(*) FROM {pg_table}")
    pg_count = pg_cursor.fetchone()[0]

    match = "OK" if sqlite_count == pg_count else "MISMATCH"
    print(f"  {pg_table:25s}  SQLite: {sqlite_count:>10,}  PG: {pg_count:>10,}  [{match}]")
    return sqlite_count == pg_count


def main():
    parser = argparse.ArgumentParser(description="Migrate SQLite elog data to PostgreSQL")
    parser.add_argument("--sqlite", default=SQLITE_PATH, help="Path to SQLite database")
    parser.add_argument("--pg-host", default=PG_HOST, help="PostgreSQL host")
    parser.add_argument("--pg-port", type=int, default=PG_PORT, help="PostgreSQL port")
    parser.add_argument("--pg-db", default=PG_DB, help="PostgreSQL database name")
    args = parser.parse_args()

    print(f"SQLite source: {args.sqlite}")
    print(f"PostgreSQL target: {args.pg_db} on {args.pg_host}:{args.pg_port}")
    print()

    # Connect to SQLite
    sqlite_conn = sqlite3.connect(args.sqlite)
    sqlite_conn.row_factory = sqlite3.Row

    # Connect to PostgreSQL (trust auth, no password)
    pg_conn = psycopg2.connect(
        host=args.pg_host,
        port=args.pg_port,
        dbname=args.pg_db,
    )

    # Migrate tables in order
    print("=== Migrating tables ===")
    t_start = time.time()
    total = 0

    for table_spec in TABLES:
        count = migrate_table(sqlite_conn, pg_conn, table_spec)
        total += count

    t_total = time.time() - t_start
    print(f"\nTotal: {total:,} rows in {t_total:.1f}s ({total / t_total:,.0f} rows/s)")

    # Verify counts
    print("\n=== Verifying row counts ===")
    all_ok = True
    for table_spec in TABLES:
        if not verify_counts(sqlite_conn, pg_conn, table_spec):
            all_ok = False

    sqlite_conn.close()
    pg_conn.close()

    if all_ok:
        print("\nAll counts match. Migration successful!")
    else:
        print("\nWARNING: Some counts do not match!", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
