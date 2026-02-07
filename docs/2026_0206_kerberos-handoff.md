# Handoff: Kerberos-Aware PostgreSQL Setup

**Date:** 2026-02-06
**Owner:** Chunhui Wang (cwang31)
**For:** Intern starting Kerberos integration work

## What This Project Is

We have a PostgreSQL database containing LCLS experiment elog data (~4.85M rows). It uses **Row-Level Security (RLS)** to filter query results per user — when you query "show me all experiments," you only see experiments you have access to.

Right now, the prototype uses **trust authentication** (anyone can claim to be anyone). Your job is to switch it to **Kerberos (GSSAPI) authentication**, so PostgreSQL verifies the user's identity through the existing SLAC Kerberos infrastructure.

**Read first:** `docs/2026_0205_technical-walkthrough.md` explains everything about the prototype in detail (PostgreSQL concepts, RLS, schema, migration). `docs/2026_0206_kerberos-auth.md` has the full Kerberos research.

## Where Everything Lives

```
/sdf/data/lcls/ds/prj/prjdat21/results/cwang31/elog-copilot-postgres/
├── pg_env/                  # PostgreSQL 16 binaries
├── pg_data/                 # Database data directory (config files live here)
│   ├── postgresql.conf      # Server config (port, memory, keytab path)
│   ├── pg_hba.conf          # Authentication rules (WHO can connect HOW)
│   └── pg_ident.conf        # Username mapping (Kerberos principal → PG role)
├── pg_logs/                 # Server logs
├── scripts/
│   ├── setup_postgres.sh    # Install/start/stop/status
│   ├── migrate_sqlite_to_postgres.py
│   └── verify_rls.sh        # 15 RLS tests
├── sql/
│   ├── 01_schema.sql        # Table definitions
│   ├── 02_rls_policies.sql  # RLS policies
│   └── 04_test_users.sql    # Test users (staff_user, pi_user, collab_user)
└── docs/                    # Documentation
```

## Current State

| Component | Status |
|-----------|--------|
| PostgreSQL 16 (with `--with-gssapi`) | Installed and running |
| Schema + data (~4.85M rows) | Loaded |
| RLS policies | Working (15/15 tests pass) |
| Authentication | Trust (no identity verification) |
| Test users | `staff_user` (all), `pi_user` (1 exp), `collab_user` (5 exp) |

## Environment Facts

| Fact | Value |
|------|-------|
| PostgreSQL host | `sdfiana025.sdf.slac.stanford.edu` |
| PostgreSQL port | `5434` |
| Database name | `elog_prototype` |
| PG binaries | `pg_env/bin/psql`, `pg_env/bin/pg_ctl`, etc. |
| Kerberos realm | `SLAC.STANFORD.EDU` |
| cwang31's principal | `cwang31@SLAC.STANFORD.EDU` |
| Kerberos ticket cache | `/tmp/krb5cc_*` (auto-created on SSH login) |
| GSSAPI build support | Confirmed (`pg_config --configure` shows `--with-gssapi`) |

Verify these yourself:

```bash
cd /sdf/data/lcls/ds/prj/prjdat21/results/cwang31/elog-copilot-postgres

# Check PG is running
pg_env/bin/pg_isready -h /tmp -p 5434

# Check GSSAPI support
pg_env/bin/pg_config --configure | tr ' ' '\n' | grep gss

# Check your Kerberos ticket
klist

# Check hostname
hostname -f
```

## Goal

After your work, a connection like this should work:

```bash
# No -U flag, no password. Kerberos ticket used automatically.
pg_env/bin/psql -h sdfiana025.sdf.slac.stanford.edu -p 5434 -d elog_prototype -c "SELECT current_user"
# → cwang31
```

And RLS should filter based on the verified identity:

```bash
pg_env/bin/psql -h sdfiana025.sdf.slac.stanford.edu -p 5434 -d elog_prototype -c "SELECT count(*) FROM experiments"
# → returns only experiments cwang31 has access to
```

## Step-by-Step Tasks

### Task 0: Verify the existing prototype works

Before changing anything, make sure the current setup is functional.

```bash
cd /sdf/data/lcls/ds/prj/prjdat21/results/cwang31/elog-copilot-postgres

# Start the server (if not running)
bash scripts/setup_postgres.sh start

# Run RLS tests
bash scripts/verify_rls.sh
# Expected: 15/15 pass

# Test a query
pg_env/bin/psql -h /tmp -p 5434 -d elog_prototype -c "SELECT count(*) FROM experiments"
```

### Task 1: Request service principal and keytab from IT

**This is a blocker — you cannot proceed without it.**

Ask the SLAC Kerberos administrators to create:

- **Service principal:** `postgres/sdfiana025.sdf.slac.stanford.edu@SLAC.STANFORD.EDU`
- **Keytab file:** A file containing the service's secret key

The request would be something like:

> "We need a Kerberos service principal for a PostgreSQL server running on sdfiana025.sdf.slac.stanford.edu. Please create `postgres/sdfiana025.sdf.slac.stanford.edu@SLAC.STANFORD.EDU` and provide us with a keytab file."

**Important:** The hostname in the principal must exactly match what clients use to connect. Check with `hostname -f` on the server. If the server might move to a different host, discuss this with IT.

Once you receive the keytab, place it in the repo directory:

```bash
# Store it securely (readable only by you)
cp /path/from/it/postgres.keytab /sdf/data/lcls/ds/prj/prjdat21/results/cwang31/elog-copilot-postgres/pg_data/postgres.keytab
chmod 600 pg_data/postgres.keytab
```

Verify the keytab:

```bash
# List principals in the keytab
klist -k pg_data/postgres.keytab
# Should show: postgres/sdfiana025.sdf.slac.stanford.edu@SLAC.STANFORD.EDU
```

### Task 2: Configure postgresql.conf

Edit `pg_data/postgresql.conf` and add the keytab path. Add this line anywhere in the file:

```
krb_server_keyfile = '/sdf/data/lcls/ds/prj/prjdat21/results/cwang31/elog-copilot-postgres/pg_data/postgres.keytab'
```

Also make sure `listen_addresses` includes the hostname (not just localhost), so TCP connections from the network work:

```
listen_addresses = 'localhost,sdfiana025.sdf.slac.stanford.edu'
```

### Task 3: Create pg_ident.conf

Create the file `pg_data/pg_ident.conf` (it may not exist yet):

```
# pg_ident.conf — Maps Kerberos principals to PostgreSQL roles
#
# MAPNAME       SYSTEM-USERNAME                    PG-USERNAME
# Strip @SLAC.STANFORD.EDU from principal, use bare username as PG role
kerberos        /^(.*)@SLAC\.STANFORD\.EDU$        \1
```

This means `cwang31@SLAC.STANFORD.EDU` becomes PG role `cwang31`.

### Task 4: Modify pg_hba.conf

Edit `pg_data/pg_hba.conf`. The new file should support **both** Kerberos (for real auth) and trust (for test users). Order matters — PostgreSQL uses the first matching rule.

```
# pg_hba.conf — Authentication rules
#
# TYPE      DATABASE        USER            ADDRESS                 METHOD          OPTIONS

# --- Unix socket connections ---
# Admin/test access via local socket (trust = no password, anyone can impersonate)
local       all             all                                     trust

# --- TCP connections ---
# Kerberos (GSSAPI) for real users connecting via TCP
# This verifies identity through Kerberos tickets
host        all             all             127.0.0.1/32            gss             include_realm=1 krb_realm=SLAC.STANFORD.EDU map=kerberos
host        all             all             ::1/128                 gss             include_realm=1 krb_realm=SLAC.STANFORD.EDU map=kerberos
host        all             all             0.0.0.0/0               gss             include_realm=1 krb_realm=SLAC.STANFORD.EDU map=kerberos
```

**Why keep `local trust`?** So you can still connect via Unix socket (`-h /tmp`) as test users for RLS testing. Kerberos connections (`-h sdfiana025...`) will require a real ticket.

### Task 5: Restart PostgreSQL and test config

```bash
# Reload config (no full restart needed for pg_hba.conf and pg_ident.conf)
pg_env/bin/pg_ctl -D pg_data reload

# For postgresql.conf changes (listen_addresses), a full restart is needed
pg_env/bin/pg_ctl -D pg_data -l pg_logs/postgresql.log restart

# Check logs for any config errors
tail -20 pg_logs/postgresql-$(date +%Y-%m-%d).log
```

### Task 6: Create a PG role for cwang31

Connect via the local socket (trust auth, as admin) and create the real user role:

```bash
pg_env/bin/psql -h /tmp -p 5434 -d elog_prototype <<'EOF'
-- Create role for cwang31 (must match the mapped Kerberos principal)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cwang31') THEN
        CREATE ROLE cwang31 LOGIN;
    END IF;
END
$$;

-- Grant table read access
GRANT USAGE ON SCHEMA public TO cwang31;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO cwang31;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO cwang31;

-- Give cwang31 access to ALL experiments (like staff_user, for testing)
INSERT INTO user_experiment_access (username, experiment_id, access_level, granted_by)
SELECT 'cwang31', experiment_id, 'read', 'admin'
FROM experiments
ON CONFLICT (username, experiment_id) DO NOTHING;

-- Verify
SELECT username, count(*) AS experiments FROM user_experiment_access WHERE username = 'cwang31' GROUP BY username;
EOF
```

### Task 7: Test Kerberos authentication

```bash
# Make sure you have a valid Kerberos ticket
klist
# If expired: kinit cwang31@SLAC.STANFORD.EDU

# Connect via TCP using the FQDN (this triggers Kerberos auth)
pg_env/bin/psql -h sdfiana025.sdf.slac.stanford.edu -p 5434 -d elog_prototype -c "SELECT current_user"
# Expected: cwang31

# Verify GSSAPI auth details
pg_env/bin/psql -h sdfiana025.sdf.slac.stanford.edu -p 5434 -d elog_prototype -c \
  "SELECT pid, gss_authenticated, encrypted, principal FROM pg_stat_gssapi WHERE pid = pg_backend_pid()"
# Expected: gss_authenticated = t, principal = cwang31@SLAC.STANFORD.EDU

# Verify RLS works with the Kerberos-authenticated identity
pg_env/bin/psql -h sdfiana025.sdf.slac.stanford.edu -p 5434 -d elog_prototype -c \
  "SELECT count(*) FROM experiments"
# Expected: 1842 (cwang31 has access to all)
```

### Task 8: Test that trust auth still works for test users

The existing RLS tests should still pass via Unix socket:

```bash
bash scripts/verify_rls.sh
# Expected: 15/15 pass (uses -h /tmp, which goes through trust auth)
```

And you can still impersonate test users via the Unix socket:

```bash
# This still works (Unix socket + trust auth)
pg_env/bin/psql -h /tmp -p 5434 -d elog_prototype -U collab_user -c "SELECT count(*) FROM experiments"
# Expected: 5

# This should NOT work via Kerberos (can't impersonate)
pg_env/bin/psql -h sdfiana025.sdf.slac.stanford.edu -p 5434 -d elog_prototype -U collab_user -c "SELECT count(*) FROM experiments"
# Expected: authentication failure (your Kerberos ticket is for cwang31, not collab_user)
```

### Task 9: Test with limited permissions for cwang31

To test what a restricted user would experience, temporarily limit cwang31's access:

```bash
# Remove all access, then grant only 3 experiments
pg_env/bin/psql -h /tmp -p 5434 -d elog_prototype <<'EOF'
DELETE FROM user_experiment_access WHERE username = 'cwang31';
INSERT INTO user_experiment_access (username, experiment_id, access_level, granted_by)
VALUES
    ('cwang31', 'mfx101232725', 'read', 'admin'),
    ('cwang31', 'cxi101233025', 'read', 'admin'),
    ('cwang31', 'xcs101220125', 'read', 'admin');
EOF

# Now test via Kerberos — should see only 3 experiments
pg_env/bin/psql -h sdfiana025.sdf.slac.stanford.edu -p 5434 -d elog_prototype -c "SELECT count(*) FROM experiments"
# Expected: 3

# Restore full access when done
pg_env/bin/psql -h /tmp -p 5434 -d elog_prototype <<'EOF'
DELETE FROM user_experiment_access WHERE username = 'cwang31';
INSERT INTO user_experiment_access (username, experiment_id, access_level, granted_by)
SELECT 'cwang31', experiment_id, 'read', 'admin'
FROM experiments
ON CONFLICT (username, experiment_id) DO NOTHING;
EOF
```

## Testing Summary

| Test | Connection | Auth Method | What It Verifies |
|------|-----------|-------------|-----------------|
| `psql -h sdfiana025... -c "SELECT current_user"` | TCP | Kerberos | Identity is verified as cwang31 |
| `pg_stat_gssapi` query | TCP | Kerberos | GSSAPI is active + shows principal |
| `psql -h sdfiana025... -c "SELECT count(*) FROM experiments"` | TCP | Kerberos | RLS works with Kerberos identity |
| `psql -h /tmp -U collab_user -c "SELECT count(*) FROM experiments"` | Socket | Trust | Test users still work for RLS testing |
| `psql -h sdfiana025... -U collab_user` | TCP | Kerberos | Should FAIL (can't impersonate via Kerberos) |
| `bash scripts/verify_rls.sh` | Socket | Trust | All 15 existing RLS tests still pass |
| Limit cwang31 to 3 experiments, query via Kerberos | TCP | Kerberos | RLS correctly restricts real user |

## Gotchas

1. **Hostname must match the service principal.** If you connect to `localhost` or `127.0.0.1`, Kerberos will look for `postgres/localhost@SLAC.STANFORD.EDU` which won't exist. Always use the FQDN (`sdfiana025.sdf.slac.stanford.edu`).

2. **GSSAPI only works over TCP, not Unix sockets.** `-h /tmp` bypasses Kerberos entirely and uses whatever method is configured for `local` in pg_hba.conf (trust in our case). This is by design — we need trust for admin/test access.

3. **Kerberos ticket expiry.** If `klist` shows expired tickets, run `kinit cwang31@SLAC.STANFORD.EDU` to renew. Tickets last ~25 hours on S3DF.

4. **Clock skew.** Kerberos requires clocks within 5 minutes. This is rarely an issue on HPC (NTP is standard), but if you get "Clock skew too great" errors, check with `date`.

5. **Keytab permissions.** The keytab file must be readable by the user running the PostgreSQL process (`cwang31`). It should NOT be world-readable (it contains a secret key). Use `chmod 600`.

6. **Role must exist before login.** Kerberos authenticates the user but does NOT auto-create PG roles. If someone connects whose role doesn't exist in PG, they get "role does not exist." The role must be created first (Task 6).

7. **pg_hba.conf order matters.** PostgreSQL uses the first matching rule. If you put `trust` before `gss` for TCP connections, Kerberos will never be used. Keep `trust` only on the `local` (Unix socket) line.

## What to Do If Something Goes Wrong

Check the PostgreSQL log:
```bash
tail -50 pg_logs/postgresql-$(date +%Y-%m-%d).log
```

Common errors:
- `no pg_hba.conf entry` — your connection doesn't match any rule in pg_hba.conf
- `GSSAPI authentication failed` — ticket expired, wrong principal, or hostname mismatch
- `role "xxx" does not exist` — need to CREATE ROLE for that user
- `could not open server key file` — keytab path wrong or bad permissions

## Reference Documents

All in `docs/`:
- `2026_0205_technical-walkthrough.md` — full technical explanation of the prototype (read this first)
- `2026_0206_kerberos-auth.md` — detailed Kerberos research (GSSAPI protocol, pg_hba.conf options, version history)
- `2026_0206_auth-vs-rls.md` — why authentication and RLS are separate problems
- `2026_0205_IT-proposal.md` — the proposal document for IT
- `postgres-rls-design.md` — original RLS design document

## Success Criteria

You're done when:

- [ ] Service principal and keytab obtained from IT
- [ ] `postgresql.conf` has `krb_server_keyfile` pointing to the keytab
- [ ] `pg_hba.conf` has `gss` rules for TCP connections
- [ ] `pg_ident.conf` maps `@SLAC.STANFORD.EDU` principals to bare usernames
- [ ] `psql -h sdfiana025.sdf.slac.stanford.edu -p 5434 -d elog_prototype` connects as cwang31 via Kerberos (no password)
- [ ] `pg_stat_gssapi` confirms `gss_authenticated = t`
- [ ] RLS correctly filters experiments based on cwang31's `user_experiment_access` entries
- [ ] Impersonation via `-U` is blocked on Kerberos connections
- [ ] Existing RLS tests (`verify_rls.sh`) still pass via Unix socket
- [ ] Limiting cwang31 to N experiments and querying via Kerberos returns exactly N
