# PostgreSQL RLS Design for Elog-Copilot

**Date:** 2026-02-05
**Status:** Design phase

## Overview

This document describes the design for using PostgreSQL Row-Level Security (RLS) to provide permission-gated access to LCLS elog data.

## Why PostgreSQL RLS?

From our exploration of DuckDB federated approach:
- ATTACH latency: ~7s for 1800 experiments
- No persistent session in CLI
- Would need a daemon/server pattern anyway
- If we need a server, PostgreSQL is proven and has native RLS

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                 PostgreSQL Server                    │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │              Data Tables                     │   │
│  │  experiments, runs, logbook, questionnaire   │   │
│  └─────────────────────────────────────────────┘   │
│                       │                             │
│                       ▼                             │
│  ┌─────────────────────────────────────────────┐   │
│  │           RLS Policies                       │   │
│  │  Filter rows by user's experiment access     │   │
│  └─────────────────────────────────────────────┘   │
│                       │                             │
│           ┌───────────┼───────────┐                 │
│           ▼           ▼           ▼                 │
│        User A      User B      User C               │
│      (1800 exp)   (3 exp)    (50 exp)              │
└─────────────────────────────────────────────────────┘
```

## Schema Design

### Core Tables (from existing SQLite schema)

```sql
-- Experiment metadata
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

-- Runs within experiments
CREATE TABLE runs (
    run_id SERIAL PRIMARY KEY,
    experiment_id TEXT REFERENCES experiments(experiment_id),
    run_number INTEGER NOT NULL,
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    UNIQUE(experiment_id, run_number)
);

-- Run production data
CREATE TABLE run_production_data (
    run_data_id SERIAL PRIMARY KEY,
    run_id INTEGER REFERENCES runs(run_id),
    n_events BIGINT,
    n_damaged BIGINT,
    n_dropped BIGINT,
    prod_start TIMESTAMPTZ,
    prod_end TIMESTAMPTZ,
    number_of_files INTEGER,
    total_size_bytes BIGINT
);

-- Logbook entries
CREATE TABLE logbook (
    log_id SERIAL PRIMARY KEY,
    experiment_id TEXT REFERENCES experiments(experiment_id),
    run_id INTEGER REFERENCES runs(run_id),
    timestamp TIMESTAMPTZ NOT NULL,
    content TEXT,
    tags TEXT,
    author TEXT
);

-- Detector catalog (shared)
CREATE TABLE detectors (
    detector_id SERIAL PRIMARY KEY,
    detector_name TEXT UNIQUE NOT NULL,
    description TEXT
);

-- Run-detector associations
CREATE TABLE run_detectors (
    run_detector_id SERIAL PRIMARY KEY,
    run_id INTEGER REFERENCES runs(run_id),
    detector_id INTEGER REFERENCES detectors(detector_id),
    status TEXT NOT NULL,
    UNIQUE(run_id, detector_id)
);
```

### Permission Table

```sql
-- Maps users to experiments they can access
CREATE TABLE user_experiment_access (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    experiment_id TEXT REFERENCES experiments(experiment_id),
    access_level TEXT DEFAULT 'read',  -- 'read', 'write', 'admin'
    granted_at TIMESTAMPTZ DEFAULT now(),
    granted_by TEXT,
    UNIQUE(username, experiment_id)
);

-- Index for fast permission lookups
CREATE INDEX idx_user_access_username ON user_experiment_access(username);
CREATE INDEX idx_user_access_experiment ON user_experiment_access(experiment_id);
```

## Row-Level Security Policies

### Enable RLS on all tables

```sql
ALTER TABLE experiments ENABLE ROW LEVEL SECURITY;
ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_production_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE logbook ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_detectors ENABLE ROW LEVEL SECURITY;
```

### Policy Definitions

```sql
-- Experiments: user sees experiments they have access to
CREATE POLICY experiment_access ON experiments
    FOR SELECT
    USING (experiment_id IN (
        SELECT experiment_id FROM user_experiment_access
        WHERE username = current_user
    ));

-- Runs: inherit from experiment access
CREATE POLICY run_access ON runs
    FOR SELECT
    USING (experiment_id IN (
        SELECT experiment_id FROM user_experiment_access
        WHERE username = current_user
    ));

-- Run production data: inherit from run access
CREATE POLICY run_prod_access ON run_production_data
    FOR SELECT
    USING (run_id IN (
        SELECT r.run_id FROM runs r
        JOIN user_experiment_access ua ON r.experiment_id = ua.experiment_id
        WHERE ua.username = current_user
    ));

-- Logbook: inherit from experiment access
CREATE POLICY logbook_access ON logbook
    FOR SELECT
    USING (experiment_id IN (
        SELECT experiment_id FROM user_experiment_access
        WHERE username = current_user
    ));

-- Run detectors: inherit from run access
CREATE POLICY run_detector_access ON run_detectors
    FOR SELECT
    USING (run_id IN (
        SELECT r.run_id FROM runs r
        JOIN user_experiment_access ua ON r.experiment_id = ua.experiment_id
        WHERE ua.username = current_user
    ));

-- Detectors catalog: public (no RLS)
-- Everyone can see the detector catalog
```

### Admin Bypass

```sql
-- Create admin role that bypasses RLS
CREATE ROLE elog_admin;
ALTER TABLE experiments FORCE ROW LEVEL SECURITY;
-- Admins use: SET ROLE elog_admin; to bypass

-- Or create policy for admins
CREATE POLICY admin_all ON experiments
    FOR ALL
    TO elog_admin
    USING (true);
```

## Permission Sync Strategy

### Option 1: Direct mapping from Unix groups

```sql
-- Sync script reads /etc/group or LDAP
-- For each experiment, check filesystem permissions
-- Insert into user_experiment_access

-- Example: User 'cwang31' is in group 'ps-data'
-- Experiment mfx101232725 has group 'ps-data'
-- → INSERT INTO user_experiment_access (username, experiment_id) VALUES ('cwang31', 'mfx101232725');
```

### Option 2: LDAP/AD integration

```sql
-- Use pg_ldap_sync or similar to sync users/groups
-- Map LDAP groups to experiment access
```

### Option 3: Application-level sync

```python
# Python script that:
# 1. Reads experiment directories
# 2. Checks group ownership
# 3. Maps groups to users
# 4. Updates user_experiment_access table
```

## Query Examples

### User queries - RLS is transparent

```sql
-- User just queries normally
-- RLS automatically filters to their experiments

SELECT experiment_id, COUNT(*) as runs
FROM runs
GROUP BY experiment_id;
-- Returns only experiments user has access to

SELECT * FROM logbook
WHERE content LIKE '%alignment%'
ORDER BY timestamp DESC
LIMIT 10;
-- Only searches logbooks user can see
```

### Cross-experiment queries - just work

```sql
-- Find experiments using a specific detector
SELECT DISTINCT e.experiment_id, e.pi
FROM experiments e
JOIN runs r ON e.experiment_id = r.experiment_id
JOIN run_detectors rd ON r.run_id = rd.run_id
JOIN detectors d ON rd.detector_id = d.detector_id
WHERE d.detector_name LIKE '%jungfrau%';
-- Automatically limited to user's accessible experiments
```

## Performance Considerations

### Indexes for RLS

```sql
-- Ensure fast permission lookups
CREATE INDEX idx_runs_experiment ON runs(experiment_id);
CREATE INDEX idx_logbook_experiment ON logbook(experiment_id);
CREATE INDEX idx_run_prod_run ON run_production_data(run_id);
CREATE INDEX idx_run_det_run ON run_detectors(run_id);
```

### Query plan with RLS

```sql
EXPLAIN ANALYZE
SELECT * FROM runs WHERE experiment_id = 'mfx101232725';
-- Should show index scan, not sequential scan
-- RLS adds a subquery filter but should be efficient with indexes
```

## Open Questions

1. **PostgreSQL hosting:** Use existing LCLS Postgres or new instance?

2. **User authentication:** How do users authenticate to Postgres?
   - Unix socket with peer auth?
   - Password auth?
   - Kerberos/GSSAPI?

3. **Permission sync frequency:** How often to sync Unix→Postgres permissions?
   - Real-time (via triggers)?
   - Periodic (cron job)?
   - On-demand?

4. **Write access:** How to handle elog writes?
   - Separate write policies?
   - Application-level auth?

5. **Data sync:** How to keep Postgres in sync with authoritative elog source?
   - One-time migration?
   - Continuous replication?
   - Postgres becomes authoritative?

## Next Steps

1. [ ] Set up test PostgreSQL instance
2. [ ] Create schema and load test data
3. [ ] Test RLS policies with different users
4. [ ] Benchmark query performance
5. [ ] Design permission sync mechanism
