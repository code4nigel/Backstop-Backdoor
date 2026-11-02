"""
Dispute Reconciliation & Freeze Engine.
When a customer claims payment was already made or disputes the invoice amount:
1. Immediately freezes all active outreach (updates case state to DISPUTED and aborts pending outbox dispatches).
2. Creates an auditable reconciliation task for the human operator queue.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, Union
import sqlite3
import time
from contextlib import contextmanager
import uuid

from engine.models import CaseState, RecoveryCase
from engine.execution.outbox import TransactionalOutbox


class DisputeType(str, Enum):
    ALREADY_PAID = "already_paid"
    AMOUNT_DISPUTE = "amount_dispute"
    SERVICE_ISSUE = "service_issue"
    FRAUD_CLAIM = "fraud_claim"


class DisputeStatus(str, Enum):
    PENDING_RECONCILIATION = "PENDING_RECONCILIATION"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"
    OPEN = "PENDING_RECONCILIATION"  # Shorthand compatibility


@dataclass
class ReconciliationTask:
    task_id: str
    case_id: str
    customer_id: str
    dispute_type: DisputeType
    evidence_notes: str
    status: DisputeStatus
    reported_at: float = field(default_factory=time.time)
    resolved_at: Optional[float] = None
    resolution_notes: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "case_id": self.case_id,
            "customer_id": self.customer_id,
            "dispute_type": self.dispute_type.value if isinstance(self.dispute_type, DisputeType) else str(self.dispute_type),
            "evidence_notes": self.evidence_notes,
            "status": self.status.value if isinstance(self.status, DisputeStatus) else str(self.status),
            "reported_at": self.reported_at,
            "resolved_at": self.resolved_at,
            "resolution_notes": self.resolution_notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ReconciliationTask":
        return cls(
            task_id=row["task_id"],
            case_id=row["case_id"],
            customer_id=row["customer_id"],
            dispute_type=DisputeType(row["dispute_type"]),
            evidence_notes=row["evidence_notes"] or "",
            status=DisputeStatus(row["status"]),
            reported_at=float(row["reported_at"]),
            resolved_at=float(row["resolved_at"]) if row["resolved_at"] else None,
            resolution_notes=row["resolution_notes"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )


# Alias
DisputeTask = ReconciliationTask


class DisputeManager:
    """
    Coordinates dispute freezes, outbox aborts, and human reconciliation queue tasks.
    """
    def __init__(self, db_path: str = "models/disputes.db", outbox: Optional[TransactionalOutbox] = None):
        self.db_path = db_path
        self.outbox = outbox
        self._is_memory = db_path == ":memory:"
        self._mem_conn: Optional[sqlite3.Connection] = None
        if self._is_memory:
            self._mem_conn = sqlite3.connect(":memory:")
            self._mem_conn.row_factory = sqlite3.Row
        self._init_schema()

    @contextmanager
    def _conn(self):
        if self._is_memory and self._mem_conn:
            yield self._mem_conn
            self._mem_conn.commit()
        else:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dispute_tasks (
                    task_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    customer_id TEXT NOT NULL,
                    dispute_type TEXT NOT NULL,
                    evidence_notes TEXT,
                    status TEXT NOT NULL,
                    reported_at REAL NOT NULL,
                    resolved_at REAL,
                    resolution_notes TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dispute_case ON dispute_tasks (case_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dispute_status ON dispute_tasks (status)")

    def raise_dispute(
        self,
        case_id: str,
        customer_id: str,
        dispute_type: Union[str, DisputeType],
        evidence_notes: str,
        case: Optional[RecoveryCase] = None,
        outbox: Optional[TransactionalOutbox] = None,
    ) -> ReconciliationTask:
        """
        Executes immediate dispute workflow:
        1. Transitions case state to CaseState.DISPUTED.
        2. Aborts all pending/scheduled outbox dispatches for this case.
        3. Creates structured reconciliation task for operator dashboard.
        """
        dtype = dispute_type if isinstance(dispute_type, DisputeType) else DisputeType(dispute_type)

        # 1. Update Case State
        if case is not None:
            case.state = CaseState.DISPUTED

        # 2. Freeze & abort any scheduled outbox dispatches
        ob = outbox or self.outbox
        if ob is not None:
            ob.abort_case(case_id, reason=f"Dispute raised: {dtype.value} - {evidence_notes}")

        # 3. Create Reconciliation Task
        task_id = f"task_disp_{uuid.uuid4().hex[:10]}"
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO dispute_tasks (
                    task_id, case_id, customer_id, dispute_type, evidence_notes,
                    status, reported_at, resolved_at, resolution_notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    case_id,
                    customer_id,
                    dtype.value,
                    evidence_notes,
                    DisputeStatus.PENDING_RECONCILIATION.value,
                    now,
                    None,
                    None,
                    now,
                    now,
                ),
            )

        return self.get_task(task_id)  # type: ignore

    def freeze_case(self, case_id: str, customer_id: str, dispute_type: str, notes: str) -> ReconciliationTask:
        """Alias for raise_dispute."""
        return self.raise_dispute(case_id, customer_id, dispute_type, notes)

    def get_task(self, task_id: str) -> Optional[ReconciliationTask]:
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM dispute_tasks WHERE task_id = ?", (task_id,))
            row = cur.fetchone()
            return ReconciliationTask.from_row(row) if row else None

    def get_pending_tasks(self) -> List[ReconciliationTask]:
        """Surfaces tasks in the dashboard under 'Disputed Cases Awaiting Human Reconciliation'."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM dispute_tasks WHERE status = ? ORDER BY reported_at ASC",
                (DisputeStatus.PENDING_RECONCILIATION.value,),
            )
            return [ReconciliationTask.from_row(r) for r in cur.fetchall()]

    def get_open_tasks(self) -> List[Dict[str, Any]]:
        return [t.to_dict() for t in self.get_pending_tasks()]

    def is_case_disputed(self, case_id: str) -> bool:
        """Checks if there is an active/pending dispute on a case."""
        with self._conn() as conn:
            cur = conn.execute(
                """SELECT 1 FROM dispute_tasks
                   WHERE case_id = ? AND status IN (?, ?)""",
                (case_id, DisputeStatus.PENDING_RECONCILIATION.value, DisputeStatus.UNDER_REVIEW.value),
            )
            return cur.fetchone() is not None

    def is_frozen(self, case_id: str) -> bool:
        return self.is_case_disputed(case_id)

    def resolve_dispute(
        self,
        task_id: str,
        resolution_status: Union[str, DisputeStatus],
        resolution_notes: str,
        case: Optional[RecoveryCase] = None,
    ) -> Optional[ReconciliationTask]:
        """Resolves dispute task following human operator decision."""
        status = resolution_status if isinstance(resolution_status, DisputeStatus) else DisputeStatus(resolution_status)
        now = time.time()

        with self._conn() as conn:
            conn.execute(
                """UPDATE dispute_tasks
                   SET status = ?, resolved_at = ?, resolution_notes = ?, updated_at = ?
                   WHERE task_id = ?""",
                (status.value, now, resolution_notes, now, task_id),
            )

        task = self.get_task(task_id)
        if task and case is not None:
            if status == DisputeStatus.RESOLVED:
                case.state = CaseState.RECOVERED
            elif status == DisputeStatus.REJECTED:
                case.state = CaseState.READY

        return task

    def resolve_task(self, case_id: str, resolution: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                """SELECT task_id FROM dispute_tasks
                   WHERE case_id = ? AND status = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (case_id, DisputeStatus.PENDING_RECONCILIATION.value),
            )
            row = cur.fetchone()
            if not row:
                return False
            task_id = row["task_id"]
        self.resolve_dispute(task_id, resolution, "Resolved via resolve_task")
        return True
