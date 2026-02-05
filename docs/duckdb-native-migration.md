# DuckDB Native Format Migration

## Overview

Migrated the federated elog-copilot prototype from SQLite storage to DuckDB native format. The query engine (DuckDB) remains the same, but experiment databases are now stored as `.duckdb` files instead of `.db` (SQLite) files.

## Why Native DuckDB?

| Before | After |
|--------|-------|
| DuckDB loads SQLite extension at startup | No extension needed |
| `ATTACH ... (TYPE sqlite)` | `ATTACH ...` (native) |
| Row-oriented storage | Columnar storage |
| SQLite type flexibility | Strict types (BIGINT, etc.) |

**Benefits:**
- Faster analytical queries (columnar storage, vectorized execution)
- Simpler query layer (no SQLite extension dependency)
- Better type safety (explicit BIGINT for large values)

## Files Changed

### `src/create_prototype_data.py`

- Import: `sqlite3` for reading source, `duckdb` for writing targets
- File extension: `.db` → `.duckdb`
- Schema changes:
  - Added sequences for auto-increment (`runs_seq`, `logbook_seq`, etc.)
  - Changed `INTEGER` to `BIGINT` for large values (`total_size_bytes`, event counts)
  - Changed `datetime('now')` to `current_timestamp`
- Used `RETURNING` clause instead of `lastrowid`
- Added guards for empty `executemany` calls (DuckDB requirement)

### `src/federated_elog.py`

- Removed: `INSTALL sqlite; LOAD sqlite;`
- Removed: `(TYPE sqlite)` from all ATTACH statements
- Updated docstrings

### `test_prototype.py`

- Updated: `MASTER_DB = "data/master.duckdb"`

## File Structure

```
data/
├── master.duckdb                              # Central index
└── experiments/
    ├── cxi/
    │   ├── cxi25410/.elog/elog.duckdb
    │   └── cxilx8720/.elog/elog.duckdb
    ├── mfx/
    │   └── mfxlt3017/.elog/elog.duckdb        # chmod 000 for testing
    └── xpp/
        └── xppn3816/.elog/elog.duckdb
```

## Schema Differences

### Auto-increment (SQLite vs DuckDB)

```sql
-- SQLite (implicit)
run_id INTEGER PRIMARY KEY

-- DuckDB (explicit sequence)
CREATE SEQUENCE runs_seq START 1;
run_id INTEGER PRIMARY KEY DEFAULT nextval('runs_seq')
```

### Large integers

```sql
-- SQLite (dynamic typing, no overflow)
total_size_bytes INTEGER

-- DuckDB (explicit BIGINT for values > 2^31)
total_size_bytes BIGINT
```

### Timestamps

```sql
-- SQLite
applied_at TEXT DEFAULT (datetime('now'))

-- DuckDB
applied_at TIMESTAMP DEFAULT current_timestamp
```

## Running the Prototype

```bash
cd /sdf/data/lcls/ds/prj/prjdat21/results/cwang31/elog-copilot-federated-b

# Recreate databases
rm -rf data/
UV_CACHE_DIR=/sdf/data/lcls/ds/prj/prjdat21/results/cwang31/.UV_CACHE \
  uv run --with duckdb python3 src/create_prototype_data.py

# Set permission for testing
chmod 000 data/experiments/mfx/mfxlt3017/.elog/elog.duckdb

# Run tests
UV_CACHE_DIR=/sdf/data/lcls/ds/prj/prjdat21/results/cwang31/.UV_CACHE \
  uv run --with duckdb python3 test_prototype.py
```

## Test Results

All 7 tests pass:
- Master index listing
- Single experiment queries
- Permission denial enforcement
- Cross-experiment queries (UNION ALL)
- Graceful handling of mixed permissions
- Logbook queries
- Joins with master detector catalog

## Notes

- Source data is still read from SQLite (`elog_2026_0202_2031.db`) using the `sqlite3` module
- DuckDB's type inference from SQLite was too aggressive (timestamp conversion errors), so we read source with `sqlite3` directly
- Permission model unchanged: filesystem ACLs gate access at query time
