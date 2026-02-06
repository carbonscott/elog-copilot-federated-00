-- 01_schema.sql
-- PostgreSQL schema for elog-copilot with RLS support
-- Matches SQLite schema from: elog_2026_0204_1800.db

-- ============================================================================
-- Core Tables
-- ============================================================================

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
    description TEXT,
    slack_channels TEXT,
    analysis_queues TEXT,
    urawi_proposal TEXT
);

-- Runs within experiments
CREATE TABLE runs (
    run_id SERIAL PRIMARY KEY,
    run_number INTEGER NOT NULL,
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    UNIQUE(run_number, experiment_id)
);

-- Run production data (event counts, file sizes)
CREATE TABLE run_production_data (
    run_data_id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
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
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    run_id INTEGER REFERENCES runs(run_id),
    timestamp TIMESTAMPTZ NOT NULL,
    content TEXT,
    tags TEXT,
    author TEXT
);

-- ============================================================================
-- Detector Tables
-- ============================================================================

-- Detector catalog (shared across all experiments)
CREATE TABLE detectors (
    detector_id SERIAL PRIMARY KEY,
    detector_name TEXT UNIQUE NOT NULL,
    description TEXT
);

-- Run-detector associations
CREATE TABLE run_detectors (
    run_detector_id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    detector_id INTEGER NOT NULL REFERENCES detectors(detector_id),
    status TEXT NOT NULL,
    UNIQUE(run_id, detector_id)
);

-- ============================================================================
-- Questionnaire (proposal/configuration details)
-- ============================================================================

CREATE TABLE questionnaire (
    questionnaire_id SERIAL PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    proposal TEXT,
    category TEXT NOT NULL,
    field_id TEXT NOT NULL,
    field_name TEXT,
    field_value TEXT,
    modified_time TIMESTAMPTZ,
    modified_uid TEXT,
    created_time TIMESTAMPTZ DEFAULT now(),
    UNIQUE(experiment_id, field_id)
);

-- ============================================================================
-- Workflows (analysis workflow definitions)
-- ============================================================================

CREATE TABLE workflows (
    workflow_id SERIAL PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    mongo_id TEXT,
    name TEXT NOT NULL,
    executable TEXT,
    "trigger" TEXT,
    location TEXT,
    parameters TEXT,
    run_param_name TEXT,
    run_param_value TEXT,
    run_as_user TEXT
);

-- ============================================================================
-- Permission Table (for RLS)
-- ============================================================================

-- Maps users to experiments they can access
CREATE TABLE user_experiment_access (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    access_level TEXT DEFAULT 'read',  -- 'read', 'write', 'admin'
    granted_at TIMESTAMPTZ DEFAULT now(),
    granted_by TEXT,
    UNIQUE(username, experiment_id)
);

-- ============================================================================
-- Indexes for Performance
-- ============================================================================

-- Permission lookups (critical for RLS performance)
CREATE INDEX idx_user_access_username ON user_experiment_access(username);
CREATE INDEX idx_user_access_experiment ON user_experiment_access(experiment_id);

-- Foreign key joins
CREATE INDEX idx_runs_experiment ON runs(experiment_id);
CREATE INDEX idx_logbook_experiment ON logbook(experiment_id);
CREATE INDEX idx_logbook_timestamp ON logbook(timestamp);
CREATE INDEX idx_run_prod_run ON run_production_data(run_id);
CREATE INDEX idx_run_det_run ON run_detectors(run_id);
CREATE INDEX idx_questionnaire_experiment ON questionnaire(experiment_id);
CREATE INDEX idx_questionnaire_category ON questionnaire(category);
CREATE INDEX idx_questionnaire_proposal ON questionnaire(proposal);
CREATE INDEX idx_workflows_experiment ON workflows(experiment_id);

-- ============================================================================
-- Convenience View (like SQLite's RunCompleteData)
-- ============================================================================

CREATE VIEW run_complete_data AS
SELECT
    r.run_id,
    r.experiment_id,
    r.run_number,
    r.start_time,
    r.end_time,
    rpd.n_events,
    rpd.n_damaged,
    rpd.n_dropped,
    rpd.prod_start,
    rpd.prod_end,
    rpd.number_of_files,
    rpd.total_size_bytes
FROM runs r
LEFT JOIN run_production_data rpd ON r.run_id = rpd.run_id;
