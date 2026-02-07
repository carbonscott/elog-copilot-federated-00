# Authentication vs Authorization (RLS): Clarifying the Two Problems

**Date:** 2026-02-06
**Context:** Notes from discussions with IT about production deployment

## The Key Insight

Auth and RLS are **two completely separate problems**:

| Problem | Question it answers | Who solves it |
|---------|-------------------|---------------|
| **Authentication** | "Is this person really cwang31?" | PostgreSQL auth layer (peer, Kerberos, etc.) |
| **Authorization (RLS)** | "What can cwang31 see?" | RLS policies + `user_experiment_access` table |

They're independent. You can solve one without the other. Our prototype already solved authorization (RLS) — the policies work, the schema works, the tests pass (15/15). Authentication is the remaining piece.

## Discussion Point 1: The Experiment Permission API

IT confirmed there's an existing API that takes a username and returns the experiments they can access. This makes populating the RLS permission table straightforward:

```python
# Pseudocode for the permission sync
for user in all_users:
    experiments = api.get_experiments_for_user(user)
    for exp in experiments:
        INSERT INTO user_experiment_access (username, experiment_id)
        VALUES (user, exp)
```

Run this periodically (cron) or on-demand. The RLS policies we already built do the rest. This is the easiest part of the whole system.

## Discussion Point 2: PostgreSQL Authentication

PostgreSQL does need to verify identity, but it **does not need its own password system**. It can delegate to existing infrastructure:

| Method | How it works | Needs PG passwords? |
|--------|-------------|---------------------|
| **Peer auth** | Connect via Unix socket. PG asks the OS "who is this process?" OS says "cwang31." Done. | No |
| **Kerberos/GSSAPI** | User already has a Kerberos ticket from logging into the HPC. PG verifies it. | No |
| **Certificate (mTLS)** | Client presents an SSL certificate that says "I am cwang31." | No |
| **Password/LDAP** | PG checks a password against its own store or LDAP. | Yes (or reuses LDAP passwords) |

**Peer auth is the simplest for our HPC environment.** When a user SSHes into the HPC as `cwang31` and runs `psql`, the OS has already verified their identity. PostgreSQL just asks the OS "who is this?" — no separate passwords, no new auth layer. The only requirement is that a PostgreSQL role named `cwang31` exists (which the permission sync script can create automatically).

Our prototype already uses a simplified version of this (trust auth = peer auth without the verification step).

## Discussion Point 3: Query Interceptor Approach

One IT suggestion was to build a SQL query interceptor that sits between AI agents and the database, authenticates users via the API, and rewrites queries (adding WHERE clauses).

**Assessment: Not recommended.** Problems with this approach:

1. **Still no authentication.** If an AI agent can claim to be anyone, adding a WHERE clause doesn't help. The identity is unverified at the database level.

2. **Fragile.** SQL rewriting is hard to get right for all query shapes (subqueries, CTEs, JOINs, UNIONs, window functions). Miss one edge case and data leaks. RLS handles all of these natively because PostgreSQL applies the filter inside the query planner before execution.

3. **Bypassable.** Anyone who connects directly to the database (not through the interceptor) skips the filter entirely. RLS cannot be bypassed — it's enforced by the database engine itself.

4. **Reinventing the wheel.** PostgreSQL RLS is exactly this concept — a query interceptor built into the database, battle-tested for 10+ years, and impossible to bypass. Building a custom one adds complexity without adding security.

The interceptor approach would make sense for a database that doesn't have RLS (like SQLite or older MySQL). But PostgreSQL has it natively.

## The Clean Architecture

Two separate, clean pipelines — no query interceptor needed:

```
┌─────────────────────────────────────────────────────────────────┐
│ Authentication Pipeline                                         │
│                                                                 │
│ User SSHes into HPC as cwang31                                  │
│       │                                                         │
│       ▼                                                         │
│ psql connects via Unix socket                                   │
│       │                                                         │
│       ▼                                                         │
│ PostgreSQL peer auth: OS confirms "this is cwang31"             │
│       │                                                         │
│       ▼                                                         │
│ current_user = 'cwang31' (verified, unforgeable)                │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ Authorization Pipeline                                          │
│                                                                 │
│ RLS policy on every table:                                      │
│   WHERE experiment_id IN (                                      │
│       SELECT experiment_id FROM user_experiment_access           │
│       WHERE username = current_user     ← verified identity     │
│   )                                                             │
│       │                                                         │
│       ▼                                                         │
│ User sees only their experiments                                │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ Permission Sync Pipeline (separate from both)                   │
│                                                                 │
│ Experiment permission API (source of truth)                     │
│       │                                                         │
│       ▼                                                         │
│ Sync script populates user_experiment_access                    │
│ (cron job, e.g., every 15 minutes)                              │
└─────────────────────────────────────────────────────────────────┘
```

## What We Need from IT

1. **Peer auth (or Kerberos) on the PostgreSQL instance** — solves authentication without any new password infrastructure
2. **Access to the experiment permission API** — so we can write a sync script for `user_experiment_access`
3. **PostgreSQL roles for real users** — the sync script can auto-create roles (e.g., `CREATE ROLE cwang31 LOGIN`)

## What's Already Done

- RLS policies: built and tested (15/15 tests pass in `scripts/verify_rls.sh`)
- Schema with `user_experiment_access` table: working
- AI copilot skills: working (Claude Code + OpenCode)
- Data migration from SQLite: working (~2.3 min for 4.85M rows)

## Summary

| Component | Status | Who |
|-----------|--------|-----|
| Database schema + RLS policies | Done | Us |
| Data migration (SQLite → PG) | Done | Us |
| AI copilot skills | Done | Us |
| PostgreSQL instance (production) | Needed | IT |
| Authentication (peer/Kerberos) | Needed | IT |
| Permission API access | Needed | IT |
| Permission sync script | To build | Us + IT |
