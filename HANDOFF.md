# Elog-Copilot with PostgreSQL RLS: Handoff Document

**Author**: Cong Wang
**Date**: 2026-02-05
**Status**: Design Phase (pivot from DuckDB federated approach)

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Architecture Overview](#2-architecture-overview)
3. [Current System Analysis](#3-current-system-analysis)
4. [PostgreSQL Schema Design](#4-postgresql-schema-design)
5. [Query Experience](#5-query-experience)
6. [Approaches Explored](#6-approaches-explored)
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

Design a **permission-gated query system** using PostgreSQL Row-Level Security (RLS) where:

1. All experiment data lives in a single PostgreSQL database
2. RLS policies automatically filter rows based on the querying user's permissions
3. Users query normally — permission filtering is transparent
4. Cross-experiment queries work naturally within the user's permission scope

### Why This Matters

- **Security**: Permission enforcement at the database level, not application level
- **Compliance**: Respect experiment-level data access policies
- **Simplicity**: Users just query — no ATTACH, no wrappers, no special syntax
- **Performance**: Instant queries (no per-session database setup latency)
- **Proven**: PostgreSQL RLS is battle-tested in production systems

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

### Proposed Architecture (PostgreSQL with RLS)

```
┌─────────────────────────────────────────────────────┐
│                   User Query                         │
│  "SELECT * FROM runs WHERE experiment_id='cxi...'"  │
│  (no ATTACH, no wrappers, just SQL)                 │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│              PostgreSQL Server                       │
│                                                      │
│  ┌─────────────────────────────────────────────┐    │
│  │              Data Tables                     │    │
│  │  experiments, runs, logbook, questionnaire   │    │
│  │  run_production_data, run_detectors, ...     │    │
│  └─────────────────────────────────────────────┘    │
│                       │                              │
│                       ▼                              │
│  ┌─────────────────────────────────────────────┐    │
│  │         Row-Level Security Policies          │    │
│  │  Each table: USING (experiment_id IN (       │    │
│  │    SELECT experiment_id                      │    │
│  │    FROM user_experiment_access               │    │
│  │    WHERE username = current_user))           │    │
│  └─────────────────────────────────────────────┘    │
│                       │                              │
│           ┌───────────┼───────────┐                  │
│           ▼           ▼           ▼                  │
│        User A      User B      User C               │
│      (1800 exp)   (3 exp)    (50 exp)               │
│      Same query → different rows returned            │
└─────────────────────────────────────────────────────┘
```

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Database Engine | **PostgreSQL** | Native RLS, proven at scale, standard tooling |
| Permission Model | **RLS policies** | Transparent to users, enforced at DB level |
| Data Layout | **Single centralized DB** | No ATTACH complexity, standard SQL |
| Permission Source | **`user_experiment_access` table** | Synced from filesystem ACLs or LDAP |
| Auth | **TBD** | Peer auth, Kerberos, or password — open question |

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

## 4. PostgreSQL Schema Design

### Overview

All experiment data lives in a single PostgreSQL database. Row-Level Security policies on each table ensure users only see experiments they have access to.

Full schema definitions: **`sql/01_schema.sql`**
Full RLS policies: **`sql/02_rls_policies.sql`**

### Core Tables

The PostgreSQL schema mirrors the existing SQLite schema with minor adjustments:

```sql
CREATE TABLE experiments (
    experiment_id TEXT PRIMARY KEY,
    name TEXT,
    instrument TEXT,
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    pi TEXT,
    pi_email TEXT,
    leader_account TEXT,
    description TEXT
);

CREATE TABLE runs (
    run_id SERIAL PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    run_number INTEGER NOT NULL,
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    UNIQUE(experiment_id, run_number)
);

-- See sql/01_schema.sql for complete definitions of:
-- run_production_data, logbook, detectors, run_detectors,
-- questionnaire, workflows
```

### Permission Table

The key addition to enable RLS:

```sql
CREATE TABLE user_experiment_access (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    access_level TEXT DEFAULT 'read',  -- 'read', 'write', 'admin'
    granted_at TIMESTAMPTZ DEFAULT now(),
    granted_by TEXT,
    UNIQUE(username, experiment_id)
);

-- Indexes for RLS performance
CREATE INDEX idx_user_access_username ON user_experiment_access(username);
CREATE INDEX idx_user_access_experiment ON user_experiment_access(experiment_id);
```

### RLS Policy Pattern

Every sensitive table gets the same policy pattern:

```sql
ALTER TABLE experiments ENABLE ROW LEVEL SECURITY;
ALTER TABLE experiments FORCE ROW LEVEL SECURITY;

CREATE POLICY experiment_select ON experiments
    FOR SELECT
    USING (experiment_id IN (
        SELECT experiment_id FROM user_experiment_access
        WHERE username = current_user
    ));
```

For tables without a direct `experiment_id` column (like `run_production_data`), the policy joins through `runs`:

```sql
CREATE POLICY run_prod_select ON run_production_data
    FOR SELECT
    USING (run_id IN (
        SELECT r.run_id FROM runs r
        JOIN user_experiment_access ua ON r.experiment_id = ua.experiment_id
        WHERE ua.username = current_user
    ));
```

The `detectors` table has **no RLS** — it's a shared catalog visible to all users.

---

## 5. Query Experience

### Users Just Query

With PostgreSQL RLS, there is no setup step. Users connect and query:

```sql
-- Cross-experiment: automatically limited to user's accessible experiments
SELECT experiment_id, COUNT(*) as run_count
FROM runs
GROUP BY experiment_id;

-- Search logbooks (only searches user's accessible experiments)
SELECT * FROM logbook
WHERE content LIKE '%alignment%'
ORDER BY timestamp DESC
LIMIT 10;

-- Join across tables (RLS applied on each table independently)
SELECT DISTINCT e.experiment_id, e.pi
FROM experiments e
JOIN runs r ON e.experiment_id = r.experiment_id
JOIN run_detectors rd ON r.run_id = rd.run_id
JOIN detectors d ON rd.detector_id = d.detector_id
WHERE d.detector_name LIKE '%jungfrau%';
```

### Comparison with DuckDB Approach

| Aspect | DuckDB Federated | PostgreSQL RLS |
|--------|------------------|----------------|
| Setup per session | ATTACH ~7s for 1800 DBs | None |
| Query syntax | Schema-prefixed (`exp_cxi.runs`) | Standard (`runs`) |
| Cross-experiment query | Manual UNION ALL or views | Just GROUP BY |
| Permission errors | Try/catch per ATTACH | Silent filtering (empty result) |
| CLI experience | Wrapper script needed | `psql` works directly |
| Persistent state | None (CLI) or daemon needed | PostgreSQL server |

---

## 6. Approaches Explored

Before settling on PostgreSQL RLS, we explored several approaches. This section documents each for historical reference.

### Summary

| # | Approach | Status | Key Finding | Why Not (or Why Chosen) |
|---|----------|--------|-------------|-------------------------|
| 1 | DuckDB Federated | Prototyped | Works end-to-end | ~7s ATTACH for 1800 DBs, no persistent CLI session |
| 2 | DuckDB Views | Tested | Views are stored queries, not data | Require ATTACH each session — same latency problem |
| 3 | CLI Dynamic Wrapper | Prototyped | `.bail off` + `os.access()` filtering | Not persistent, regenerates each invocation |
| 4 | Per-user SQLite | Analyzed | Simple once built | ~71GB storage for 1000 users, stale permission revocation |
| 5 | DuckDB Daemon | Considered | Would solve persistent session | Over-engineering; if need server, just use Postgres |
| 6 | **PostgreSQL RLS** | **Chosen** | Native permission filtering, instant queries | Requires server infrastructure |

### Approach 1: DuckDB Federated (per-experiment files)

**Concept**: Each experiment gets its own DuckDB file at `/sdf/data/lcls/ds/<hutch>/<exp>/.elog-metadata/elog.duckdb`. Filesystem ACLs gate access. A master index tracks all experiment paths. At query time, ATTACH only accessible databases.

**What worked**:
- Permission gating via filesystem ACLs confirmed working
- Cross-experiment queries via UNION ALL and JOINs across schemas
- Graceful error handling when experiments are inaccessible
- 4 real experiments deployed and tested successfully

**What didn't scale**:
- ATTACH latency: ~7s for 1800 databases (even with parallel ATTACH due to internal locking)
- No persistent session in DuckDB CLI — must re-ATTACH every invocation
- `os.access()` checks are fast (~0.03s for 1800) but the ATTACH step is the bottleneck

**Code**: `src/federated_elog.py`, `src/elog_extractor.py`, `src/master_builder.py` (on `branch-b`)
**Docs**: `docs/2026-02-04-candidate-experiments-research.md` (on `branch-b`)

### Approach 2: DuckDB Views

**Concept**: Create VIEWs in a DuckDB file that aggregate across experiments, e.g., `CREATE VIEW all_runs AS SELECT * FROM exp_cxi.runs UNION ALL SELECT * FROM exp_mfx.runs`.

**What we found**:
- Views are stored queries, not materialized data
- The underlying schemas must be ATTACHed each session for views to work
- If ANY referenced experiment is inaccessible, the entire view fails (no graceful partial results)

**Conclusion**: Views don't solve the ATTACH problem — they just defer it.

**Docs**: `docs/2026-02-04-unified-query-layer-discussion.md` (on `branch-b`)

### Approach 3: CLI Dynamic Wrapper

**Concept**: A shell script that (1) runs `os.access()` on all experiment paths, (2) generates an init.sql that ATTACHes only accessible ones and creates VIEWs, (3) runs user query via `./bin/duckdb -init init.sql`.

**What worked**: Functional for ad-hoc one-off queries.

**Limitations**: Regenerates init.sql each invocation (full permission scan + ATTACH latency each time).

**Key DuckDB features discovered**: `.bail off` continues after ATTACH errors; `grep -v "^IO Error:"` suppresses permission error output.

**Docs**: `docs/2026-02-04-cli-dynamic-view-solution.md` (on `branch-b`)

### Approach 4: Per-user SQLite

**Concept**: Materialize a per-user SQLite database containing only the experiments each user can access. Simple queries with no permission logic at query time.

**Analysis**:
- Storage: ~71GB total (950 normal users x 10 experiments x 700KB + 50 power users x 1841 x 700KB)
- Permission revocation is stale until next sync
- 1000 users x 1841 experiments = 1.8M permission checks for full rebuild

**Conclusion**: Storage is manageable but permission revocation staleness is a compliance concern, and the generation/sync complexity is high.

**Docs**: `docs/2026-02-05-per-user-sqlite-analysis.md`, `docs/2026-02-05-0950-per-user-sqlite-pivot-discussion.md` (on `branch-b`)

### Approach 5: DuckDB Daemon (Unix Socket)

**Concept**: Run a persistent DuckDB process that keeps databases ATTACHed, serving queries via Unix socket. Avoids re-ATTACH latency.

**Why rejected**: If we need a persistent server process, PostgreSQL is a much more proven and capable solution with native RLS, replication, monitoring, and tooling. Building a custom daemon is over-engineering.

### Approach 6: PostgreSQL RLS (Chosen)

**Concept**: Single PostgreSQL database with all experiment data. Row-Level Security policies filter rows based on `current_user`'s entries in `user_experiment_access` table.

**Why chosen**:
- Instant queries (no setup latency)
- Transparent permission filtering (users just query normally)
- Standard tooling (psql, any PostgreSQL client, BI tools)
- Battle-tested security model
- Built-in auth options (peer, Kerberos, LDAP)

**Trade-off**: Requires PostgreSQL server infrastructure (hosting, backups, monitoring).

---

## 7. Prototype Tasks

### Phase 1: Infrastructure (Week 1-2)

- [ ] **Task 1.1**: Identify PostgreSQL hosting
  - Evaluate existing LCLS PostgreSQL instances
  - Determine if new instance is needed
  - Decide on authentication method

- [ ] **Task 1.2**: Create database and load schema
  - Run `sql/01_schema.sql` to create tables
  - Run `sql/02_rls_policies.sql` to enable RLS
  - Create test users

- [ ] **Task 1.3**: Migrate data from SQLite
  - Write migration script (SQLite → PostgreSQL)
  - Source: `/sdf/group/lcls/ds/dm/apps/dev/data/elog-copilot/elog_2026_0204_1800.db`
  - Verify data integrity post-migration

### Phase 2: RLS Validation (Week 3-4)

- [ ] **Task 2.1**: Test RLS with different user roles
  - Staff user (access to all experiments)
  - PI user (access to own experiments)
  - Collaborator (access to subset)
  - Unauthenticated (no access)

- [ ] **Task 2.2**: Test cross-experiment queries
  - GROUP BY experiment_id (should only show accessible)
  - JOINs across tables (RLS on each table independently)
  - Full-text search in logbook (only accessible entries)

- [ ] **Task 2.3**: Benchmark query performance
  - Single experiment queries with/without RLS
  - Cross-experiment aggregations
  - Ensure indexes support RLS policy subqueries

### Phase 3: Permission Sync (Week 5-6)

- [ ] **Task 3.1**: Design permission sync mechanism
  - Map filesystem ACLs to `user_experiment_access` entries
  - Evaluate: `os.access()` probing vs LDAP group mapping vs API
  - Define sync frequency (real-time vs nightly vs on-demand)

- [ ] **Task 3.2**: Implement permission sync script
  - Read experiment directory permissions
  - Map to usernames
  - Upsert into `user_experiment_access`
  - Handle permission revocations

### Phase 4: Integration (Week 7-8)

- [ ] **Task 4.1**: Data sync from elog source
  - Incremental updates from elogfetch
  - Handle new experiments, new runs, logbook entries
  - Conflict resolution strategy

- [ ] **Task 4.2**: Integration testing
  - End-to-end: user connects → queries → sees correct data
  - Permission changes propagate correctly
  - Performance under concurrent users

- [ ] **Task 4.3**: Documentation
  - User guide (how to connect and query)
  - Admin guide (sync, monitoring, troubleshooting)
  - Migration guide from centralized SQLite

---

## 8. Directory Structure

### This Worktree

```
elog-copilot-postgres/                     # Git worktree on branch-postgres
├── CLAUDE.md                              # Project instructions
├── HANDOFF.md                             # This document
├── docs/
│   ├── postgres-rls-design.md             # Detailed RLS design
│   └── duckdb-native-migration.md         # Historical (from branch-b)
├── sql/
│   ├── 01_schema.sql                      # PostgreSQL table definitions
│   ├── 02_rls_policies.sql                # RLS policy definitions
│   └── 03_test_data.sql                   # Sample data + migration notes
├── scripts/
│   └── setup.sh                           # Setup instructions
└── src/                                   # Historical (from branch-b)
    ├── create_prototype_data.py           # DuckDB extractor (not used)
    └── federated_elog.py                  # DuckDB query layer (not used)
```

### Related Worktree (DuckDB exploration)

```
../elog-copilot-federated-b/               # Git worktree on branch-b
├── src/
│   ├── elog_extractor.py                  # Single experiment → DuckDB
│   ├── master_builder.py                  # Master index builder
│   └── federated_elog.py                  # DuckDB query layer
├── scripts/
│   └── run_real_test.sh                   # Orchestration
├── docs/                                  # Exploration documentation
│   ├── 2026-02-04-candidate-experiments-research.md
│   ├── 2026-02-04-unified-query-layer-discussion.md
│   ├── 2026-02-04-cli-dynamic-view-solution.md
│   ├── 2026-02-05-per-user-sqlite-analysis.md
│   └── ...
└── test_real_experiments.py               # DuckDB integration tests
```

---

## 9. Open Questions

### Infrastructure

1. **PostgreSQL hosting**: Use an existing LCLS PostgreSQL instance or provision a new one?

2. **Authentication**: How do users authenticate to the database?
   - Unix socket with peer auth (requires local access)?
   - Password auth (credential management)?
   - Kerberos/GSSAPI (aligns with existing LCLS auth)?

3. **Backup and HA**: What's the recovery strategy?

### Permission Model

4. **Permission sync mechanism**: How to populate `user_experiment_access`?
   - Probe filesystem ACLs (`os.access()` for each user x experiment)?
   - Query LDAP/AD group membership?
   - Use existing permission API?

5. **Sync frequency**: How often to sync permissions?
   - Real-time (trigger-based)?
   - Periodic (cron)?
   - On-demand (user-initiated)?

6. **Permission revocation**: When a user loses access, how quickly must it take effect?

### Data Management

7. **Write access**: Should users be able to write through PostgreSQL, or read-only (writes go through elogfetch)?

8. **Data freshness**: How to keep PostgreSQL in sync with the authoritative elog source?
   - One-time migration with PostgreSQL as new authoritative source?
   - Continuous replication from upstream?
   - Periodic batch sync?

9. **Data locality**: Is it important that experiment data lives in the experiment directory (on-disk alongside the data)?
   - If yes, PostgreSQL is a complement, not replacement
   - If no, PostgreSQL can be the sole data store

---

## 10. References

### Current System

- **Elog-copilot tools**: `/sdf/group/lcls/ds/dm/apps/dev/tools/elog-copilot/`
- **Centralized DB**: `/sdf/group/lcls/ds/dm/apps/dev/data/elog-copilot/elog-copilot.db`
- **API docs**: `https://pswww.slac.stanford.edu/ws/lgbk/` (internal)

### PostgreSQL RLS

- **RLS documentation**: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
- **RLS tutorial**: https://www.postgresql.org/docs/current/sql-createpolicy.html
- **Multi-tenant RLS patterns**: https://www.postgresql.org/docs/current/ddl-rowsecurity.html#DDL-ROWSECURITY-1

### DuckDB Exploration (historical)

- **DuckDB docs**: https://duckdb.org/docs/
- **DuckDB SQLite extension**: https://duckdb.org/docs/extensions/sqlite
- **Exploration docs**: See `branch-b` worktree at `../elog-copilot-federated-b/docs/`

### Industry Examples

- **Turso (database-per-tenant)**: https://turso.tech/multi-tenancy
- **PostgreSQL RLS patterns**: https://supabase.com/docs/guides/database/postgres/row-level-security

### Contact

Questions about this project: Cong Wang (cwang31@slac.stanford.edu)

---

*Last updated: 2026-02-05*
