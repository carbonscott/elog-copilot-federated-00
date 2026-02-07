# Technical Walkthrough: What We Built and How It Works

**Date:** 2026-02-05
**Audience:** Someone who knows SQLite but not PostgreSQL

## The Big Picture

We took our existing elog SQLite database (~4.85M rows) and set up a PostgreSQL copy with **per-user permission filtering built into the database itself**. This means when user A queries "show me all experiments," they only see *their* experiments — and the filtering happens inside PostgreSQL, not in our application code.

SQLite has no concept of users or permissions — if you can read the file, you see everything. PostgreSQL has a feature called **Row-Level Security (RLS)** that solves this.

## Step-by-Step: What Was Done

### Step 1: Install PostgreSQL locally

**File:** `scripts/setup_postgres.sh`

SQLite is a library — you link it into your program, or use the `sqlite3` CLI, and it reads/writes a file directly. No server process.

PostgreSQL is a **client-server database**. There's a server process (`postgres`) that runs in the background, and clients (like `psql`) connect to it over a socket. Think of it like a web server, but for SQL.

We installed PostgreSQL 16 using micromamba (a fast conda-like package manager) into a local directory:

```
pg_env/          ← PostgreSQL binaries (psql, pg_ctl, etc.)
pg_data/         ← Database files (like SQLite's .db file, but a directory)
pg_logs/         ← Server log files
```

Key differences from SQLite:

| Concept | SQLite | PostgreSQL |
|---------|--------|------------|
| Where data lives | Single `.db` file | `pg_data/` directory of many files |
| How you start it | Nothing to start — just open the file | `pg_ctl start` runs a background server |
| How you connect | `sqlite3 /path/to/file.db` | `psql -h /tmp -p 5434 -d elog_prototype` |
| Port | N/A | 5434 (we chose this; default is 5432) |
| Socket | N/A | Unix socket at `/tmp` (like a local-only network connection) |

The setup script does: install micromamba → install PostgreSQL → initialize a data directory → configure → start the server → create a database called `elog_prototype`.

### Step 2: Create the schema

**File:** `sql/01_schema.sql`

This is very similar to SQLite `CREATE TABLE` statements, with a few differences.

**What's the same:**
```sql
-- This looks almost identical to SQLite
CREATE TABLE experiments (
    experiment_id TEXT PRIMARY KEY,
    name TEXT,
    instrument TEXT,
    description TEXT
);
```

**What's different:**

1. **`SERIAL` instead of `INTEGER PRIMARY KEY` for auto-increment:**

   ```sql
   -- SQLite: INTEGER PRIMARY KEY auto-increments implicitly
   CREATE TABLE runs (run_id INTEGER PRIMARY KEY, ...);

   -- PostgreSQL: use SERIAL (creates a sequence behind the scenes)
   CREATE TABLE runs (run_id SERIAL PRIMARY KEY, ...);
   ```

   `SERIAL` tells PostgreSQL to automatically generate incrementing IDs. It creates a hidden *sequence* object (like a counter) that increments each time you insert a row.

2. **`TIMESTAMPTZ` instead of TEXT for dates:**

   ```sql
   -- SQLite: dates are just text strings, no special handling
   start_time TEXT

   -- PostgreSQL: native date/time type, understands time zones
   start_time TIMESTAMPTZ
   ```

   This means PostgreSQL actually *understands* dates. You can do `WHERE start_time >= '2025-01-01'` and it works correctly — no need for the `DATETIME()` workarounds we had in SQLite.

3. **`REFERENCES` enforces foreign keys by default:**

   In SQLite, `REFERENCES` is declared but not enforced unless you run `PRAGMA foreign_keys = ON`. In PostgreSQL, foreign keys are always enforced. If you try to insert a run for a non-existent experiment_id, PostgreSQL rejects it.

4. **Table names are lowercase:**

   SQLite is case-insensitive for table names. PostgreSQL converts unquoted names to lowercase. Our SQLite uses `Experiment`, `Run`, `Logbook` (PascalCase). In PostgreSQL, we use `experiments`, `runs`, `logbook` (lowercase).

5. **One extra table — `user_experiment_access`:**

   This is new. It doesn't exist in SQLite. It maps usernames to experiments they can access:

   ```sql
   CREATE TABLE user_experiment_access (
       username TEXT NOT NULL,        -- e.g., 'cwang31'
       experiment_id TEXT NOT NULL,   -- e.g., 'mfx101232725'
       access_level TEXT DEFAULT 'read',
       UNIQUE(username, experiment_id)
   );
   ```

   This table is the foundation of the permission system.

6. **Indexes:**

   SQLite creates indexes automatically for primary keys. PostgreSQL does too, but we also created explicit indexes for columns used in JOINs and WHERE clauses to speed up queries:

   ```sql
   CREATE INDEX idx_runs_experiment ON runs(experiment_id);
   CREATE INDEX idx_user_access_username ON user_experiment_access(username);
   ```

   Think of an index like a book's index — instead of reading every page to find "alignment," you look up "alignment" in the index and jump directly to the right page. Without indexes on large tables, PostgreSQL would scan every row.

### Step 3: Set up Row-Level Security (RLS)

**File:** `sql/02_rls_policies.sql`

This is the core feature that SQLite doesn't have. RLS lets you define rules like "user X can only see rows where experiment_id is in their access list."

**How it works, step by step:**

1. **Enable RLS on a table:**

   ```sql
   ALTER TABLE experiments ENABLE ROW LEVEL SECURITY;
   ```

   Once enabled, the table returns **zero rows** to everyone by default. It's like putting a lock on every row.

2. **Create a policy (the "key"):**

   ```sql
   CREATE POLICY experiment_select ON experiments
       FOR SELECT
       USING (experiment_id IN (
           SELECT experiment_id FROM user_experiment_access
           WHERE username = current_user
       ));
   ```

   Let's break this down:
   - `ON experiments` — this policy applies to the `experiments` table
   - `FOR SELECT` — it controls read access (not insert/update/delete)
   - `USING (...)` — the condition each row must satisfy to be visible
   - `current_user` — a built-in PostgreSQL variable that returns the username of whoever is connected

   So when `pi_user` runs `SELECT * FROM experiments`, PostgreSQL internally rewrites it to:

   ```sql
   SELECT * FROM experiments
   WHERE experiment_id IN (
       SELECT experiment_id FROM user_experiment_access
       WHERE username = 'pi_user'
   );
   ```

   The user never sees or writes this filter. It's invisible. They just write normal SQL and get filtered results.

3. **Chain policies across related tables:**

   For tables that have `experiment_id` directly (like `runs`, `logbook`, `questionnaire`), the policy is similar — check if the row's `experiment_id` is in the user's access list.

   For tables that don't have `experiment_id` directly (like `run_production_data`, which only has `run_id`), we JOIN through `runs` to find the experiment:

   ```sql
   CREATE POLICY run_prod_select ON run_production_data
       FOR SELECT
       USING (run_id IN (
           SELECT r.run_id FROM runs r
           JOIN user_experiment_access ua ON r.experiment_id = ua.experiment_id
           WHERE ua.username = current_user
       ));
   ```

   This means: "you can see production data for a run only if you have access to the experiment that run belongs to."

4. **FORCE ROW LEVEL SECURITY:**

   ```sql
   ALTER TABLE experiments FORCE ROW LEVEL SECURITY;
   ```

   By default, the table *owner* (the user who created the table) bypasses RLS. `FORCE` closes this loophole — even the owner must go through RLS policies. We use this because in our prototype, the same user who created the tables also queries them.

5. **Admin bypass:**

   We created an `elog_admin` role that can see everything (needed for data migration and maintenance):

   ```sql
   CREATE ROLE elog_admin;
   CREATE POLICY admin_experiments ON experiments FOR ALL TO elog_admin
       USING (true) WITH CHECK (true);
   ```

   `USING (true)` means "every row passes" — so admins see all data. The migration script connects as a user who has `elog_admin` granted.

**SQLite analogy:** Imagine if SQLite automatically appended `WHERE experiment_id IN (your_allowed_list)` to every query you run, and there was no way to bypass it. That's RLS.

### Step 4: Migrate data from SQLite

**File:** `scripts/migrate_sqlite_to_postgres.py`

This Python script reads from our SQLite database and inserts into PostgreSQL. It uses:
- `sqlite3` (Python stdlib) to read
- `psycopg2` (PostgreSQL adapter for Python) to write

Key details:

1. **Table name mapping:** `Experiment` → `experiments`, `Run` → `runs`, etc.

2. **Batch inserts:** Instead of inserting one row at a time (slow), we insert in batches of 5,000 rows using `psycopg2.extras.execute_values()`. This is like doing a bulk `INSERT INTO ... VALUES (...), (...), (...), ...` instead of separate INSERT statements.

3. **NUL byte stripping:** Our SQLite logbook content contains `\x00` (NUL) characters. SQLite allows this, but PostgreSQL TEXT columns reject NUL bytes. The script strips them:

   ```python
   v.replace("\x00", "") if isinstance(v, str) else v
   ```

4. **Serial sequence reset:** After inserting rows with explicit IDs (e.g., `run_id = 443000`), we reset PostgreSQL's auto-increment counter to start after the highest existing ID:

   ```sql
   SELECT setval('runs_run_id_seq', MAX(run_id)) FROM runs;
   ```

   Without this, the next auto-generated ID might collide with an existing one.

5. **Foreign key order:** Tables are migrated in dependency order — `experiments` first (no dependencies), then `runs` (depends on experiments), then `run_production_data` (depends on runs), etc.

**Result:** ~4.85M rows migrated in ~2.3 minutes:

| Table | Rows |
|-------|------|
| experiments | 1,842 |
| runs | 443,000 |
| logbook | 710,000 |
| run_detectors | 3,100,000 |
| questionnaire | 109,000 |
| workflows | 2,600 |
| detectors | 484 |
| run_production_data | 484,000 |

### Step 5: Create test users

**File:** `sql/04_test_users.sql`

In SQLite, there are no users. Anyone who opens the file sees everything.

In PostgreSQL, every connection has a **role** (user). We created three test roles to simulate different permission levels:

```sql
CREATE ROLE staff_user LOGIN;    -- can log in
CREATE ROLE pi_user LOGIN;
CREATE ROLE collab_user LOGIN;
```

`LOGIN` means the role can connect to the database (some roles are just for grouping permissions, not for logging in).

Then we granted them table access and populated `user_experiment_access`:

```sql
-- staff_user gets ALL experiments
INSERT INTO user_experiment_access (username, experiment_id, access_level, granted_by)
SELECT 'staff_user', experiment_id, 'read', 'admin'
FROM experiments;     -- inserts 1,842 rows

-- pi_user gets 1 experiment with write access
INSERT INTO user_experiment_access (username, experiment_id, access_level, granted_by)
VALUES ('pi_user', 'mfx101232725', 'write', 'admin');

-- collab_user gets 5 experiments with read access
INSERT INTO user_experiment_access (username, experiment_id, access_level, granted_by)
VALUES
    ('collab_user', 'mfx101232725', 'read', 'admin'),
    ('collab_user', 'cxi101233025', 'read', 'admin'),
    -- ... 3 more
```

Two levels of access control are at play:

1. **`GRANT SELECT ON ALL TABLES`** — allows the role to run SELECT on tables at all. Without this, they'd get "permission denied" before RLS even kicks in.

2. **`user_experiment_access` rows** — controls *which rows* they see (RLS). Without entries here, RLS returns zero rows.

Think of it as: GRANT is the front door key (can you enter the building?), RLS is the room key (which rooms can you access once inside?).

### Step 6: Verify RLS works

**File:** `scripts/verify_rls.sh`

This bash script runs 15 automated tests by connecting as different users and checking that they see the right data:

```bash
# Connect as pi_user and count experiments — should be exactly 1
psql -U pi_user -c "SELECT COUNT(*) FROM experiments;"
# → 1

# Connect as collab_user — should see exactly 5
psql -U collab_user -c "SELECT COUNT(*) FROM experiments;"
# → 5

# Connect as staff_user — should see all 1800+
psql -U staff_user -c "SELECT COUNT(*) FROM experiments;"
# → 1842
```

The `-U` flag tells psql which user to connect as. This works in our prototype because we use **trust authentication** (no password required, anyone can impersonate anyone). In production, you'd only be able to connect as yourself.

The tests verify:
- Each user sees the correct number of experiments
- RLS cascades to child tables (runs, logbook, questionnaire, workflows)
- Cross-table joins (run_production_data, run_detectors) are also filtered
- Text searches in logbook stay within the user's experiments

### Step 7: AI copilot skills

**Files:**
- Claude Code: `deploy-opencode/claude/skills/elog-copilot-postgres/SKILL.md`
- OpenCode: `/sdf/group/lcls/ds/dm/apps/dev/opencode/agents/experimental-elog-copilot-postgres.md`

These teach the AI assistant how to query the PostgreSQL database. The skill tells the AI:

- Use `psql` to run queries (instead of `sqlite3`)
- Table names are lowercase
- Use `ILIKE` for case-insensitive search (not `LIKE`)
- Use `string_agg()` (not `GROUP_CONCAT`)
- Check if the server is running first
- Results are automatically filtered by the user's permissions

## PostgreSQL vs SQLite: Concepts Glossary

| PostgreSQL concept | SQLite equivalent | Explanation |
|--------------------|-------------------|-------------|
| Server (`postgres` process) | None | Background process that manages the database |
| `psql` CLI | `sqlite3` CLI | Interactive query tool |
| Role / User | None | An identity that connects to the database |
| `GRANT SELECT` | File read permission | Controls whether a user can query a table |
| Row-Level Security (RLS) | None | Filters rows based on who's querying |
| `current_user` | None | Built-in variable: "who am I connected as?" |
| `SERIAL` | `INTEGER PRIMARY KEY` | Auto-incrementing ID column |
| `TIMESTAMPTZ` | TEXT (by convention) | Native date/time type with time zone |
| `pg_ctl start/stop` | None | Controls the server process |
| `pg_isready` | `test -f db.file` | Checks if the server is accepting connections |
| `CREATE DATABASE` | Creating a `.db` file | Each database is an isolated namespace |
| `pg_hba.conf` | File permissions | Controls who can connect and how |
| `postgresql.conf` | None | Server configuration (port, memory, etc.) |
| Schema / `public` | None (single namespace) | Namespace for tables within a database |
| `FORCE ROW LEVEL SECURITY` | None | Even the table owner must obey RLS |
| `psycopg2` | `sqlite3` module | Python library to talk to the database |

## File Map

```
elog-copilot-postgres/
├── scripts/
│   ├── setup_postgres.sh              # Install PG, create server, create database
│   ├── migrate_sqlite_to_postgres.py  # Copy data from SQLite → PG
│   └── verify_rls.sh                  # 15 automated RLS tests
├── sql/
│   ├── 01_schema.sql                  # CREATE TABLE statements
│   ├── 02_rls_policies.sql            # RLS policies (the permission rules)
│   ├── 03_test_data.sql               # (placeholder)
│   └── 04_test_users.sql              # Test users + their permission entries
├── pg_env/                            # PostgreSQL binaries (installed by setup)
├── pg_data/                           # Database files (created by setup)
├── pg_logs/                           # Server logs
└── docs/                              # Documentation
```

## How to Reproduce from Scratch

```bash
cd /sdf/data/lcls/ds/prj/prjdat21/results/cwang31/elog-copilot-postgres

# 1. Install and start PostgreSQL
bash scripts/setup_postgres.sh

# 2. Load schema
pg_env/bin/psql -h /tmp -p 5434 -d elog_prototype -f sql/01_schema.sql

# 3. Enable RLS policies
pg_env/bin/psql -h /tmp -p 5434 -d elog_prototype -f sql/02_rls_policies.sql

# 4. Migrate data from SQLite (~2.3 minutes)
UV_CACHE_DIR=/sdf/data/lcls/ds/prj/prjdat21/results/cwang31/.UV_CACHE \
  uv run --with psycopg2-binary scripts/migrate_sqlite_to_postgres.py

# 5. Create test users
pg_env/bin/psql -h /tmp -p 5434 -d elog_prototype -f sql/04_test_users.sql

# 6. Verify RLS (15 tests)
bash scripts/verify_rls.sh

# 7. Try a query
pg_env/bin/psql -h /tmp -p 5434 -d elog_prototype -c "SELECT count(*) FROM experiments"

# 8. Try as a limited user
pg_env/bin/psql -h /tmp -p 5434 -d elog_prototype -U collab_user -c "SELECT count(*) FROM experiments"
```
