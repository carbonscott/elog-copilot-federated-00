-- 03_test_data.sql
-- Load test data from centralized SQLite into PostgreSQL
-- This file contains instructions and sample queries for data migration

-- ============================================================================
-- Data Source
-- ============================================================================

-- Centralized SQLite database:
--   /sdf/group/lcls/ds/dm/apps/dev/data/elog-copilot/elog_2026_0204_1800.db

-- Tables in source:
--   Experiment, Run, RunProductionData, Logbook, Detector, RunDetector,
--   Questionnaire, Workflow

-- ============================================================================
-- Migration Options
-- ============================================================================

-- Option 1: Use pgloader (recommended for large datasets)
--
--   pgloader sqlite:///path/to/elog.db postgresql://user@host/elog_prototype
--
-- Option 2: Use Python script with psycopg2
--
--   See scripts/migrate_sqlite_to_postgres.py (to be created)
--
-- Option 3: Manual CSV export/import
--
--   sqlite3 elog.db ".mode csv" ".headers on" "SELECT * FROM Experiment;" > experiments.csv
--   \copy experiments FROM 'experiments.csv' CSV HEADER;

-- ============================================================================
-- Sample Data for Testing (4 experiments)
-- ============================================================================

-- These experiments have data on disk and were used for DuckDB prototype testing:
--   mfx101232725, cxi101233025, xcs101220125, mfx101269525

-- Insert test experiments
INSERT INTO experiments (experiment_id, instrument, pi, description) VALUES
    ('mfx101232725', 'mfx', 'Test PI 1', 'MFX experiment for testing'),
    ('cxi101233025', 'cxi', 'Test PI 2', 'CXI experiment for testing'),
    ('xcs101220125', 'xcs', 'Test PI 3', 'XCS experiment for testing'),
    ('mfx101269525', 'mfx', 'Test PI 4', 'MFX experiment 2 for testing');

-- Insert test runs
INSERT INTO runs (experiment_id, run_number, start_time) VALUES
    ('mfx101232725', 1, '2025-12-01 10:00:00'),
    ('mfx101232725', 2, '2025-12-01 11:00:00'),
    ('cxi101233025', 1, '2025-12-15 09:00:00'),
    ('xcs101220125', 1, '2025-12-20 14:00:00'),
    ('mfx101269525', 1, '2026-01-10 08:00:00');

-- Insert test detectors
INSERT INTO detectors (detector_name, description) VALUES
    ('jungfrau4m', 'Jungfrau 4M detector'),
    ('epix10ka', 'ePix10ka detector'),
    ('rayonix', 'Rayonix detector');

-- Insert test user access (simulating different permission levels)
-- Staff user: access to all experiments
INSERT INTO user_experiment_access (username, experiment_id, access_level, granted_by) VALUES
    ('staff_user', 'mfx101232725', 'read', 'admin'),
    ('staff_user', 'cxi101233025', 'read', 'admin'),
    ('staff_user', 'xcs101220125', 'read', 'admin'),
    ('staff_user', 'mfx101269525', 'read', 'admin');

-- PI user: access to own experiments only
INSERT INTO user_experiment_access (username, experiment_id, access_level, granted_by) VALUES
    ('pi_user', 'mfx101232725', 'write', 'admin');

-- Collaborator: access to a few experiments
INSERT INTO user_experiment_access (username, experiment_id, access_level, granted_by) VALUES
    ('collab_user', 'mfx101232725', 'read', 'admin'),
    ('collab_user', 'cxi101233025', 'read', 'admin');

-- ============================================================================
-- Test Queries (after loading data)
-- ============================================================================

-- As staff_user (should see all 4 experiments):
--   SET ROLE staff_user;
--   SELECT experiment_id FROM experiments;
--   -- Expected: 4 rows

-- As pi_user (should see 1 experiment):
--   SET ROLE pi_user;
--   SELECT experiment_id FROM experiments;
--   -- Expected: 1 row (mfx101232725)

-- As collab_user (should see 2 experiments):
--   SET ROLE collab_user;
--   SELECT experiment_id FROM experiments;
--   -- Expected: 2 rows (mfx101232725, cxi101233025)

-- Cross-experiment query (RLS filters automatically):
--   SELECT experiment_id, COUNT(*) as run_count
--   FROM runs
--   GROUP BY experiment_id;
--   -- Returns only experiments the current user can access
