# Federated Elog-Copilot: Intern Handoff Document

**Author**: Cong Wang
**Date**: 2026-02-04
**Status**: Prototype Design

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Architecture Overview](#2-architecture-overview)
3. [Current System Analysis](#3-current-system-analysis)
4. [Proposed Data Distribution](#4-proposed-data-distribution)
5. [Query Layer Design](#5-query-layer-design)
6. [Starter Code](#6-starter-code)
7. [Prototype Tasks](#7-prototype-tasks)
8. [Directory Structure](#8-directory-structure)
9. [Open Questions](#9-open-questions)
10. [References](#10-references)

---

## 1. Problem Statement

### The Issue

The current elog-copilot system uses a **single centralized SQLite database** (~1.3GB) that all users query. However, at LCLS, different users have different permissions to access experiment data:

- User A may access `cxilz5418` and `cxi00123` (experiments they're part of)
- User B may access `mfx00456` only (their current experiment)
- User C may have facility-wide access (rare, typically admins)

**Important**: Permissions are **per-experiment**, not per-instrument. Having access to one MFX experiment does NOT grant access to other MFX experiments. Each experiment folder has its own ACL.

The centralized database **bypasses the filesystem permission model** that LCLS already uses to gate access to experiment folders.

### The Goal

Design a **federated database architecture** where:

1. Each experiment's data lives in a SQLite file within that experiment's folder
2. Filesystem ACLs (already configured per-experiment) naturally gate database access
3. A query layer dynamically attaches only the databases the user can read
4. Users can still run cross-experiment queries (within their permission scope)

### Why This Matters

- **Security**: Leverage existing POSIX ACLs instead of building custom auth
- **Compliance**: Respect experiment-level data access policies
- **Simplicity**: No application-level permission logic needed
- **Auditability**: Filesystem access logs track who queried what

---

## 2. Architecture Overview

### Current Architecture (Centralized)

```
┌─────────────────────────────────────────────────────┐
│                   All Users                          │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│           elog-copilot.db (1.3GB)                   │
│                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │Experiment│ │   Run    │ │ Logbook  │  ...       │
│  │ (1,841)  │ │(443,083) │ │(710,108) │            │
│  └──────────┘ └──────────┘ └──────────┘            │
│                                                      │
│  ALL experiment data in ONE file                    │
│  NO permission checks                                │
└─────────────────────────────────────────────────────┘
```

### Proposed Architecture (Federated)

```
┌─────────────────────────────────────────────────────┐
│                   User Query                         │
│         "SELECT * FROM runs WHERE exp='cxilz5418'"  │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│              Query Layer (DuckDB)                    │
│                                                      │
│  1. Parse SQL → identify experiments needed          │
│  2. Lookup paths in master.db                        │
│  3. ATTACH each experiment DB (filesystem gates)     │
│  4. Execute query across attached DBs                │
│  5. DETACH and return results                        │
└─────────────────────┬───────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        │             │             │
        ▼             ▼             ▼
┌───────────┐  ┌───────────┐  ┌───────────┐
│ master.db │  │           │  │           │
│ (central) │  │           │  │           │
│           │  │           │  │           │
│ - Index   │  │           │  │           │
│ - Schema  │  │           │  │           │
│ - Catalog │  │           │  │           │
└───────────┘  │           │  │           │
               │           │  │           │
               ▼           ▼  ▼           ▼
┌─────────────────────────────────────────────────────┐
│          Experiment Databases (per-folder)           │
│                                                      │
│  /sdf/data/lcls/ds/cxi/cxilz5418/.elog/elog.db     │
│  /sdf/data/lcls/ds/mfx/mfx00123/.elog/elog.db      │
│  /sdf/data/lcls/ds/xpp/xpp12345/.elog/elog.db      │
│  ...                                                 │
│                                                      │
│  Each inherits folder's ACL permissions!             │
└─────────────────────────────────────────────────────┘
```

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Query Engine | **DuckDB** | No ATTACH limit (SQLite max 125), native SQLite extension |
| Hierarchy | **2-tier** (Master + Experiment) | Simpler than 3-tier, sufficient for per-experiment permissions |
| DB Location | `<exp_folder>/.elog/elog.db` | Hidden folder, inherits parent ACLs |
| Master Location | Central deploy directory | Readable by all, contains only index data |

**Key Insight**: Permissions are per-experiment, not per-instrument. User access to `mfx00123` does not imply access to `mfx00456`. Each experiment folder's ACL independently controls who can read its `.elog/elog.db`.

---

## 3. Current System Analysis

### 3.1 Database Schema

The current elog-copilot database has **9 tables**:

```sql
-- Core experiment metadata
Experiment (
    experiment_id TEXT PRIMARY KEY,  -- e.g., 'cxilz5418'
    name TEXT,
    instrument TEXT,                  -- e.g., 'CXI', 'MFX'
    start_time DATETIME,
    end_time DATETIME,
    pi TEXT,                          -- Principal Investigator
    pi_email TEXT,
    leader_account TEXT,
    description TEXT,
    slack_channels TEXT,
    analysis_queues TEXT,
    urawi_proposal TEXT
)

-- Run information
Run (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_number INTEGER NOT NULL,
    experiment_id TEXT NOT NULL,      -- FK → Experiment
    start_time DATETIME,
    end_time DATETIME,
    UNIQUE(run_number, experiment_id)
)

-- Production data per run
RunProductionData (
    run_data_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,          -- FK → Run
    n_events INTEGER,
    n_damaged INTEGER,
    n_dropped INTEGER,
    prod_start DATETIME,
    prod_end DATETIME,
    number_of_files INTEGER,
    total_size_bytes INTEGER
)

-- Detector catalog (shared)
Detector (
    detector_id INTEGER PRIMARY KEY AUTOINCREMENT,
    detector_name TEXT NOT NULL,
    description TEXT,
    UNIQUE(detector_name)
)

-- Detector status per run
RunDetector (
    run_detector_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,          -- FK → Run
    detector_id INTEGER NOT NULL,     -- FK → Detector
    status TEXT NOT NULL,
    UNIQUE(run_id, detector_id)
)

-- Logbook entries
Logbook (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,      -- FK → Experiment
    run_id INTEGER,                   -- FK → Run (nullable)
    timestamp DATETIME NOT NULL,
    content TEXT,
    tags TEXT,
    author TEXT
)

-- Proposal questionnaire
Questionnaire (
    questionnaire_id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,      -- FK → Experiment
    proposal TEXT,
    category TEXT NOT NULL,
    field_id TEXT NOT NULL,
    field_name TEXT,
    field_value TEXT,
    modified_time DATETIME,
    modified_uid TEXT,
    created_time DATETIME,
    UNIQUE(experiment_id, field_id)
)

-- Workflow definitions
Workflow (
    workflow_id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,      -- FK → Experiment
    mongo_id TEXT,
    name TEXT NOT NULL,
    executable TEXT,
    trigger TEXT,
    location TEXT,
    parameters TEXT,                  -- JSON
    run_param_name TEXT,
    run_param_value TEXT,
    run_as_user TEXT
)

-- System metadata
Metadata (
    key TEXT PRIMARY KEY,
    value TEXT
)
```

### 3.2 Data Volumes

| Table | Rows | Notes |
|-------|------|-------|
| Experiment | 1,841 | All LCLS experiments |
| Run | 443,083 | ~240 runs per experiment |
| RunProductionData | 443,083 | 1:1 with Run |
| Detector | 484 | Shared catalog |
| **RunDetector** | **3,133,943** | **Largest table** (~7 detectors/run) |
| Logbook | 710,108 | ~386 entries per experiment |
| Questionnaire | 109,251 | ~59 fields per experiment |
| Workflow | 2,651 | ~1.4 per experiment |
| Metadata | 2 | `last_update`, `hours_lookback` |

**Total**: ~4.7 million rows, 1.3GB

### 3.3 Data Sources

The `elogfetch` tool pulls from 7 API endpoints at `https://pswww.slac.stanford.edu`:

| Data | Endpoint | Auth |
|------|----------|------|
| Experiment list | `/ws/lgbk/lgbk/ws/experiment_names_updated_within` | Public |
| Experiment info | `/ws-kerb/lgbk/lgbk/{exp}/ws/info` | Kerberos |
| Logbook | `/ws-kerb/lgbk/lgbk/{exp}/ws/elog` | Kerberos |
| Run table | `/ws-kerb/lgbk/lgbk/{exp}/ws/runs/{run}` | Kerberos |
| File manager | `/ws-kerb/lgbk/lgbk/{exp}/ws/files` | Kerberos |
| Questionnaire | `/ws-kerb/questionnaire/ws/proposal/...` | Kerberos |
| Workflows | `/ws-kerb/lgbk/lgbk/{exp}/ws/workflow_definitions` | Kerberos |

---

## 4. Proposed Data Distribution

### Master Database (Central, Public)

**Location**: `/sdf/group/lcls/ds/dm/apps/dev/data/elog-copilot-federated/master.db`

**Purpose**: Discovery index only, no sensitive data

```sql
-- Experiment discovery (public info only)
CREATE TABLE experiment_index (
    experiment_id TEXT PRIMARY KEY,
    instrument TEXT NOT NULL,
    name TEXT,
    start_time DATETIME,
    end_time DATETIME,
    db_path TEXT NOT NULL           -- Path to experiment's elog.db
);

-- Shared detector catalog
CREATE TABLE detector_catalog (
    detector_id INTEGER PRIMARY KEY,
    detector_name TEXT UNIQUE NOT NULL,
    description TEXT
);

-- Schema version for migrations
CREATE TABLE schema_info (
    version INTEGER PRIMARY KEY,
    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Sync metadata
CREATE TABLE sync_metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);
```

**What's NOT in master**: PI names, emails, logbook content, questionnaire details

### Experiment Database (Per-Folder, Permission-Gated)

**Location**: `/sdf/data/lcls/ds/<hutch>/<experiment>/.elog/elog.db`

**Example**: `/sdf/data/lcls/ds/cxi/cxilz5418/.elog/elog.db`

```sql
-- Full experiment details
CREATE TABLE experiment (
    experiment_id TEXT PRIMARY KEY,
    name TEXT,
    instrument TEXT,
    start_time DATETIME,
    end_time DATETIME,
    pi TEXT,
    pi_email TEXT,
    leader_account TEXT,
    description TEXT,
    slack_channels TEXT,
    analysis_queues TEXT,
    urawi_proposal TEXT
);

-- Runs (same schema as current)
CREATE TABLE runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_number INTEGER NOT NULL UNIQUE,
    start_time DATETIME,
    end_time DATETIME
);

-- Run production data
CREATE TABLE run_production_data (
    run_data_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    n_events INTEGER,
    n_damaged INTEGER,
    n_dropped INTEGER,
    prod_start DATETIME,
    prod_end DATETIME,
    number_of_files INTEGER,
    total_size_bytes INTEGER
);

-- Run-detector mapping (references master's detector_catalog)
CREATE TABLE run_detectors (
    run_detector_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    detector_id INTEGER NOT NULL,  -- FK to master.detector_catalog
    status TEXT NOT NULL,
    UNIQUE(run_id, detector_id)
);

-- Logbook entries
CREATE TABLE logbook (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES runs(run_id),
    timestamp DATETIME NOT NULL,
    content TEXT,
    tags TEXT,
    author TEXT
);

-- Questionnaire
CREATE TABLE questionnaire (
    questionnaire_id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal TEXT,
    category TEXT NOT NULL,
    field_id TEXT NOT NULL UNIQUE,
    field_name TEXT,
    field_value TEXT,
    modified_time DATETIME,
    modified_uid TEXT
);

-- Workflows
CREATE TABLE workflows (
    workflow_id INTEGER PRIMARY KEY AUTOINCREMENT,
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

-- Local metadata
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);
```

**Note**: `experiment_id` is implicit (one per DB), so tables don't need FK to Experiment.

---

## 5. Query Layer Design

### Core Concept

Use **DuckDB** as the query engine because:
1. No limit on attached databases (SQLite max is 125)
2. Native SQLite extension: `ATTACH 'file.db' (TYPE sqlite)`
3. Cross-database queries work seamlessly
4. Can also read Parquet files (useful for lcls-catalog integration)

### Query Flow

```
User SQL Query
      │
      ▼
┌─────────────────────────────────────┐
│  1. PARSE: Extract experiment IDs   │
│     from WHERE clauses, JOINs, etc. │
└─────────────────┬───────────────────┘
                  │
                  ▼
┌─────────────────────────────────────┐
│  2. LOOKUP: Get db_path from        │
│     master.experiment_index         │
└─────────────────┬───────────────────┘
                  │
                  ▼
┌─────────────────────────────────────┐
│  3. ATTACH: For each experiment     │
│     - Check filesystem access       │
│     - DuckDB ATTACH if accessible   │
│     - Skip if permission denied     │
└─────────────────┬───────────────────┘
                  │
                  ▼
┌─────────────────────────────────────┐
│  4. REWRITE: Transform query to     │
│     use attached schema names       │
│     e.g., exp_cxilz5418.runs        │
└─────────────────┬───────────────────┘
                  │
                  ▼
┌─────────────────────────────────────┐
│  5. EXECUTE: Run rewritten query    │
│     DuckDB handles cross-DB joins   │
└─────────────────┬───────────────────┘
                  │
                  ▼
┌─────────────────────────────────────┐
│  6. DETACH: Clean up attached DBs   │
│     Return results to user          │
└─────────────────────────────────────┘
```

### Permission Check Strategy

**Option A: Try and Catch** (Recommended for prototype)
```python
try:
    conn.execute(f"ATTACH '{db_path}' AS {exp_id} (TYPE sqlite)")
    attached.append(exp_id)
except Exception as e:
    # Permission denied or file not found
    skipped.append((exp_id, str(e)))
```

**Option B: Pre-check with os.access**
```python
if os.access(db_path, os.R_OK):
    conn.execute(f"ATTACH '{db_path}' AS {exp_id} (TYPE sqlite)")
```

Option A is simpler and handles edge cases (file exists but unreadable, etc.).

---

## 6. Starter Code

### 6.1 Query Layer Skeleton

```python
#!/usr/bin/env python3
"""
federated_elog.py - Federated Elog Query Layer

This module provides a query interface that dynamically attaches
experiment databases based on user permissions (filesystem ACLs).
"""

import duckdb
import os
import re
from pathlib import Path
from dataclasses import dataclass
from typing import List, Tuple, Optional, Any


@dataclass
class QueryResult:
    """Result of a federated query."""
    data: List[Tuple]
    columns: List[str]
    attached_experiments: List[str]
    skipped_experiments: List[Tuple[str, str]]  # (exp_id, reason)


class FederatedElog:
    """
    Federated query layer for elog-copilot databases.

    Uses DuckDB to dynamically attach per-experiment SQLite databases,
    leveraging filesystem permissions for access control.
    """

    def __init__(self, master_db_path: str):
        """
        Initialize the federated query layer.

        Args:
            master_db_path: Path to the master index database
        """
        self.master_db_path = Path(master_db_path)
        if not self.master_db_path.exists():
            raise FileNotFoundError(f"Master database not found: {master_db_path}")

        # Create DuckDB connection and load SQLite extension
        self.conn = duckdb.connect(":memory:")
        self.conn.execute("INSTALL sqlite; LOAD sqlite;")

        # Attach master database
        self.conn.execute(f"ATTACH '{self.master_db_path}' AS master (TYPE sqlite)")

        # Track attached experiment databases
        self._attached: List[str] = []

    def list_experiments(self, instrument: Optional[str] = None) -> List[dict]:
        """
        List available experiments from master index.

        Args:
            instrument: Optional filter by instrument (e.g., 'CXI', 'MFX')

        Returns:
            List of experiment records
        """
        sql = "SELECT * FROM master.experiment_index"
        if instrument:
            sql += f" WHERE instrument = '{instrument}'"
        sql += " ORDER BY start_time DESC"

        result = self.conn.execute(sql).fetchall()
        columns = [desc[0] for desc in self.conn.description]
        return [dict(zip(columns, row)) for row in result]

    def _extract_experiments(self, sql: str) -> List[str]:
        """
        Extract experiment IDs mentioned in a SQL query.

        This is a simplified parser - production version should use
        proper SQL parsing (e.g., sqlparse library).

        Args:
            sql: The SQL query string

        Returns:
            List of experiment IDs found in the query
        """
        # Pattern: experiment_id = 'xxx' or experiment = 'xxx' or exp = 'xxx'
        pattern = r"(?:experiment_id|experiment|exp)\s*=\s*['\"]([^'\"]+)['\"]"
        matches = re.findall(pattern, sql, re.IGNORECASE)

        # Also check for IN clauses
        in_pattern = r"(?:experiment_id|experiment|exp)\s+IN\s*\(([^)]+)\)"
        in_matches = re.findall(in_pattern, sql, re.IGNORECASE)
        for match in in_matches:
            # Parse comma-separated values
            values = re.findall(r"['\"]([^'\"]+)['\"]", match)
            matches.extend(values)

        return list(set(matches))

    def _get_experiment_paths(self, experiment_ids: List[str]) -> List[Tuple[str, str]]:
        """
        Look up database paths for given experiment IDs.

        Args:
            experiment_ids: List of experiment IDs

        Returns:
            List of (experiment_id, db_path) tuples
        """
        if not experiment_ids:
            return []

        placeholders = ", ".join(f"'{eid}'" for eid in experiment_ids)
        sql = f"""
            SELECT experiment_id, db_path
            FROM master.experiment_index
            WHERE experiment_id IN ({placeholders})
        """
        return self.conn.execute(sql).fetchall()

    def _attach_experiments(self, experiment_paths: List[Tuple[str, str]]) -> Tuple[List[str], List[Tuple[str, str]]]:
        """
        Attach experiment databases, respecting filesystem permissions.

        Args:
            experiment_paths: List of (experiment_id, db_path) tuples

        Returns:
            Tuple of (attached_ids, skipped_with_reasons)
        """
        attached = []
        skipped = []

        for exp_id, db_path in experiment_paths:
            # Skip if already attached
            if exp_id in self._attached:
                attached.append(exp_id)
                continue

            # Try to attach - filesystem permissions will gate access
            try:
                self.conn.execute(f"ATTACH '{db_path}' AS exp_{exp_id} (TYPE sqlite)")
                self._attached.append(exp_id)
                attached.append(exp_id)
            except Exception as e:
                error_msg = str(e)
                if "Permission denied" in error_msg or "unable to open" in error_msg.lower():
                    skipped.append((exp_id, "Permission denied"))
                else:
                    skipped.append((exp_id, error_msg))

        return attached, skipped

    def _detach_experiments(self, experiment_ids: Optional[List[str]] = None):
        """
        Detach experiment databases.

        Args:
            experiment_ids: Specific IDs to detach, or None for all
        """
        ids_to_detach = experiment_ids or self._attached.copy()
        for exp_id in ids_to_detach:
            if exp_id in self._attached:
                try:
                    self.conn.execute(f"DETACH exp_{exp_id}")
                    self._attached.remove(exp_id)
                except Exception:
                    pass  # Ignore detach errors

    def query(self, sql: str, auto_detach: bool = True) -> QueryResult:
        """
        Execute a federated query across experiment databases.

        Args:
            sql: SQL query (can reference experiment tables)
            auto_detach: Whether to detach databases after query

        Returns:
            QueryResult with data and metadata
        """
        # Extract experiment IDs from query
        experiment_ids = self._extract_experiments(sql)

        # Look up paths in master
        experiment_paths = self._get_experiment_paths(experiment_ids)

        # Attach databases (permission check happens here)
        attached, skipped = self._attach_experiments(experiment_paths)

        # Execute query
        try:
            result = self.conn.execute(sql)
            data = result.fetchall()
            columns = [desc[0] for desc in result.description] if result.description else []
        except Exception as e:
            if auto_detach:
                self._detach_experiments(attached)
            raise

        # Clean up
        if auto_detach:
            self._detach_experiments(attached)

        return QueryResult(
            data=data,
            columns=columns,
            attached_experiments=attached,
            skipped_experiments=skipped
        )

    def query_experiment(self, experiment_id: str, sql: str) -> QueryResult:
        """
        Query a single experiment database.

        Convenience method that attaches one experiment and runs a query.

        Args:
            experiment_id: The experiment to query
            sql: SQL query (tables without schema prefix)

        Returns:
            QueryResult
        """
        # Get path
        paths = self._get_experiment_paths([experiment_id])
        if not paths:
            raise ValueError(f"Experiment not found: {experiment_id}")

        # Attach
        attached, skipped = self._attach_experiments(paths)
        if skipped:
            raise PermissionError(f"Cannot access experiment: {skipped[0][1]}")

        # Rewrite SQL to use schema prefix
        prefixed_sql = sql
        for table in ['runs', 'logbook', 'questionnaire', 'workflows',
                      'run_production_data', 'run_detectors', 'experiment']:
            prefixed_sql = re.sub(
                rf'\b{table}\b',
                f'exp_{experiment_id}.{table}',
                prefixed_sql,
                flags=re.IGNORECASE
            )

        # Execute
        try:
            result = self.conn.execute(prefixed_sql)
            data = result.fetchall()
            columns = [desc[0] for desc in result.description] if result.description else []
        finally:
            self._detach_experiments([experiment_id])

        return QueryResult(
            data=data,
            columns=columns,
            attached_experiments=attached,
            skipped_experiments=skipped
        )

    def close(self):
        """Clean up resources."""
        self._detach_experiments()
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# Example usage
if __name__ == "__main__":
    # This is for testing with dummy data
    master_path = "data/master.db"

    with FederatedElog(master_path) as elog:
        # List all experiments
        experiments = elog.list_experiments()
        print(f"Found {len(experiments)} experiments in index")

        # Query a specific experiment
        try:
            result = elog.query_experiment(
                "cxilz5418",
                "SELECT run_number, start_time FROM runs ORDER BY run_number LIMIT 10"
            )
            print(f"Runs: {result.data}")
        except PermissionError as e:
            print(f"Access denied: {e}")
```

### 6.2 Schema Setup Script

```python
#!/usr/bin/env python3
"""
setup_schemas.py - Create master and experiment database schemas
"""

import sqlite3
from pathlib import Path


MASTER_SCHEMA = """
-- Experiment discovery index (public)
CREATE TABLE IF NOT EXISTS experiment_index (
    experiment_id TEXT PRIMARY KEY,
    instrument TEXT NOT NULL,
    name TEXT,
    start_time TEXT,
    end_time TEXT,
    db_path TEXT NOT NULL
);

-- Shared detector catalog
CREATE TABLE IF NOT EXISTS detector_catalog (
    detector_id INTEGER PRIMARY KEY,
    detector_name TEXT UNIQUE NOT NULL,
    description TEXT
);

-- Schema version
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER PRIMARY KEY,
    applied_at TEXT DEFAULT (datetime('now'))
);

-- Sync metadata
CREATE TABLE IF NOT EXISTS sync_metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Insert schema version
INSERT OR REPLACE INTO schema_info (version) VALUES (1);
"""


EXPERIMENT_SCHEMA = """
-- Experiment details
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

-- Runs
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_number INTEGER NOT NULL UNIQUE,
    start_time TEXT,
    end_time TEXT
);

-- Run production data
CREATE TABLE IF NOT EXISTS run_production_data (
    run_data_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    n_events INTEGER,
    n_damaged INTEGER,
    n_dropped INTEGER,
    prod_start TEXT,
    prod_end TEXT,
    number_of_files INTEGER,
    total_size_bytes INTEGER
);

-- Run-detector mapping
CREATE TABLE IF NOT EXISTS run_detectors (
    run_detector_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    detector_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    UNIQUE(run_id, detector_id)
);

-- Logbook
CREATE TABLE IF NOT EXISTS logbook (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES runs(run_id),
    timestamp TEXT NOT NULL,
    content TEXT,
    tags TEXT,
    author TEXT
);

-- Questionnaire
CREATE TABLE IF NOT EXISTS questionnaire (
    questionnaire_id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal TEXT,
    category TEXT NOT NULL,
    field_id TEXT NOT NULL UNIQUE,
    field_name TEXT,
    field_value TEXT,
    modified_time TEXT,
    modified_uid TEXT
);

-- Workflows
CREATE TABLE IF NOT EXISTS workflows (
    workflow_id INTEGER PRIMARY KEY AUTOINCREMENT,
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

-- Local metadata
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_runs_number ON runs(run_number);
CREATE INDEX IF NOT EXISTS idx_logbook_run ON logbook(run_id);
CREATE INDEX IF NOT EXISTS idx_logbook_timestamp ON logbook(timestamp);
"""


def create_master_db(path: Path):
    """Create master database with schema."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(MASTER_SCHEMA)
    conn.commit()
    conn.close()
    print(f"Created master database: {path}")


def create_experiment_db(path: Path, experiment_id: str):
    """Create experiment database with schema."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(EXPERIMENT_SCHEMA)
    # Insert experiment_id into metadata
    conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('experiment_id', ?)",
                 (experiment_id,))
    conn.commit()
    conn.close()
    print(f"Created experiment database: {path}")


if __name__ == "__main__":
    # Create dummy data structure for testing
    base = Path("data")

    # Master DB
    create_master_db(base / "master.db")

    # Experiment DBs
    experiments = [
        ("cxi", "cxilz5418"),
        ("cxi", "cxi00123"),
        ("mfx", "mfx00456"),
    ]

    for hutch, exp_id in experiments:
        exp_path = base / "experiments" / hutch / exp_id / ".elog" / "elog.db"
        create_experiment_db(exp_path, exp_id)

        # Register in master
        conn = sqlite3.connect(base / "master.db")
        conn.execute("""
            INSERT OR REPLACE INTO experiment_index
            (experiment_id, instrument, name, db_path)
            VALUES (?, ?, ?, ?)
        """, (exp_id, hutch.upper(), exp_id, str(exp_path)))
        conn.commit()
        conn.close()

    print("\nDummy data structure created!")
```

---

## 7. Prototype Tasks

### Phase 1: Foundation (Week 1-2)

- [ ] **Task 1.1**: Set up development environment
  - Install DuckDB: `pip install duckdb`
  - Create prototype directory structure
  - Run `setup_schemas.py` to create dummy databases

- [ ] **Task 1.2**: Implement basic query layer
  - Copy `federated_elog.py` skeleton
  - Test with dummy data
  - Verify ATTACH/DETACH works correctly

- [ ] **Task 1.3**: Test permission gating
  - Create experiment DBs with different file permissions
  - Verify queries fail gracefully for inaccessible DBs
  - Document error messages

### Phase 2: Data Migration (Week 3-4)

- [ ] **Task 2.1**: Write migration script
  - Read from current centralized DB
  - Split data by experiment_id
  - Write to per-experiment DBs
  - Update master index

- [ ] **Task 2.2**: Test with subset of real data
  - Migrate 10-20 experiments
  - Verify data integrity
  - Compare query results with centralized DB

- [ ] **Task 2.3**: Handle edge cases
  - Experiments with no runs
  - Experiments with very large logbooks
  - Detector catalog synchronization

### Phase 3: Integration (Week 5-6)

- [ ] **Task 3.1**: Integrate with elogfetch
  - Modify elogfetch to write per-experiment DBs
  - Add master index update logic
  - Test incremental updates

- [ ] **Task 3.2**: Cross-experiment queries
  - Implement `query_multiple_experiments()` method
  - Handle UNION ALL generation
  - Test with instrument-wide queries

- [ ] **Task 3.3**: Performance testing
  - Benchmark query latency
  - Test with many attached DBs
  - Identify bottlenecks

### Phase 4: Production Readiness (Week 7-8)

- [ ] **Task 4.1**: Error handling & logging
  - Add comprehensive error messages
  - Log permission denials (for audit)
  - Handle network/filesystem failures

- [ ] **Task 4.2**: Documentation
  - User guide for query syntax
  - Admin guide for deployment
  - API documentation

- [ ] **Task 4.3**: Integration testing
  - Test with real experiment folders
  - Verify ACL enforcement
  - Performance under load

---

## 8. Directory Structure

### Prototype Directory Layout

```
/sdf/data/lcls/ds/prj/prjdat21/results/cwang31/elog-copilot-federated/
├── HANDOFF.md                    # This document
├── src/
│   ├── federated_elog.py         # Query layer implementation
│   ├── setup_schemas.py          # Schema creation script
│   ├── migrate_data.py           # Data migration script (to create)
│   └── __init__.py
├── tests/
│   ├── test_query_layer.py       # Unit tests
│   ├── test_permissions.py       # Permission gating tests
│   └── conftest.py               # Pytest fixtures
├── data/                         # Dummy data for testing
│   ├── master.db                 # Master index DB
│   └── experiments/              # Mimics /sdf/data/lcls/ds/
│       ├── cxi/
│       │   ├── cxilz5418/
│       │   │   └── .elog/
│       │   │       └── elog.db
│       │   └── cxi00123/
│       │       └── .elog/
│       │           └── elog.db
│       └── mfx/
│           └── mfx00456/
│               └── .elog/
│                   └── elog.db
├── scripts/
│   ├── create_dummy_data.sh      # Generate test data
│   └── test_permissions.sh       # Test permission scenarios
└── pyproject.toml                # Project configuration
```

### Production Directory Layout (Target)

```
# Central (readable by all)
/sdf/group/lcls/ds/dm/apps/dev/data/elog-copilot-federated/
└── master.db                     # Master index only

# Per-experiment (inherits folder ACLs)
/sdf/data/lcls/ds/<hutch>/<experiment>/.elog/
└── elog.db                       # Experiment-specific data

# Examples:
/sdf/data/lcls/ds/cxi/cxilz5418/.elog/elog.db
/sdf/data/lcls/ds/mfx/mfx00123/.elog/elog.db
/sdf/data/lcls/ds/xpp/xpp12345/.elog/elog.db
```

---

## 9. Open Questions

These are decisions for you to explore during the prototype:

### Architecture

1. **Detector catalog sync**: Should detector_catalog be in master (shared) or replicated to each experiment DB? Consider query patterns.

2. **Schema versioning**: How to handle schema migrations across 1,800+ experiment databases?

3. **Caching**: Should the query layer cache experiment paths? How to invalidate?

### Data Management

4. **Incremental updates**: How to detect which experiment DBs need updating? Timestamp comparison? Hash?

5. **Initial migration**: Migrate all 1,841 experiments at once, or phase by instrument/time?

6. **Storage overhead**: Is 1,841 separate SQLite files acceptable? (Current: 1 file × 1.3GB vs. 1,841 files × ~700KB each ≈ 1.3GB total)

### Query Layer

7. **SQL parsing**: Use regex (simple) or proper parser like `sqlparse` (robust)?

8. **Cross-experiment aggregations**: How to handle `SELECT COUNT(*) FROM all_runs`? Pre-compute in master?

9. **Error reporting**: When a user can't access 5 of 10 requested experiments, how verbose should the error be?

### Operations

10. **Fallback**: If federated query fails, should it fall back to centralized DB?

11. **Monitoring**: How to track query patterns, permission denials, performance?

12. **Cleanup**: How to handle experiments that are archived/deleted?

---

## 10. References

### Current System

- **Elog-copilot tools**: `/sdf/group/lcls/ds/dm/apps/dev/tools/elog-copilot/`
- **Centralized DB**: `/sdf/group/lcls/ds/dm/apps/dev/data/elog-copilot/elog-copilot.db`
- **API docs**: `https://pswww.slac.stanford.edu/ws/lgbk/` (internal)

### Technologies

- **DuckDB docs**: https://duckdb.org/docs/
- **DuckDB SQLite extension**: https://duckdb.org/docs/extensions/sqlite
- **SQLite ATTACH**: https://www.sqlite.org/lang_attach.html

### Industry Examples

- **Turso (database-per-tenant)**: https://turso.tech/multi-tenancy
- **ayb (multi-tenant SQLite)**: https://blog.marcua.net/2023/06/25/ayb-a-multi-tenant-database-that-helps-you-own-your-data.html
- **HPC ACL management**: https://hpc.dccn.nl/docs/project_storage/access_management.html

### Contact

Questions about this project: Cong Wang (cwang31@slac.stanford.edu)

---

*Last updated: 2026-02-04*
