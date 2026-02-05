# Federated Elog-Copilot

A prototype for permission-gated, per-experiment elog databases using native DuckDB format.

## Quick Start

```bash
cd /sdf/data/lcls/ds/prj/prjdat21/results/cwang31/elog-copilot-federated-b

# Run the test suite
UV_CACHE_DIR=/sdf/data/lcls/ds/prj/prjdat21/results/cwang31/.UV_CACHE \
  uv run --with duckdb python3 test_prototype.py

# Interactive DuckDB CLI
./bin/duckdb
```

## Project Structure

```
elog-copilot-federated/
├── CLAUDE.md                 # This file
├── HANDOFF.md                # Design document and rationale
├── bin/
│   └── duckdb                # DuckDB CLI binary (v1.1.3)
├── src/
│   ├── create_prototype_data.py   # Extracts data from centralized DB
│   └── federated_elog.py          # Python query layer
├── test_prototype.py         # Test suite
└── data/
    ├── master.duckdb         # Public index (experiment IDs, paths, detectors)
    └── experiments/
        ├── cxi/
        │   ├── cxi25410/.elog/elog.duckdb    # Readable
        │   └── cxilx8720/.elog/elog.duckdb   # Readable
        ├── mfx/
        │   └── mfxlt3017/.elog/elog.duckdb   # chmod 000 (denied)
        └── xpp/
            └── xppn3816/.elog/elog.duckdb    # Readable
```

## Architecture

### Master DB (public, readable by all)
Contains only non-sensitive discovery data:
- `experiment_index`: experiment_id, instrument, db_path
- `detector_catalog`: shared detector names
- `schema_info`: version tracking

### Experiment DBs (permission-gated by filesystem ACLs)
Contains sensitive data:
- `experiment`: PI name, email, description
- `runs`, `run_production_data`, `run_detectors`
- `logbook`: timestamped entries with content
- `questionnaire`: proposal details
- `workflows`: analysis workflow definitions

## Usage Examples

### DuckDB CLI

```sql
-- Attach databases (native DuckDB format, no extension needed)
ATTACH 'data/master.duckdb' AS master;
ATTACH 'data/experiments/cxi/cxi25410/.elog/elog.duckdb' AS cxi25410;

-- Query
SELECT * FROM cxi25410.runs LIMIT 5;

-- Cross-experiment query
SELECT 'cxi25410' as exp, COUNT(*) FROM cxi25410.runs
UNION ALL
SELECT 'cxilx8720', COUNT(*) FROM cxilx8720.runs;

-- Join with master catalog
SELECT d.detector_name, COUNT(*) as usage
FROM cxi25410.run_detectors rd
JOIN master.detector_catalog d ON rd.detector_id = d.detector_id
GROUP BY d.detector_name;
```

### Python API

```python
from src.federated_elog import FederatedElog

with FederatedElog('data/master.duckdb') as elog:
    # List all experiments
    experiments = elog.list_experiments()

    # Query single experiment
    result = elog.query_experiment('cxi25410',
        'SELECT run_number FROM runs LIMIT 5')

    # Cross-experiment query (handles permission denial gracefully)
    result = elog.query_cross_experiment(
        ['cxi25410', 'mfxlt3017', 'xppn3816'],
        'SELECT run_number FROM {exp}.runs LIMIT 3'
    )
    print(f"Attached: {result.attached_experiments}")
    print(f"Skipped: {result.skipped_experiments}")
```

## Key Findings

| Feature | Status | Notes |
|---------|--------|-------|
| Native DuckDB ATTACH | ✓ Works | No limit on attached DBs (unlike SQLite's 125) |
| Permission enforcement | ✓ Works | Filesystem ACLs gate access at query time |
| Cross-DB queries | ✓ Works | UNION ALL, JOINs across schemas |
| Graceful denial handling | ✓ Works | Skipped experiments reported in result |
| Master/experiment separation | ✓ Works | Sensitive data only in experiment DBs |

## Known Issues

1. **DuckDB CLI not available via pip/uv**
   - Must download binary from GitHub releases
   - Binary located at `bin/duckdb`

2. **Lazy permission check**
   - DuckDB's ATTACH succeeds even for unreadable files
   - Error occurs at query time, not attach time
   - Python wrapper pre-checks with `os.access()` for better UX

## Recreating Test Data

```bash
# Regenerate from centralized DB
UV_CACHE_DIR=/sdf/data/lcls/ds/prj/prjdat21/results/cwang31/.UV_CACHE \
  uv run python3 src/create_prototype_data.py

# Set permission denial on mfxlt3017
chmod 000 data/experiments/mfx/mfxlt3017/.elog/elog.duckdb
```

## Source Data

Centralized DB: `/sdf/group/lcls/ds/dm/apps/dev/data/elog-copilot/elog_2026_0202_2031.db`

## Next Steps

See HANDOFF.md Section 7 "Prototype Tasks" for the full roadmap. Key items:
- Migration script for all 1,800+ experiments
- Incremental sync mechanism
- Performance testing with many attached DBs
- Integration with elogfetch for writes
