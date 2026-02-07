# PostgreSQL Kerberos/GSSAPI Authentication Research

**Date:** 2026-02-06
**Purpose:** Evaluate Kerberos as the authentication mechanism for production PostgreSQL elog access

## Short Answer

Yes, PostgreSQL natively supports Kerberos via GSSAPI. It's built-in (not an extension), has been the standard Kerberos method since PostgreSQL 9.4, and is well-suited for the S3DF HPC environment where users already have Kerberos tickets from SSH.

## How It Would Work for Us

```
User SSHes into S3DF as cwang31
    → gets Kerberos ticket: cwang31@SLAC.STANFORD.EDU
        │
        ▼
User runs: psql -h pgserver -d elog_prototype
    → libpq finds cached Kerberos ticket
    → requests service ticket for postgres/pgserver@SLAC.STANFORD.EDU
    → sends to PostgreSQL server
        │
        ▼
PostgreSQL validates ticket using its keytab
    → pg_ident.conf maps cwang31@SLAC.STANFORD.EDU → PG role cwang31
    → current_user = 'cwang31' (verified, unforgeable)
        │
        ▼
RLS policies filter data by cwang31's experiment access
    → user sees only their experiments
```

**No password is ever entered for the database connection.** The Kerberos ticket from SSH login is reused automatically.

## Version History

| PostgreSQL Version | Kerberos Support |
|-------------------|-----------------|
| 6.x – 8.2 | Separate `krb5` auth method (direct Kerberos v5) |
| 8.3 | `krb5` deprecated in favor of `gss` (GSSAPI) |
| 9.4 | `krb5` removed entirely. Only `gss` remains |
| 12 | Added `hostgssenc` for GSSAPI-encrypted connections |
| **16 (ours)** | Full GSSAPI auth + encryption + credential delegation |

Our micromamba-installed PostgreSQL 16 is already built with `--with-gssapi` support.

## What's Needed for Setup

### 1. Service Principal (IT creates this)

PostgreSQL needs a Kerberos service principal in the form:

```
postgres/<fully-qualified-hostname>@SLAC.STANFORD.EDU
```

For example: `postgres/sdflogin001.sdf.slac.stanford.edu@SLAC.STANFORD.EDU`

IT's Kerberos administrators create this using `kadmin`.

### 2. Keytab File (IT provides this)

A keytab file contains the service principal's secret key. PostgreSQL uses it to verify incoming Kerberos tickets.

Configure in `postgresql.conf`:
```
krb_server_keyfile = '/path/to/postgres.keytab'
```

Permissions: readable only by the PostgreSQL OS user (contains a secret key).

### 3. pg_hba.conf (we configure this)

Tell PostgreSQL to use GSSAPI auth for TCP connections:

```
# TYPE      DATABASE  USER  ADDRESS       METHOD  OPTIONS
hostgssenc  all       all   0.0.0.0/0     gss     include_realm=1 krb_realm=SLAC.STANFORD.EDU map=kerberos

# Keep Unix socket access for local admin
local       all       all                 peer
```

- `hostgssenc` — only accept GSSAPI-encrypted TCP connections (wire encryption, like SSL but via Kerberos)
- `krb_realm=SLAC.STANFORD.EDU` — only accept principals from this realm (security: rejects `cwang31@EVIL.COM`)
- `map=kerberos` — use the mapping defined in pg_ident.conf

### 4. pg_ident.conf (we configure this)

Maps Kerberos principal names to PostgreSQL role names:

```
# MAPNAME     SYSTEM-USERNAME                    PG-USERNAME
kerberos      /^(.*)@SLAC\.STANFORD\.EDU$        \1
```

This regex strips `@SLAC.STANFORD.EDU` from the principal, so `cwang31@SLAC.STANFORD.EDU` maps to PG role `cwang31`. This is the recommended approach — it keeps realm verification secure while using short usernames as PG roles.

### 5. PostgreSQL Roles (sync script creates these)

Each user needs a PG role matching their SLAC username:

```sql
CREATE ROLE cwang31 LOGIN;
```

This can be automated as part of the permission sync script — when it populates `user_experiment_access`, it also creates the role if it doesn't exist.

## Client-Side Requirements

**Nothing special.** Users just need a valid Kerberos ticket, which they already have from SSH login:

```bash
$ klist
Default principal: cwang31@SLAC.STANFORD.EDU
Valid starting       Expires              Service principal
02/06/2026 16:54:47  02/07/2026 17:54:47  krbtgt/SLAC.STANFORD.EDU@SLAC.STANFORD.EDU
```

`psql` (via libpq) automatically discovers the ticket and uses it. No configuration needed on the client beyond what S3DF already provides.

The existing `/etc/krb5.conf` on S3DF already has the right settings:
- `default_realm = SLAC.STANFORD.EDU`
- `forwardable = true`
- `rdns = false` (avoids reverse DNS hostname issues)

## Gotchas and Limitations

### GSSAPI only works over TCP, not Unix sockets

Kerberos auth requires a TCP connection. Unix socket connections (what we use now with `-h /tmp`) need a different method (peer or trust). In production, you'd typically have:
- `local` connections via Unix socket using `peer` auth (for admin on the server itself)
- `host`/`hostgssenc` connections via TCP using `gss` auth (for users connecting remotely)

### Hostname matters

The client builds the service principal from the hostname it connects to. If the user connects to `pgserver` but the principal is registered as `postgres/pgserver.sdf.slac.stanford.edu`, it won't match. S3DF's `rdns = false` and `ignore_acceptor_hostname = true` mitigate this, but the hostname used in the `psql` command should match the principal.

### No automatic role creation

Kerberos authenticates the user but does not auto-create PostgreSQL roles. The role must already exist. The permission sync script should handle this.

### Clock skew

Kerberos requires clocks within 5 minutes. HPC clusters have NTP, so this is rarely an issue.

### Ticket expiry

Tickets are checked only at connection time. Long-running connections are not affected. New connections after ticket expiry fail until the user renews (`kinit`). S3DF tickets last ~25 hours with 7-day renewability.

## Comparison: Peer Auth vs Kerberos

| Aspect | Peer Auth | Kerberos/GSSAPI |
|--------|-----------|-----------------|
| Connection type | Unix socket only | TCP (can be remote) |
| Identity source | OS process UID | Kerberos ticket |
| Setup complexity | Minimal | Needs service principal + keytab |
| Remote access | No (local only) | Yes |
| Wire encryption | No (local socket) | Yes (GSSAPI encryption) |
| User experience | `psql -h /tmp` (local only) | `psql -h pgserver` (from anywhere on network) |
| IT involvement | Low | Medium (Kerberos admin creates principal) |

**For production:** Kerberos is better because it supports remote connections (users can connect from any S3DF node, not just the PG server host) and provides wire encryption.

**For development/prototype:** Peer auth (or trust) is simpler and doesn't need IT involvement.

## Monitoring GSSAPI Connections

PostgreSQL provides a built-in view to verify Kerberos auth is working:

```sql
SELECT pid, gss_authenticated, encrypted, principal
FROM pg_stat_gssapi
WHERE pid = pg_backend_pid();

-- Example output:
--  pid  | gss_authenticated | encrypted |            principal
-- ------+-------------------+-----------+------------------------------------
--  1234 | t                 | t         | cwang31@SLAC.STANFORD.EDU
```

## Summary: What to Ask IT For

| Item | Who provides | Effort |
|------|-------------|--------|
| Service principal (`postgres/<host>@SLAC.STANFORD.EDU`) | Kerberos admins | Low (standard procedure) |
| Keytab file | Kerberos admins | Low (generated with principal) |
| PostgreSQL instance hosting | IT infrastructure | Medium |
| pg_hba.conf / pg_ident.conf configuration | Us (with IT review) | Low |
| Role creation + permission sync script | Us | Medium |
