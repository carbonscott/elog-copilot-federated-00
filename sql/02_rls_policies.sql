-- 02_rls_policies.sql
-- Row-Level Security policies for elog-copilot
-- Users see only experiments they have access to via user_experiment_access table

-- ============================================================================
-- Enable RLS on all tables with sensitive data
-- ============================================================================

ALTER TABLE experiments ENABLE ROW LEVEL SECURITY;
ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_production_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE logbook ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_detectors ENABLE ROW LEVEL SECURITY;
ALTER TABLE questionnaire ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflows ENABLE ROW LEVEL SECURITY;

-- Note: detectors table is NOT RLS-protected (shared catalog, public info)
-- Note: user_experiment_access is NOT RLS-protected (admin-managed)

-- ============================================================================
-- RLS Policies: SELECT access based on user_experiment_access
-- ============================================================================

-- Experiments: user sees experiments they have explicit access to
CREATE POLICY experiment_select ON experiments
    FOR SELECT
    USING (experiment_id IN (
        SELECT experiment_id FROM user_experiment_access
        WHERE username = current_user
    ));

-- Runs: inherit from experiment access
CREATE POLICY run_select ON runs
    FOR SELECT
    USING (experiment_id IN (
        SELECT experiment_id FROM user_experiment_access
        WHERE username = current_user
    ));

-- Run production data: inherit from run access (via experiment)
CREATE POLICY run_prod_select ON run_production_data
    FOR SELECT
    USING (run_id IN (
        SELECT r.run_id FROM runs r
        JOIN user_experiment_access ua ON r.experiment_id = ua.experiment_id
        WHERE ua.username = current_user
    ));

-- Logbook: inherit from experiment access
CREATE POLICY logbook_select ON logbook
    FOR SELECT
    USING (experiment_id IN (
        SELECT experiment_id FROM user_experiment_access
        WHERE username = current_user
    ));

-- Run detectors: inherit from run access (via experiment)
CREATE POLICY run_detector_select ON run_detectors
    FOR SELECT
    USING (run_id IN (
        SELECT r.run_id FROM runs r
        JOIN user_experiment_access ua ON r.experiment_id = ua.experiment_id
        WHERE ua.username = current_user
    ));

-- Questionnaire: inherit from experiment access
CREATE POLICY questionnaire_select ON questionnaire
    FOR SELECT
    USING (experiment_id IN (
        SELECT experiment_id FROM user_experiment_access
        WHERE username = current_user
    ));

-- Workflows: inherit from experiment access
CREATE POLICY workflow_select ON workflows
    FOR SELECT
    USING (experiment_id IN (
        SELECT experiment_id FROM user_experiment_access
        WHERE username = current_user
    ));

-- ============================================================================
-- Admin Role and Bypass
-- ============================================================================

-- Create admin role that can bypass RLS (used for migration and admin tasks)
CREATE ROLE elog_admin;

-- Force RLS even for table owners (prevents bypass)
ALTER TABLE experiments FORCE ROW LEVEL SECURITY;
ALTER TABLE runs FORCE ROW LEVEL SECURITY;
ALTER TABLE run_production_data FORCE ROW LEVEL SECURITY;
ALTER TABLE logbook FORCE ROW LEVEL SECURITY;
ALTER TABLE run_detectors FORCE ROW LEVEL SECURITY;
ALTER TABLE questionnaire FORCE ROW LEVEL SECURITY;
ALTER TABLE workflows FORCE ROW LEVEL SECURITY;

-- Admin can see and modify everything (WITH CHECK required for INSERT/UPDATE)
CREATE POLICY admin_experiments ON experiments FOR ALL TO elog_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_runs ON runs FOR ALL TO elog_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_run_prod ON run_production_data FOR ALL TO elog_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_logbook ON logbook FOR ALL TO elog_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_run_det ON run_detectors FOR ALL TO elog_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_questionnaire ON questionnaire FOR ALL TO elog_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_workflows ON workflows FOR ALL TO elog_admin USING (true) WITH CHECK (true);

-- Grant elog_admin to the current superuser so migration works with FORCE RLS
-- (The setup script creates the DB as the current OS user who is the superuser)
DO $$
BEGIN
    EXECUTE format('GRANT elog_admin TO %I', current_user);
END
$$;

-- ============================================================================
-- Usage Notes
-- ============================================================================

-- To grant a user access to an experiment:
--   INSERT INTO user_experiment_access (username, experiment_id, granted_by)
--   VALUES ('cwang31', 'mfx101232725', 'admin');

-- To revoke access:
--   DELETE FROM user_experiment_access
--   WHERE username = 'cwang31' AND experiment_id = 'mfx101232725';

-- To make a user an admin (bypasses RLS):
--   GRANT elog_admin TO username;

-- Users query normally - RLS is transparent:
--   SELECT * FROM runs WHERE experiment_id = 'mfx101232725';
--   (Returns rows only if user has access)
