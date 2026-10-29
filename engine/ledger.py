"""
Recovery Ledger: a crash-safe, idempotent state machine for recovery actions.

Why this exists (say this explicitly in the demo):
A naive recovery agent is a script: detect -> call LLM -> take action -> next.
If that process dies mid-batch (crash, timeout, deploy), a naive agent has
no idea what it already did. Restarting risks double-charging a customer,
sending the same nudge twice, or silently skipping someone.

Every recovery action here is a durable, idempotent state transition:
  - Every action has a stable idempotency_key = hash(customer, invoice, attempt, action)
  - Actions are recorded PENDING -> EXECUTED (or FAILED) in a durable log
  - On restart, any action already EXECUTED is skipped -- proven no-op,
    not re-attempted
  - The full transition history IS the audit trail

Backed by SQLite (durable file, zero setup for a hackathon).
"""
import sqlite3
import hashlib
import json
import time
from contextlib import contextmanager

DB_PATH = "models/recovery_ledger.db"


def make_idempotency_key(customer_id, invoice_id, attempt_number, action):
    raw = f"{customer_id}:{invoice_id}:{attempt_number}:{action}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class RecoveryLedger:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS actions (
                    idempotency_key TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    invoice_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    root_cause TEXT,
                    expected_value REAL,
                    state TEXT NOT NULL,
                    detail_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    reason TEXT,
                    ts REAL NOT NULL
                )
            """)

    def get_action(self, key):
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM actions WHERE idempotency_key = ?", (key,))
            row = cur.fetchone()
            if row is None:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))

    def begin_action(self, customer_id, invoice_id, attempt_number, action, root_cause, expected_value, detail=None):
        key = make_idempotency_key(customer_id, invoice_id, attempt_number, action)
        existing = self.get_action(key)
        now = time.time()

        if existing is not None:
            if existing["state"] in ("EXECUTED", "SKIPPED"):
                self._log(key, existing["state"], "SKIPPED", "already executed -- idempotent no-op")
                return key, False, "EXECUTED"
            return key, True, existing["state"]

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO actions
                   (idempotency_key, customer_id, invoice_id, attempt_number, action,
                    root_cause, expected_value, state, detail_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)""",
                (key, customer_id, invoice_id, attempt_number, action, root_cause,
                 expected_value, json.dumps(detail or {}), now, now),
            )
        self._log(key, None, "PENDING", "action registered, awaiting execution")
        return key, True, None

    def complete_action(self, key, success, result_detail=None):
        state = "EXECUTED" if success else "FAILED"
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE actions SET state=?, detail_json=?, updated_at=? WHERE idempotency_key=?",
                (state, json.dumps(result_detail or {}), now, key),
            )
        self._log(key, "PENDING", state, "execution result recorded")

    def _log(self, key, from_state, to_state, reason):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO audit_log (idempotency_key, from_state, to_state, reason, ts) VALUES (?, ?, ?, ?, ?)",
                (key, from_state, to_state, reason, time.time()),
            )

    def history(self, limit=200):
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def summary(self):
        with self._conn() as conn:
            cur = conn.execute("SELECT state, COUNT(*), COALESCE(SUM(expected_value),0) FROM actions GROUP BY state")
            return {row[0]: {"count": row[1], "total_ev": round(row[2], 2)} for row in cur.fetchall()}


if __name__ == "__main__":
    ledger = RecoveryLedger()
    key, should_exec, existing = ledger.begin_action(
        "cust_1", "inv_100", 1, "retry_delayed", "insufficient_funds", 10998.0
    )
    print("First call -> should_execute:", should_exec, "key:", key)
    if should_exec:
        ledger.complete_action(key, success=True, result_detail={"note": "simulated retry succeeded"})

    key2, should_exec2, existing2 = ledger.begin_action(
        "cust_1", "inv_100", 1, "retry_delayed", "insufficient_funds", 10998.0
    )
    print("Replayed call after 'crash' -> should_execute:", should_exec2, "(must be False)")
    assert key == key2
    assert should_exec2 is False, "IDEMPOTENCY VIOLATION"
    print("Idempotency guarantee holds.")