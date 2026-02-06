-- 04_test_users.sql
-- Create test users and populate permission entries for RLS testing
--
-- Prerequisites: Run after 01_schema.sql, 02_rls_policies.sql, and data migration

-- ============================================================================
-- Create test roles (trust auth — no passwords needed)
-- ============================================================================

-- Use DO block to avoid errors if roles already exist
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'staff_user') THEN
        CREATE ROLE staff_user LOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pi_user') THEN
        CREATE ROLE pi_user LOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'collab_user') THEN
        CREATE ROLE collab_user LOGIN;
    END IF;
END
$$;

-- ============================================================================
-- Grant read access to tables
-- ============================================================================

GRANT USAGE ON SCHEMA public TO staff_user, pi_user, collab_user;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO staff_user, pi_user, collab_user;

-- Also grant on future tables created in public schema
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO staff_user, pi_user, collab_user;

-- ============================================================================
-- Populate user_experiment_access
-- ============================================================================

-- Staff user: access to ALL experiments (~1842)
INSERT INTO user_experiment_access (username, experiment_id, access_level, granted_by)
SELECT 'staff_user', experiment_id, 'read', 'admin'
FROM experiments
ON CONFLICT (username, experiment_id) DO NOTHING;

-- PI user: access to 1 experiment
INSERT INTO user_experiment_access (username, experiment_id, access_level, granted_by)
VALUES ('pi_user', 'mfx101232725', 'write', 'admin')
ON CONFLICT (username, experiment_id) DO NOTHING;

-- Collaborator user: access to 5 experiments
INSERT INTO user_experiment_access (username, experiment_id, access_level, granted_by)
VALUES
    ('collab_user', 'mfx101232725', 'read', 'admin'),
    ('collab_user', 'cxi101233025', 'read', 'admin'),
    ('collab_user', 'xcs101220125', 'read', 'admin'),
    ('collab_user', 'mfx101269525', 'read', 'admin'),
    ('collab_user', 'amo01709',     'read', 'admin')
ON CONFLICT (username, experiment_id) DO NOTHING;

-- ============================================================================
-- Verify
-- ============================================================================

SELECT username, COUNT(*) AS experiment_count
FROM user_experiment_access
GROUP BY username
ORDER BY experiment_count DESC;
