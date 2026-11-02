"""
Recovery Ledger: Crash-safe, idempotent state machine and audit trail for Backstop.

Features:
- Partitioned by run_id to guarantee zero cross-run or benchmark contamination
- SQLite WAL mode for concurrency and crash-safety
- Zero-overhead in-memory mode for unit tests and benchmarks
- Outbox persistence for asynchronous dispatch reconciliation
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

from engine.config import DEFAULT_RUN_ID, SQLITE_DB_PATH


def make_idempotency_key(
    customer_id: str,
    case_id: Optional[str] = None,
    attempt_number: int = 1,
    action: str = "",
    invoice_id: Optional[str] = None,
) -> str:
    """Generate a stable 24-character SHA256 idempotency key.

    Args:
        customer_id: Unique customer ID
        case_id: Case ID (or invoice_id)
        attempt_number: Sequence attempt count
        action: Recovery action name
        invoice_id: Compatibility alias for case_id

    Returns:
        24-character hex string
    """
    cid = str(case_id if case_id is not None else (invoice_id or ""))
    raw = f"{customer_id}:{cid}:{attempt_number}:{action}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class InflightRecoveryList(list):
    """List of inflight actions that also supports integer/count evaluation."""

    def __int__(self) -> int:
        return len(self)

    @property
    def count(self) -> int:
        return len(self)


class RecoveryLedger:
    """Idempotent SQLite recovery ledger partitioned by run_id."""

    def __init__(
        self,
        db_path: str = SQLITE_DB_PATH,
        default_run_id: str = DEFAULT_RUN_ID,
    ) -> None:
        """Initialize the ledger.

        Args:
            db_path: Path to SQLite file or ":memory:" for in-memory mode.
            default_run_id: Default run partition.
        """
        self.db_path = db_path
        self.default_run_id = default_run_id
        self._is_memory = (db_path == ":memory:")
        self._lock = threading.RLock()
        self._mem_conn: Optional[sqlite3.Connection] = None

        if self._is_memory:
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
        else:
            db_dir = os.path.dirname(db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

        self._init_schema()
        self.create_run(self.default_run_id, description="Default operational run partition")

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        """Context-managed connection handling thread safety and WAL mode."""
        with self._lock:
            if self._is_memory:
                assert self._mem_conn is not None
                try:
                    yield self._mem_conn
                    self._mem_conn.commit()
                except Exception:
                    self._mem_conn.rollback()
                    raise
            else:
                conn = sqlite3.connect(self.db_path, timeout=30.0)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()

    def _init_schema(self) -> None:
        """Initialize partitioned database schema."""
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    description TEXT,
                    status TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS actions (
                    idempotency_key TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    customer_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    root_cause TEXT,
                    expected_value REAL,
                    state TEXT NOT NULL,
                    detail_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (run_id, idempotency_key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    rule_or_action TEXT,
                    details_json TEXT,
                    ts REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outbox (
                    idempotency_key TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    customer_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    provider_ref TEXT,
                    error_message TEXT,
                    metadata_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_actions_run_case ON actions(run_id, case_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_run_case ON audit_events(run_id, case_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_outbox_run_case ON outbox(run_id, case_id)")

    def create_run(
        self,
        run_id: str,
        description: str = "",
        status: str = "ACTIVE",
    ) -> Dict[str, Any]:
        """Create or ensure a run partition exists."""
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO runs (run_id, created_at, description, status)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    description = CASE WHEN excluded.description != '' THEN excluded.description ELSE runs.description END,
                    status = excluded.status
                """,
                (run_id, now, description, status),
            )
        return {"run_id": run_id, "created_at": now, "description": description, "status": status}

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve run partition record."""
        with self._conn() as conn:
            cur = conn.execute("SELECT run_id, created_at, description, status FROM runs WHERE run_id = ?", (run_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {"run_id": row["run_id"], "created_at": row["created_at"], "description": row["description"], "status": row["status"]}

    def list_runs(self) -> List[Dict[str, Any]]:
        """List all run partitions."""
        with self._conn() as conn:
            cur = conn.execute("SELECT run_id, created_at, description, status FROM runs ORDER BY created_at DESC")
            return [
                {"run_id": row["run_id"], "created_at": row["created_at"], "description": row["description"], "status": row["status"]}
                for row in cur.fetchall()
            ]

    def begin_action(
        self,
        run_id: str,
        customer_id: Optional[str] = None,
        case_id: Optional[Union[str, int]] = None,
        attempt_number: Optional[Union[int, str]] = 1,
        action: str = "",
        root_cause: str = "",
        expected_value: float = 0.0,
        detail: Optional[Dict[str, Any]] = None,
        invoice_id: Optional[str] = None,
    ) -> Tuple[str, bool, Optional[str]]:
        """Atomically register and check if an action should execute.

        Supports partitioned call:
            begin_action(run_id, customer_id, case_id, attempt_number, action, root_cause, expected_value, detail)
        and legacy 7-parameter call:
            begin_action(customer_id, invoice_id, attempt_number, action, root_cause, expected_value, detail)

        Returns:
            (key, should_execute, existing_state)
        """
        if isinstance(case_id, int):
            actual_run_id = self.default_run_id
            actual_customer_id = str(run_id)
            actual_case_id = str(customer_id)
            actual_attempt_number = int(case_id)
            actual_action = str(attempt_number)
            actual_root_cause = str(action)
            actual_expected_value = float(root_cause) if root_cause else 0.0
            actual_detail = detail if isinstance(detail, dict) else (expected_value if isinstance(expected_value, dict) else None)
        else:
            actual_run_id = str(run_id)
            actual_customer_id = str(customer_id or "")
            actual_case_id = str(case_id or invoice_id or "")
            actual_attempt_number = int(attempt_number or 1)
            actual_action = str(action or "")
            actual_root_cause = str(root_cause or "")
            actual_expected_value = float(expected_value or 0.0)
            actual_detail = detail

        self.create_run(actual_run_id)

        key = make_idempotency_key(
            customer_id=actual_customer_id,
            case_id=actual_case_id,
            attempt_number=actual_attempt_number,
            action=actual_action,
        )

        now = time.time()
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT state FROM actions WHERE run_id = ? AND idempotency_key = ?",
                (actual_run_id, key),
            )
            row = cur.fetchone()
            if row:
                existing_state = row["state"]
                if existing_state in ("EXECUTED", "SKIPPED", "COMPLETED", "FAILED", "CONFIRMED"):
                    self.log_audit_event(
                        run_id=actual_run_id,
                        case_id=actual_case_id,
                        from_state=existing_state,
                        to_state="SKIPPED",
                        rule_or_action=actual_action,
                        details={"reason": "already executed -- idempotent no-op", "key": key},
                    )
                    return key, False, existing_state
                return key, True, existing_state

            cur.execute(
                """
                INSERT INTO actions (
                    idempotency_key, run_id, customer_id, case_id, attempt_number,
                    action, root_cause, expected_value, state, detail_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)
                """,
                (
                    key,
                    actual_run_id,
                    actual_customer_id,
                    actual_case_id,
                    actual_attempt_number,
                    actual_action,
                    actual_root_cause,
                    actual_expected_value,
                    json.dumps(actual_detail or {}),
                    now,
                    now,
                ),
            )

        self.log_audit_event(
            run_id=actual_run_id,
            case_id=actual_case_id,
            from_state=None,
            to_state="PENDING",
            rule_or_action=actual_action,
            details={"detail": actual_detail or {}, "key": key},
        )
        return key, True, "PENDING"

    def complete_action(
        self,
        run_id: Optional[str] = None,
        key: Optional[Union[str, bool]] = None,
        success: Union[bool, Optional[Dict[str, Any]]] = True,
        result_detail: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> bool:
        """Mark an action as EXECUTED or FAILED in the ledger.

        Handles all calling conventions:
        - complete_action(run_id, key, success=True, result_detail={})
        - complete_action(run_id, key, success, result_detail)
        - complete_action(key, success=True, result_detail={})
        - complete_action(key, True, result_detail)
        """
        # Resolve arguments
        if isinstance(key, bool):
            # Positional legacy: complete_action("key123", True, detail)
            actual_run_id = self.default_run_id
            actual_key = str(run_id)
            actual_success = key
            actual_detail = success if isinstance(success, dict) else (result_detail or {})
        elif key is None:
            # 1-arg positional or legacy keyword: complete_action("key123", success=True, result_detail=...)
            actual_run_id = self.default_run_id
            actual_key = str(run_id)
            actual_success = bool(success if not isinstance(success, dict) else kwargs.get("success", True))
            actual_detail = result_detail if isinstance(result_detail, dict) else (success if isinstance(success, dict) else kwargs.get("result_detail", {}))
        else:
            actual_run_id = str(run_id if run_id is not None else kwargs.get("run_id", self.default_run_id))
            actual_key = str(key)
            actual_success = bool(success if not isinstance(success, dict) else kwargs.get("success", True))
            actual_detail = result_detail if isinstance(result_detail, dict) else (success if isinstance(success, dict) else kwargs.get("result_detail", {}))

        target_state = "EXECUTED" if actual_success else "FAILED"
        if isinstance(actual_detail, dict) and "state" in actual_detail:
            target_state = actual_detail["state"]

        now = time.time()
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT case_id, action, detail_json FROM actions WHERE run_id = ? AND idempotency_key = ?",
                (actual_run_id, actual_key),
            )
            row = cur.fetchone()
            existing_detail = json.loads(row["detail_json"]) if (row and row["detail_json"]) else {}
            if isinstance(actual_detail, dict):
                existing_detail.update(actual_detail)

            cur.execute(
                """
                UPDATE actions
                SET state = ?, detail_json = ?, updated_at = ?
                WHERE run_id = ? AND idempotency_key = ?
                """,
                (target_state, json.dumps(existing_detail), now, actual_run_id, actual_key),
            )
            updated = cur.rowcount > 0

        case_id = row["case_id"] if row else "unknown"
        action_name = row["action"] if row else "unknown"

        self.log_audit_event(
            run_id=actual_run_id,
            case_id=case_id,
            from_state="PENDING",
            to_state=target_state,
            rule_or_action=action_name,
            details=actual_detail if isinstance(actual_detail, dict) else {},
        )
        return updated

    def log_audit_event(
        self,
        run_id: str,
        case_id: str,
        from_state: Optional[str],
        to_state: str,
        rule_or_action: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Insert an immutable audit event record."""
        now = time.time()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO audit_events (run_id, case_id, from_state, to_state, rule_or_action, details_json, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, case_id, from_state, to_state, rule_or_action, json.dumps(details or {}), now),
            )
            return cur.lastrowid or 0

    def get_action(self, run_id: str, key: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieve an action by (run_id, key), or (key) using default_run_id."""
        if key is None:
            actual_run_id = self.default_run_id
            actual_key = run_id
        else:
            actual_run_id = run_id
            actual_key = key

        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM actions WHERE run_id = ? AND idempotency_key = ?",
                (actual_run_id, actual_key),
            )
            row = cur.fetchone()
            if not row:
                return None
            res = dict(row)
            if "detail_json" in res and res["detail_json"]:
                try:
                    res["detail"] = json.loads(res["detail_json"])
                except Exception:
                    res["detail"] = {}
            return res

    def get_case_history(self, run_id: str, case_id: str) -> List[Dict[str, Any]]:
        """Return full ordered transition audit trail for a case."""
        with self._conn() as conn:
            cur = conn.execute(
                """
                SELECT id, run_id, case_id, from_state, to_state, rule_or_action, details_json, ts
                FROM audit_events
                WHERE run_id = ? AND case_id = ?
                ORDER BY id ASC
                """,
                (run_id, case_id),
            )
            history = []
            for r in cur.fetchall():
                try:
                    parsed = json.loads(r["details_json"]) if r["details_json"] else {}
                except Exception:
                    parsed = {}
                history.append({
                    "id": r["id"],
                    "run_id": r["run_id"],
                    "case_id": r["case_id"],
                    "from_state": r["from_state"],
                    "to_state": r["to_state"],
                    "rule_or_action": r["rule_or_action"],
                    "details": parsed,
                    "timestamp": r["ts"],
                })
            return history

    def recover_inflight_actions(self, run_id: str) -> InflightRecoveryList:
        """Find in-flight PENDING actions (mid-flight or post-crash).

        Returns:
            InflightRecoveryList supporting both list operations and count/len.
        """
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM actions WHERE run_id = ? AND state = 'PENDING' ORDER BY created_at ASC",
                (run_id,),
            )
            items = [dict(row) for row in cur.fetchall()]
            return InflightRecoveryList(items)

    def purge_run(self, run_id: str) -> Dict[str, int]:
        """Purge all actions, outbox, audit events, and run record for run_id."""
        with self._conn() as conn:
            c1 = conn.execute("DELETE FROM actions WHERE run_id = ?", (run_id,)).rowcount
            c2 = conn.execute("DELETE FROM audit_events WHERE run_id = ?", (run_id,)).rowcount
            c3 = conn.execute("DELETE FROM outbox WHERE run_id = ?", (run_id,)).rowcount
            c4 = conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,)).rowcount

        return {"actions_purged": c1, "audit_events_purged": c2, "outbox_purged": c3, "runs_purged": c4}

    def summary(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        """Return aggregated action metrics.

        Supports both state-keyed dicts:
            summary["EXECUTED"]["count"]
        and direct property access:
            summary["executed"], summary["total_actions"], summary["by_state"]
        """
        query = "SELECT state, count(*) as cnt, COALESCE(SUM(expected_value), 0.0) as total_ev FROM actions"
        params: Tuple[Any, ...] = ()
        if run_id:
            query += " WHERE run_id = ?"
            params = (run_id,)
        query += " GROUP BY state"

        with self._conn() as conn:
            cur = conn.execute(query, params)
            rows = cur.fetchall()

        by_state = {}
        res: Dict[str, Any] = {}
        total_actions = 0

        for r in rows:
            st = r["state"]
            cnt = r["cnt"]
            ev = round(float(r["total_ev"]), 2)
            total_actions += cnt
            by_state[st] = cnt
            res[st] = {"count": cnt, "total_ev": ev}

        # Common aliases
        res["total_actions"] = total_actions
        res["executed"] = by_state.get("EXECUTED", 0)
        res["pending"] = by_state.get("PENDING", 0)
        res["failed"] = by_state.get("FAILED", 0)
        res["by_state"] = by_state

        for st in ("EXECUTED", "PENDING", "FAILED", "SKIPPED"):
            if st not in res:
                res[st] = {"count": 0, "total_ev": 0.0}

        return res

    def history(self, run_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Return audit trail for API or reporting."""
        with self._conn() as conn:
            if run_id:
                cur = conn.execute(
                    "SELECT id, run_id, case_id, from_state, to_state, rule_or_action, details_json, ts FROM audit_events WHERE run_id = ? ORDER BY id DESC LIMIT ?",
                    (run_id, limit),
                )
            else:
                cur = conn.execute(
                    "SELECT id, run_id, case_id, from_state, to_state, rule_or_action, details_json, ts FROM audit_events ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            out = []
            for r in cur.fetchall():
                try:
                    details = json.loads(r["details_json"]) if r["details_json"] else {}
                except Exception:
                    details = {}
                out.append({
                    "id": r["id"],
                    "run_id": r["run_id"],
                    "case_id": r["case_id"],
                    "from_state": r["from_state"],
                    "to_state": r["to_state"],
                    "rule_or_action": r["rule_or_action"],
                    "details": details,
                    "timestamp": r["ts"],
                })
            return out

    def close(self) -> None:
        """Close persistent memory connection if open."""
        with self._lock:
            if self._mem_conn is not None:
                try:
                    self._mem_conn.close()
                except Exception:
                    pass
                self._mem_conn = None
