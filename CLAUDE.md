# PostgreSQL Elog-Copilot

A prototype for permission-gated elog access using PostgreSQL Row-Level Security (RLS).

## Quick Start

```bash
cd /sdf/data/lcls/ds/prj/prjdat21/results/cwang31/elog-copilot-postgres

# Connect to test database
pg_env/bin/psql -h /tmp -p 5434 -d elog_prototype

# Query (RLS automatically filters by user's permissions)
SELECT * FROM runs WHERE experiment_id = 'mfx101232725';
SELECT experiment_id, COUNT(*) FROM runs GROUP BY experiment_id;
```

## Project Structure

```
elog-copilot-postgres/
├── CLAUDE.md
├── docs/
│   ├── postgres-rls-design.md
│   └── 2026_0205_1727-progress.md
├── sql/
│   ├── 01_schema.sql
│   ├── 02_rls_policies.sql
│   ├── 03_test_data.sql
│   └── 04_test_users.sql
└── scripts/
    ├── setup_postgres.sh
    ├── migrate_sqlite_to_postgres.py
    └── verify_rls.sh
```

## Architecture

### Single Database with RLS

```
┌─────────────────────────────────────────────────────┐
│                 PostgreSQL Server                    │
│                                                     │
│  Tables: experiments, runs, logbook, detectors...   │
│  RLS Policies: Filter by user's experiment access   │
│                                                     │
│  User A (staff_user): sees all ~1842 experiments    │
│  User B (pi_user): sees 1 experiment                │
│  User C (collab_user): sees 5 experiments           │
│  (Same tables, different views based on role)       │
└─────────────────────────────────────────────────────┘
```

### Permission Model

```sql
-- Users see only experiments they have access to
CREATE POLICY experiment_access ON runs
    USING (experiment_id IN (
        SELECT experiment_id FROM user_experiment_access
        WHERE username = current_user
    ));
```

### Key Tables

| Table | Purpose |
|-------|---------|
| `experiments` | Experiment metadata (PI, dates, description) |
| `runs` | Run numbers, timestamps |
| `run_production_data` | Event counts, file sizes |
| `logbook` | Timestamped log entries |
| `questionnaire` | Proposal/configuration details |
| `user_experiment_access` | Maps users → experiments they can access |

## Key Differences from DuckDB Approach

| Aspect | DuckDB Federated | PostgreSQL RLS |
|--------|------------------|----------------|
| Data location | Per-experiment files | Centralized |
| Permission source | Filesystem ACLs | Database roles/policies |
| Query experience | Need ATTACH | Just query |
| CLI latency | ~7s for 1800 experiments | Instant |
| Scaling | Files per experiment | Single DB |
| Server required | No (but daemon helps) | Yes |

## Source Data

Centralized SQLite: `/sdf/group/lcls/ds/dm/apps/dev/data/elog-copilot/elog-copilot.db` (symlink to latest)

## Related Branch

DuckDB federated approach: `branch-b` in `../elog-copilot-federated-b/`

## Status

**Working prototype** - PG 16 running locally, ~4.85M rows migrated, RLS verified (15/15 tests).

## Others

**elog-copilot** infrastructure codes: 
- Path: `/sdf/group/lcls/ds/dm/apps/dev/tools/elog-copilot`
- It handles data fetching, and ingestion to sqlitedb.
