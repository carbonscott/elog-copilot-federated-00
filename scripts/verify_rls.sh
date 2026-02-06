#!/bin/bash
# verify_rls.sh - Verify Row-Level Security works for test users
#
# Prerequisites:
#   1. PostgreSQL running (scripts/setup_postgres.sh start)
#   2. Schema loaded (sql/01_schema.sql)
#   3. RLS policies loaded (sql/02_rls_policies.sql)
#   4. Data migrated (scripts/migrate_sqlite_to_postgres.py)
#   5. Test users created (sql/04_test_users.sql)
#
# Usage:
#   bash scripts/verify_rls.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PSQL="$REPO_DIR/pg_env/bin/psql"
PG_OPTS="-h /tmp -p 5434 -d elog_prototype --no-psqlrc -qtA"

PASS=0
FAIL=0

check() {
    local description="$1"
    local user="$2"
    local query="$3"
    local expected="$4"

    actual=$($PSQL $PG_OPTS -U "$user" -c "$query" 2>&1 | tr -d '[:space:]')

    if [ "$actual" = "$expected" ]; then
        echo "  PASS: $description (got $actual)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $description (expected $expected, got $actual)"
        FAIL=$((FAIL + 1))
    fi
}

check_gte() {
    local description="$1"
    local user="$2"
    local query="$3"
    local min_expected="$4"

    actual=$($PSQL $PG_OPTS -U "$user" -c "$query" 2>&1 | tr -d '[:space:]')

    if [ "$actual" -ge "$min_expected" ] 2>/dev/null; then
        echo "  PASS: $description (got $actual >= $min_expected)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $description (expected >= $min_expected, got $actual)"
        FAIL=$((FAIL + 1))
    fi
}

echo "=============================================="
echo "RLS Verification Tests"
echo "=============================================="
echo ""

# ------------------------------------------------------------------
echo "--- staff_user: sees all experiments ---"
# ------------------------------------------------------------------
check_gte "staff_user: experiment count" \
    staff_user \
    "SELECT COUNT(*) FROM experiments;" \
    1800

check_gte "staff_user: total run count" \
    staff_user \
    "SELECT COUNT(*) FROM runs;" \
    400000

check_gte "staff_user: logbook count" \
    staff_user \
    "SELECT COUNT(*) FROM logbook;" \
    700000

echo ""

# ------------------------------------------------------------------
echo "--- pi_user: sees 1 experiment (mfx101232725) ---"
# ------------------------------------------------------------------
check "pi_user: experiment count" \
    pi_user \
    "SELECT COUNT(*) FROM experiments;" \
    "1"

check "pi_user: sees correct experiment" \
    pi_user \
    "SELECT experiment_id FROM experiments;" \
    "mfx101232725"

check_gte "pi_user: run count for their experiment" \
    pi_user \
    "SELECT COUNT(*) FROM runs;" \
    1

check_gte "pi_user: logbook count for their experiment" \
    pi_user \
    "SELECT COUNT(*) FROM logbook;" \
    1

check "pi_user: questionnaire only shows their experiment" \
    pi_user \
    "SELECT COUNT(DISTINCT experiment_id) FROM questionnaire;" \
    "1"

check "pi_user: workflows only shows their experiment" \
    pi_user \
    "SELECT COUNT(DISTINCT experiment_id) FROM workflows;" \
    "1"

echo ""

# ------------------------------------------------------------------
echo "--- collab_user: sees 5 experiments ---"
# ------------------------------------------------------------------
check "collab_user: experiment count" \
    collab_user \
    "SELECT COUNT(*) FROM experiments;" \
    "5"

check "collab_user: distinct experiments in runs" \
    collab_user \
    "SELECT COUNT(DISTINCT experiment_id) FROM runs;" \
    "5"

check "collab_user: can GROUP BY experiments" \
    collab_user \
    "SELECT COUNT(DISTINCT experiment_id) FROM logbook;" \
    "5"

echo ""

# ------------------------------------------------------------------
echo "--- Cross-table RLS: run_production_data, run_detectors ---"
# ------------------------------------------------------------------
check "pi_user: run_production_data filtered" \
    pi_user \
    "SELECT COUNT(DISTINCT r.experiment_id) FROM run_production_data rpd JOIN runs r ON rpd.run_id = r.run_id;" \
    "1"

check "pi_user: run_detectors filtered" \
    pi_user \
    "SELECT COUNT(DISTINCT r.experiment_id) FROM run_detectors rd JOIN runs r ON rd.run_id = r.run_id;" \
    "1"

echo ""

# ------------------------------------------------------------------
echo "--- Performance: EXPLAIN ANALYZE on key queries ---"
# ------------------------------------------------------------------
echo "  (Checking that indexes are used for RLS subqueries)"
echo ""

echo "  staff_user: experiments query plan:"
$PSQL $PG_OPTS -U staff_user -c "EXPLAIN (COSTS OFF) SELECT COUNT(*) FROM experiments;" 2>&1 | while read line; do echo "    $line"; done

echo ""
echo "  pi_user: runs query plan:"
$PSQL $PG_OPTS -U pi_user -c "EXPLAIN (COSTS OFF) SELECT COUNT(*) FROM runs;" 2>&1 | while read line; do echo "    $line"; done

echo ""

# ------------------------------------------------------------------
echo "--- Logbook search (RLS filters automatically) ---"
# ------------------------------------------------------------------
check "pi_user: logbook search stays within experiment" \
    pi_user \
    "SELECT COUNT(DISTINCT experiment_id) FROM logbook WHERE content LIKE '%run%';" \
    "1"

echo ""

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
echo "=============================================="
echo "Results: $PASS passed, $FAIL failed"
echo "=============================================="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
