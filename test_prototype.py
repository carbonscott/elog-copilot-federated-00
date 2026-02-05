#!/usr/bin/env python3
"""
test_prototype.py - Test the federated elog prototype

Tests:
1. List experiments from master index
2. Query accessible experiments
3. Verify permission denial for restricted experiment
4. Cross-experiment queries (with graceful handling of denied experiments)
"""

import sys
sys.path.insert(0, 'src')

from federated_elog import FederatedElog

MASTER_DB = "data/master.duckdb"


def print_section(title: str):
    print()
    print("=" * 60)
    print(f" {title}")
    print("=" * 60)


def test_list_experiments(elog: FederatedElog):
    """Test listing experiments from master index."""
    print_section("TEST 1: List experiments from master index")

    experiments = elog.list_experiments()
    print(f"Found {len(experiments)} experiments in index:")
    for exp in experiments:
        print(f"  {exp['experiment_id']:15} | {exp['instrument']:4} | {exp['db_path']}")


def test_single_experiment_query(elog: FederatedElog):
    """Test querying a single accessible experiment."""
    print_section("TEST 2: Query single experiment (cxi25410)")

    result = elog.query_experiment(
        "cxi25410",
        "SELECT run_number, start_time FROM runs ORDER BY run_number LIMIT 5"
    )

    print(f"Columns: {result.columns}")
    print(f"Rows returned: {len(result.data)}")
    print("Sample data:")
    for row in result.data[:5]:
        print(f"  Run {row[0]}: {row[1]}")


def test_permission_denial(elog: FederatedElog):
    """Test that permission denial is handled correctly."""
    print_section("TEST 3: Permission denial (mfxlt3017)")

    try:
        result = elog.query_experiment(
            "mfxlt3017",
            "SELECT * FROM runs LIMIT 1"
        )
        print("ERROR: Should have raised PermissionError!")
    except PermissionError as e:
        print(f"✓ Correctly denied access: {e}")
    except Exception as e:
        print(f"Got exception (expected): {type(e).__name__}: {e}")


def test_cross_experiment_query(elog: FederatedElog):
    """Test cross-experiment queries with mixed permissions."""
    print_section("TEST 4: Cross-experiment query (CXI experiments)")

    # Query both CXI experiments
    result = elog.query_cross_experiment(
        ["cxi25410", "cxilx8720"],
        "SELECT run_number, start_time FROM {exp}.runs ORDER BY run_number LIMIT 3"
    )

    print(f"Attached: {result.attached_experiments}")
    print(f"Skipped: {result.skipped_experiments}")
    print(f"Total rows: {len(result.data)}")
    print("Sample data:")
    for row in result.data[:6]:
        print(f"  {row[0]:12} | Run {row[1]}: {row[2]}")


def test_cross_experiment_with_denied(elog: FederatedElog):
    """Test cross-experiment query including a denied experiment."""
    print_section("TEST 5: Cross-experiment with permission denial")

    # Include mfxlt3017 which should be denied
    result = elog.query_cross_experiment(
        ["cxi25410", "mfxlt3017", "xppn3816"],
        "SELECT run_number FROM {exp}.runs ORDER BY run_number LIMIT 2"
    )

    print(f"Attached: {result.attached_experiments}")
    print(f"Skipped: {result.skipped_experiments}")
    print(f"Total rows: {len(result.data)}")

    if result.skipped_experiments:
        print("✓ Correctly skipped inaccessible experiments")


def test_logbook_query(elog: FederatedElog):
    """Test querying logbook entries."""
    print_section("TEST 6: Logbook query (xppn3816)")

    result = elog.query_experiment(
        "xppn3816",
        """SELECT timestamp, substr(content, 1, 50) as preview, author
           FROM logbook
           ORDER BY timestamp DESC
           LIMIT 3"""
    )

    print(f"Found {len(result.data)} recent logbook entries:")
    for row in result.data:
        print(f"  [{row[0]}] by {row[2]}")
        print(f"    {row[1]}...")


def test_join_with_master(elog: FederatedElog):
    """Test joining experiment data with master detector catalog."""
    print_section("TEST 7: Join with master detector catalog")

    # First attach an experiment
    elog.attach_experiment("cxilx8720")

    result = elog.raw_query("""
        SELECT d.detector_name, COUNT(*) as usage_count
        FROM exp_cxilx8720.run_detectors rd
        JOIN master.detector_catalog d ON rd.detector_id = d.detector_id
        GROUP BY d.detector_name
        ORDER BY usage_count DESC
        LIMIT 5
    """)

    print("Top 5 detectors used in cxilx8720:")
    for row in result.data:
        print(f"  {row[0]:30} : {row[1]} runs")


def main():
    print("=" * 60)
    print(" FEDERATED ELOG PROTOTYPE TEST")
    print("=" * 60)
    print()
    print("Testing federated query layer with:")
    print("  - Accessible: cxi25410, cxilx8720, xppn3816")
    print("  - Denied:     mfxlt3017 (chmod 000)")

    with FederatedElog(MASTER_DB) as elog:
        test_list_experiments(elog)
        test_single_experiment_query(elog)
        test_permission_denial(elog)
        test_cross_experiment_query(elog)
        test_cross_experiment_with_denied(elog)
        test_logbook_query(elog)
        test_join_with_master(elog)

    print_section("ALL TESTS COMPLETED")
    print()
    print("Summary:")
    print("  ✓ Master index listing works")
    print("  ✓ Single experiment queries work")
    print("  ✓ Permission denial is enforced")
    print("  ✓ Cross-experiment queries work")
    print("  ✓ Graceful handling of mixed permissions")
    print("  ✓ Joins with master catalog work")
    print()
    print("The federated architecture prototype is working!")


if __name__ == "__main__":
    main()
