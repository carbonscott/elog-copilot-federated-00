#!/usr/bin/env python3
"""
federated_elog.py - Federated Elog Query Layer

Uses DuckDB to dynamically attach per-experiment SQLite databases,
leveraging filesystem permissions for access control.
"""

import duckdb
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Any


@dataclass
class QueryResult:
    """Result of a federated query."""
    data: List[Tuple]
    columns: List[str]
    attached_experiments: List[str] = field(default_factory=list)
    skipped_experiments: List[Tuple[str, str]] = field(default_factory=list)  # (exp_id, reason)


class FederatedElog:
    """
    Federated query layer for elog-copilot databases.

    Uses DuckDB to dynamically attach per-experiment SQLite databases,
    leveraging filesystem permissions for access control.
    """

    def __init__(self, master_db_path: str):
        """
        Initialize the federated query layer.

        Args:
            master_db_path: Path to the master index database
        """
        self.master_db_path = Path(master_db_path)
        if not self.master_db_path.exists():
            raise FileNotFoundError(f"Master database not found: {master_db_path}")

        # Create DuckDB connection and load SQLite extension
        self.conn = duckdb.connect(":memory:")
        self.conn.execute("INSTALL sqlite; LOAD sqlite;")

        # Attach master database
        self.conn.execute(f"ATTACH '{self.master_db_path}' AS master (TYPE sqlite)")

        # Track attached experiment databases
        self._attached: List[str] = []

    def list_experiments(self, instrument: Optional[str] = None) -> List[dict]:
        """
        List available experiments from master index.

        Args:
            instrument: Optional filter by instrument (e.g., 'CXI', 'MFX')

        Returns:
            List of experiment records
        """
        sql = "SELECT * FROM master.experiment_index"
        if instrument:
            sql += f" WHERE instrument = '{instrument}'"
        sql += " ORDER BY experiment_id"

        result = self.conn.execute(sql).fetchall()
        columns = [desc[0] for desc in self.conn.description]
        return [dict(zip(columns, row)) for row in result]

    def _get_experiment_paths(self, experiment_ids: List[str]) -> List[Tuple[str, str]]:
        """
        Look up database paths for given experiment IDs.

        Args:
            experiment_ids: List of experiment IDs

        Returns:
            List of (experiment_id, db_path) tuples
        """
        if not experiment_ids:
            return []

        placeholders = ", ".join(f"'{eid}'" for eid in experiment_ids)
        sql = f"""
            SELECT experiment_id, db_path
            FROM master.experiment_index
            WHERE experiment_id IN ({placeholders})
        """
        return self.conn.execute(sql).fetchall()

    def attach_experiment(self, exp_id: str) -> Tuple[bool, str]:
        """
        Attach a single experiment database.

        Args:
            exp_id: Experiment ID

        Returns:
            Tuple of (success, message)
        """
        if exp_id in self._attached:
            return True, "Already attached"

        paths = self._get_experiment_paths([exp_id])
        if not paths:
            return False, f"Experiment not found: {exp_id}"

        _, db_path = paths[0]

        # Check if file is readable
        if not os.access(db_path, os.R_OK):
            return False, f"Permission denied: {db_path}"

        try:
            self.conn.execute(f"ATTACH '{db_path}' AS exp_{exp_id} (TYPE sqlite)")
            self._attached.append(exp_id)
            return True, "Attached"
        except Exception as e:
            error_msg = str(e)
            if "Permission denied" in error_msg or "unable to open" in error_msg.lower():
                return False, "Permission denied"
            return False, error_msg

    def attach_experiments(self, experiment_ids: List[str]) -> Tuple[List[str], List[Tuple[str, str]]]:
        """
        Attach multiple experiment databases.

        Args:
            experiment_ids: List of experiment IDs

        Returns:
            Tuple of (attached_ids, skipped_with_reasons)
        """
        attached = []
        skipped = []

        for exp_id in experiment_ids:
            success, msg = self.attach_experiment(exp_id)
            if success:
                attached.append(exp_id)
            else:
                skipped.append((exp_id, msg))

        return attached, skipped

    def detach_experiment(self, exp_id: str):
        """Detach a single experiment database."""
        if exp_id in self._attached:
            try:
                self.conn.execute(f"DETACH exp_{exp_id}")
                self._attached.remove(exp_id)
            except Exception:
                pass

    def detach_all(self):
        """Detach all experiment databases."""
        for exp_id in self._attached.copy():
            self.detach_experiment(exp_id)

    def query_experiment(self, experiment_id: str, sql: str) -> QueryResult:
        """
        Query a single experiment database.

        Args:
            experiment_id: The experiment to query
            sql: SQL query (tables without schema prefix)

        Returns:
            QueryResult
        """
        success, msg = self.attach_experiment(experiment_id)
        if not success:
            raise PermissionError(f"Cannot access experiment {experiment_id}: {msg}")

        # Rewrite SQL to use schema prefix
        import re
        prefixed_sql = sql
        tables = ['runs', 'logbook', 'questionnaire', 'workflows',
                  'run_production_data', 'run_detectors', 'experiment', 'metadata']
        for table in tables:
            prefixed_sql = re.sub(
                rf'\b{table}\b',
                f'exp_{experiment_id}.{table}',
                prefixed_sql,
                flags=re.IGNORECASE
            )

        try:
            result = self.conn.execute(prefixed_sql)
            data = result.fetchall()
            columns = [desc[0] for desc in result.description] if result.description else []
        except Exception as e:
            raise RuntimeError(f"Query failed: {e}")

        return QueryResult(
            data=data,
            columns=columns,
            attached_experiments=[experiment_id],
            skipped_experiments=[]
        )

    def query_cross_experiment(self, experiment_ids: List[str], sql_template: str) -> QueryResult:
        """
        Query across multiple experiments using UNION ALL.

        Args:
            experiment_ids: List of experiments to query
            sql_template: SQL query template (use {exp} placeholder for schema)

        Returns:
            QueryResult with combined data
        """
        attached, skipped = self.attach_experiments(experiment_ids)

        if not attached:
            return QueryResult(
                data=[],
                columns=[],
                attached_experiments=[],
                skipped_experiments=skipped
            )

        # Build UNION ALL query
        union_parts = []
        for exp_id in attached:
            exp_sql = sql_template.replace("{exp}", f"exp_{exp_id}")
            # Add experiment_id column for tracking
            exp_sql = f"SELECT '{exp_id}' as experiment_id, * FROM ({exp_sql})"
            union_parts.append(exp_sql)

        full_sql = " UNION ALL ".join(union_parts)

        try:
            result = self.conn.execute(full_sql)
            data = result.fetchall()
            columns = [desc[0] for desc in result.description] if result.description else []
        except Exception as e:
            raise RuntimeError(f"Query failed: {e}")

        return QueryResult(
            data=data,
            columns=columns,
            attached_experiments=attached,
            skipped_experiments=skipped
        )

    def raw_query(self, sql: str) -> QueryResult:
        """
        Execute a raw SQL query (for advanced use).

        Assumes experiments are already attached with exp_{id} schema names.
        """
        result = self.conn.execute(sql)
        data = result.fetchall()
        columns = [desc[0] for desc in result.description] if result.description else []

        return QueryResult(
            data=data,
            columns=columns,
            attached_experiments=self._attached.copy(),
            skipped_experiments=[]
        )

    def close(self):
        """Clean up resources."""
        self.detach_all()
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
