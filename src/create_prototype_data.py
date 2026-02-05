#!/usr/bin/env python3
"""
create_prototype_data.py - Extract data from centralized DB and split into experiment DBs

This creates the federated structure for testing:
- master.db: Index with experiment paths
- Per-experiment DBs: One SQLite file per experiment
"""

import sqlite3
from pathlib import Path
from typing import List, Tuple

# Source database
SOURCE_DB = "/sdf/group/lcls/ds/dm/apps/dev/data/elog-copilot/elog_2026_0202_2031.db"

# Test experiments
TEST_EXPERIMENTS = [
    ("cxi", "cxi25410"),
    ("cxi", "cxilx8720"),
    ("mfx", "mfxlt3017"),  # Will be made unreadable
    ("xpp", "xppn3816"),
]

# Base paths
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"


MASTER_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiment_index (
    experiment_id TEXT PRIMARY KEY,
    instrument TEXT NOT NULL,
    name TEXT,
    start_time TEXT,
    end_time TEXT,
    db_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS detector_catalog (
    detector_id INTEGER PRIMARY KEY,
    detector_name TEXT UNIQUE NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER PRIMARY KEY,
    applied_at TEXT DEFAULT (datetime('now'))
);

INSERT OR REPLACE INTO schema_info (version) VALUES (1);
"""


EXPERIMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiment (
    experiment_id TEXT PRIMARY KEY,
    name TEXT,
    instrument TEXT,
    start_time TEXT,
    end_time TEXT,
    pi TEXT,
    pi_email TEXT,
    leader_account TEXT,
    description TEXT,
    slack_channels TEXT,
    analysis_queues TEXT,
    urawi_proposal TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY,
    run_number INTEGER NOT NULL UNIQUE,
    start_time TEXT,
    end_time TEXT
);

CREATE TABLE IF NOT EXISTS run_production_data (
    run_data_id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    n_events INTEGER,
    n_damaged INTEGER,
    n_dropped INTEGER,
    prod_start TEXT,
    prod_end TEXT,
    number_of_files INTEGER,
    total_size_bytes INTEGER
);

CREATE TABLE IF NOT EXISTS run_detectors (
    run_detector_id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    detector_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    UNIQUE(run_id, detector_id)
);

CREATE TABLE IF NOT EXISTS logbook (
    log_id INTEGER PRIMARY KEY,
    run_id INTEGER REFERENCES runs(run_id),
    timestamp TEXT NOT NULL,
    content TEXT,
    tags TEXT,
    author TEXT
);

CREATE TABLE IF NOT EXISTS questionnaire (
    questionnaire_id INTEGER PRIMARY KEY,
    proposal TEXT,
    category TEXT NOT NULL,
    field_id TEXT NOT NULL UNIQUE,
    field_name TEXT,
    field_value TEXT,
    modified_time TEXT,
    modified_uid TEXT
);

CREATE TABLE IF NOT EXISTS workflows (
    workflow_id INTEGER PRIMARY KEY,
    mongo_id TEXT,
    name TEXT NOT NULL,
    executable TEXT,
    trigger TEXT,
    location TEXT,
    parameters TEXT,
    run_param_name TEXT,
    run_param_value TEXT,
    run_as_user TEXT
);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_number ON runs(run_number);
CREATE INDEX IF NOT EXISTS idx_logbook_run ON logbook(run_id);
CREATE INDEX IF NOT EXISTS idx_logbook_timestamp ON logbook(timestamp);
"""


def create_master_db(experiments: List[Tuple[str, str]]) -> Path:
    """Create master index database."""
    master_path = DATA_DIR / "master.db"
    master_path.parent.mkdir(parents=True, exist_ok=True)

    # Remove if exists
    if master_path.exists():
        master_path.unlink()

    conn = sqlite3.connect(master_path)
    conn.executescript(MASTER_SCHEMA)

    # Copy detector catalog from source
    src_conn = sqlite3.connect(SOURCE_DB)
    detectors = src_conn.execute("SELECT detector_id, detector_name, description FROM Detector").fetchall()
    conn.executemany(
        "INSERT INTO detector_catalog (detector_id, detector_name, description) VALUES (?, ?, ?)",
        detectors
    )
    src_conn.close()

    # Add experiment index entries
    for hutch, exp_id in experiments:
        db_path = DATA_DIR / "experiments" / hutch / exp_id / ".elog" / "elog.db"
        conn.execute("""
            INSERT INTO experiment_index (experiment_id, instrument, db_path)
            VALUES (?, ?, ?)
        """, (exp_id, hutch.upper(), str(db_path)))

    conn.commit()
    conn.close()
    print(f"Created master database: {master_path}")
    return master_path


def create_experiment_db(hutch: str, exp_id: str) -> Path:
    """Extract data for one experiment and create its database."""
    exp_path = DATA_DIR / "experiments" / hutch / exp_id / ".elog" / "elog.db"
    exp_path.parent.mkdir(parents=True, exist_ok=True)

    # Remove if exists
    if exp_path.exists():
        exp_path.unlink()

    # Create schema
    conn = sqlite3.connect(exp_path)
    conn.executescript(EXPERIMENT_SCHEMA)

    # Connect to source
    src = sqlite3.connect(SOURCE_DB)

    # 1. Copy experiment record
    exp_row = src.execute("""
        SELECT experiment_id, name, instrument, start_time, end_time,
               pi, pi_email, leader_account, description,
               slack_channels, analysis_queues, urawi_proposal
        FROM Experiment WHERE experiment_id = ?
    """, (exp_id,)).fetchone()

    if exp_row:
        conn.execute("""
            INSERT INTO experiment VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, exp_row)

    # 2. Copy runs (need to track run_id mapping since we're renumbering)
    runs = src.execute("""
        SELECT run_id, run_number, start_time, end_time
        FROM Run WHERE experiment_id = ?
        ORDER BY run_number
    """, (exp_id,)).fetchall()

    run_id_map = {}  # old_id -> new_id
    for old_id, run_num, start, end in runs:
        cursor = conn.execute("""
            INSERT INTO runs (run_number, start_time, end_time) VALUES (?, ?, ?)
        """, (run_num, start, end))
        run_id_map[old_id] = cursor.lastrowid

    # 3. Copy run production data
    for old_id, new_id in run_id_map.items():
        prod_data = src.execute("""
            SELECT n_events, n_damaged, n_dropped, prod_start, prod_end,
                   number_of_files, total_size_bytes
            FROM RunProductionData WHERE run_id = ?
        """, (old_id,)).fetchone()

        if prod_data:
            conn.execute("""
                INSERT INTO run_production_data
                (run_id, n_events, n_damaged, n_dropped, prod_start, prod_end,
                 number_of_files, total_size_bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (new_id,) + prod_data)

    # 4. Copy run detectors
    for old_id, new_id in run_id_map.items():
        detectors = src.execute("""
            SELECT detector_id, status FROM RunDetector WHERE run_id = ?
        """, (old_id,)).fetchall()

        conn.executemany("""
            INSERT INTO run_detectors (run_id, detector_id, status) VALUES (?, ?, ?)
        """, [(new_id, det_id, status) for det_id, status in detectors])

    # 5. Copy logbook entries
    logbook = src.execute("""
        SELECT l.run_id, l.timestamp, l.content, l.tags, l.author
        FROM Logbook l WHERE l.experiment_id = ?
    """, (exp_id,)).fetchall()

    for old_run_id, timestamp, content, tags, author in logbook:
        new_run_id = run_id_map.get(old_run_id) if old_run_id else None
        conn.execute("""
            INSERT INTO logbook (run_id, timestamp, content, tags, author)
            VALUES (?, ?, ?, ?, ?)
        """, (new_run_id, timestamp, content, tags, author))

    # 6. Copy questionnaire
    questionnaire = src.execute("""
        SELECT proposal, category, field_id, field_name, field_value,
               modified_time, modified_uid
        FROM Questionnaire WHERE experiment_id = ?
    """, (exp_id,)).fetchall()

    conn.executemany("""
        INSERT INTO questionnaire
        (proposal, category, field_id, field_name, field_value, modified_time, modified_uid)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, questionnaire)

    # 7. Copy workflows
    workflows = src.execute("""
        SELECT mongo_id, name, executable, trigger, location, parameters,
               run_param_name, run_param_value, run_as_user
        FROM Workflow WHERE experiment_id = ?
    """, (exp_id,)).fetchall()

    conn.executemany("""
        INSERT INTO workflows
        (mongo_id, name, executable, trigger, location, parameters,
         run_param_name, run_param_value, run_as_user)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, workflows)

    # 8. Add metadata
    conn.execute("INSERT INTO metadata (key, value) VALUES ('experiment_id', ?)", (exp_id,))

    conn.commit()

    # Report stats
    stats = {
        'runs': conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
        'logbook': conn.execute("SELECT COUNT(*) FROM logbook").fetchone()[0],
        'detectors': conn.execute("SELECT COUNT(*) FROM run_detectors").fetchone()[0],
    }

    conn.close()
    src.close()

    print(f"Created {exp_id}: {stats['runs']} runs, {stats['logbook']} logs, {stats['detectors']} detector records")
    return exp_path


def main():
    print("Creating federated prototype data...")
    print(f"Source: {SOURCE_DB}")
    print(f"Target: {DATA_DIR}")
    print()

    # Create experiment databases
    print("Creating experiment databases:")
    for hutch, exp_id in TEST_EXPERIMENTS:
        create_experiment_db(hutch, exp_id)

    print()

    # Create master index
    create_master_db(TEST_EXPERIMENTS)

    print()
    print("Done! Data structure created.")
    print()
    print("Next step: Set permissions on mfxlt3017 to test ACL enforcement:")
    print(f"  chmod 000 {DATA_DIR}/experiments/mfx/mfxlt3017/.elog/elog.db")


if __name__ == "__main__":
    main()
