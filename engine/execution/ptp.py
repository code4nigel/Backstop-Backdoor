"""
Promise-to-Pay (PTP) Lifecycle Tracker.
Tracks promises (promise date, reminder scheduling, kept/broken states),
freezes active outreach until the promise date, and enforces escalation policies
on broken promises.
"""
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple, Union
import sqlite3
import time
from contextlib import contextmanager
import uuid

from engine.models import CaseState, ActionType, RecoveryCase


class PTPState(str, Enum):
    ACTIVE = "ACTIVE"
    KEPT = "KEPT"
    BROKEN = "BROKEN"
    CANCELLED = "CANCELLED"


@dataclass
class PromiseRecord:
    promise_id: str
    case_id: str
    customer_id: str
    promise_date: str  # YYYY-MM-DD
    promised_amount: float
    state: PTPState
    broken_count: int = 0
    is_habitual_breaker: bool = False
    reminder_scheduled_at: Optional[str] = None
    notes: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def status(self) -> str:
        return self.state.value if isinstance(self.state, PTPState) else str(self.state)

    @status.setter
    def status(self, val: str):
        self.state = PTPState(val)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "promise_id": self.promise_id,
            "case_id": self.case_id,
            "customer_id": self.customer_id,
            "promise_date": self.promise_date,
            "promised_amount": self.promised_amount,
            "state": self.state.value if isinstance(self.state, PTPState) else str(self.state),
            "status": self.status,
            "broken_count": self.broken_count,
            "is_habitual_breaker": self.is_habitual_breaker,
            "reminder_scheduled_at": self.reminder_scheduled_at,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PromiseRecord":
        return cls(
            promise_id=row["promise_id"],
            case_id=row["case_id"],
            customer_id=row["customer_id"],
            promise_date=row["promise_date"],
            promised_amount=float(row["promised_amount"]),
            state=PTPState(row["state"]),
            broken_count=int(row["broken_count"]),
            is_habitual_breaker=bool(row["is_habitual_breaker"]),
            reminder_scheduled_at=row["reminder_scheduled_at"],
            notes=row["notes"] or "",
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )


# Alias
PTPRecord = PromiseRecord


@dataclass
class PTPEvaluationResult:
    case_id: str
    state: PTPState
    promised_amount: float
    paid_amount: float
    is_habitual_breaker: bool
    recommended_action: ActionType
    policy_action: str
    notes: str


class PromiseToPayTracker:
    """
    Manages Promise-to-Pay (PTP) lifecycles, freezing outreach until the promise date,
    scheduling courteous 9:00 AM reminders, and enforcing broken promise policies.
    """
    def __init__(self, db_path: str = "models/ptp.db"):
        self.db_path = db_path
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
                CREATE TABLE IF NOT EXISTS ptp_records (
                    promise_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    customer_id TEXT NOT NULL,
                    promise_date TEXT NOT NULL,
                    promised_amount REAL NOT NULL,
                    state TEXT NOT NULL,
                    broken_count INTEGER DEFAULT 0,
                    is_habitual_breaker INTEGER DEFAULT 0,
                    reminder_scheduled_at TEXT,
                    notes TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ptp_case ON ptp_records (case_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ptp_customer ON ptp_records (customer_id)")

    def _parse_date(self, d: Union[str, date, datetime]) -> date:
        if isinstance(d, datetime):
            return d.date()
        elif isinstance(d, date):
            return d
        return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()

    def register_promise(
        self,
        case_id: str,
        customer_id: str,
        promise_date: Union[str, date, datetime],
        promised_amount: float,
        case: Optional[RecoveryCase] = None,
        notes: str = "",
    ) -> PromiseRecord:
        """
        Registers customer commitment to pay on a future date.
        - Updates case state to CaseState.PROMISED.
        - Freezes active outreach until 1 day prior to the promise date.
        - Schedules courteous reminder for 9:00 AM on the promised date.
        """
        p_date = self._parse_date(promise_date)
        p_date_str = p_date.strftime("%Y-%m-%d")
        reminder_time_str = f"{p_date_str} 09:00:00"

        with self._conn() as conn:
            cur = conn.execute(
                "SELECT COALESCE(MAX(broken_count), 0) as max_broken FROM ptp_records WHERE customer_id = ?",
                (customer_id,),
            )
            row = cur.fetchone()
            broken_count = row["max_broken"] if row else 0
            is_habitual = broken_count >= 2

            conn.execute(
                "UPDATE ptp_records SET state = ?, updated_at = ? WHERE case_id = ? AND state = ?",
                (PTPState.CANCELLED.value, time.time(), case_id, PTPState.ACTIVE.value),
            )

            promise_id = f"ptp_{uuid.uuid4().hex[:10]}"
            now = time.time()
            conn.execute(
                """INSERT INTO ptp_records (
                    promise_id, case_id, customer_id, promise_date, promised_amount,
                    state, broken_count, is_habitual_breaker, reminder_scheduled_at,
                    notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    promise_id,
                    case_id,
                    customer_id,
                    p_date_str,
                    promised_amount,
                    PTPState.ACTIVE.value,
                    broken_count,
                    1 if is_habitual else 0,
                    reminder_time_str,
                    notes,
                    now,
                    now,
                ),
            )

        if case is not None:
            case.state = CaseState.PROMISED

        return self.get_promise(promise_id)  # type: ignore

    def get_promise(self, promise_id: str) -> Optional[PromiseRecord]:
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM ptp_records WHERE promise_id = ?", (promise_id,))
            row = cur.fetchone()
            return PromiseRecord.from_row(row) if row else None

    def get_active_promise_for_case(self, case_id: str) -> Optional[PromiseRecord]:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM ptp_records WHERE case_id = ? AND state = ? ORDER BY created_at DESC LIMIT 1",
                (case_id, PTPState.ACTIVE.value),
            )
            row = cur.fetchone()
            return PromiseRecord.from_row(row) if row else None

    def is_promise_active(self, case_id: str) -> bool:
        return self.get_active_promise_for_case(case_id) is not None

    def is_outreach_frozen(
        self,
        case_id: str,
        current_date: Union[str, date, datetime],
    ) -> bool:
        """
        Rule: All active outreach is frozen until 1 day prior to the promise date.
        If current_date < (promise_date - 1 day), outreach is FROZEN (True).
        """
        active = self.get_active_promise_for_case(case_id)
        if not active:
            return False

        today = self._parse_date(current_date)
        p_date = self._parse_date(active.promise_date)

        freeze_until = p_date - timedelta(days=1)
        if today < freeze_until:
            return True
        return False

    def evaluate_promise(
        self,
        case_id: str,
        current_date: Optional[Union[str, date, datetime, float, int]] = None,
        payments_ledger: Optional[Union[float, int, bool, Dict[str, Any], List[Dict[str, Any]]]] = 0.0,
        case: Optional[RecoveryCase] = None,
        paid_amount: Optional[float] = None,
        is_date_passed: Optional[bool] = None,
        **kwargs,
    ) -> Union[PTPEvaluationResult, str]:
        """
        Evaluates active promise against actual payments.
        Supports both full specification:
            evaluate_promise(case_id, current_date, payments_ledger, case=None)
        and shorthand signature:
            evaluate_promise(case_id, paid_amount, is_date_passed)
        """
        active = self.get_active_promise_for_case(case_id)
        if not active:
            if is_date_passed is not None or isinstance(payments_ledger, bool):
                return "NO_PROMISE"
            raise ValueError(f"No active promise found for case {case_id}")

        # Shorthand 1: keyword paid_amount / is_date_passed
        if paid_amount is not None and is_date_passed is not None:
            now = time.time()
            if paid_amount >= active.promised_amount:
                with self._conn() as conn:
                    conn.execute("UPDATE ptp_records SET state = ?, updated_at = ? WHERE promise_id = ?", (PTPState.KEPT.value, now, active.promise_id))
                return "KEPT"
            elif is_date_passed:
                with self._conn() as conn:
                    conn.execute("UPDATE ptp_records SET state = ?, broken_count = broken_count + 1, updated_at = ? WHERE promise_id = ?", (PTPState.BROKEN.value, now, active.promise_id))
                return "BROKEN"
            return "ACTIVE"

        # Shorthand 2: positional float + bool (e.g. evaluate_promise("case1", 4500.0, True))
        if isinstance(current_date, (int, float)) and isinstance(payments_ledger, bool):
            p_amt = float(current_date)
            p_passed = payments_ledger
            now = time.time()
            if p_amt >= active.promised_amount:
                with self._conn() as conn:
                    conn.execute("UPDATE ptp_records SET state = ?, updated_at = ? WHERE promise_id = ?", (PTPState.KEPT.value, now, active.promise_id))
                return "KEPT"
            elif p_passed:
                with self._conn() as conn:
                    conn.execute("UPDATE ptp_records SET state = ?, broken_count = broken_count + 1, updated_at = ? WHERE promise_id = ?", (PTPState.BROKEN.value, now, active.promise_id))
                return "BROKEN"
            return "ACTIVE"

        # Full specification
        if current_date is None:
            current_date = date.today()

        today = self._parse_date(current_date)
        p_date = self._parse_date(active.promise_date)

        actual_paid = 0.0
        if isinstance(payments_ledger, (int, float)):
            actual_paid = float(payments_ledger)
        elif isinstance(payments_ledger, dict):
            actual_paid = float(payments_ledger.get(case_id, payments_ledger.get("amount_paid", 0.0)))
        elif isinstance(payments_ledger, list):
            for payment in payments_ledger:
                if payment.get("case_id") == case_id or payment.get("customer_id") == active.customer_id:
                    actual_paid += float(payment.get("amount", 0.0))

        now = time.time()

        if actual_paid >= active.promised_amount:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE ptp_records SET state = ?, updated_at = ? WHERE promise_id = ?",
                    (PTPState.KEPT.value, now, active.promise_id),
                )
            if case is not None:
                case.state = CaseState.RECOVERED

            return PTPEvaluationResult(
                case_id=case_id,
                state=PTPState.KEPT,
                promised_amount=active.promised_amount,
                paid_amount=actual_paid,
                is_habitual_breaker=active.is_habitual_breaker,
                recommended_action=ActionType.DO_NOTHING,
                policy_action="case_recovered",
                notes=f"Promise kept! Full amount of ₹{actual_paid:,.2f} recovered.",
            )

        if today > p_date:
            new_broken_count = active.broken_count + 1
            is_habitual = new_broken_count >= 2

            with self._conn() as conn:
                conn.execute(
                    """UPDATE ptp_records
                       SET state = ?, broken_count = ?, is_habitual_breaker = ?, updated_at = ?
                       WHERE promise_id = ?""",
                    (PTPState.BROKEN.value, new_broken_count, 1 if is_habitual else 0, now, active.promise_id),
                )

            if is_habitual:
                return PTPEvaluationResult(
                    case_id=case_id,
                    state=PTPState.BROKEN,
                    promised_amount=active.promised_amount,
                    paid_amount=actual_paid,
                    is_habitual_breaker=True,
                    recommended_action=ActionType.HUMAN_ESCALATION,
                    policy_action="escalate_human_debt_manager",
                    notes=f"Promise broken ({new_broken_count} broken promises). Flagged habitual promise-breaker. Escalated to human debt manager.",
                )
            else:
                return PTPEvaluationResult(
                    case_id=case_id,
                    state=PTPState.BROKEN,
                    promised_amount=active.promised_amount,
                    paid_amount=actual_paid,
                    is_habitual_breaker=False,
                    recommended_action=ActionType.REMINDER_SMS,
                    policy_action="requeue_firmer_reminder",
                    notes="1st broken promise. Re-queuing with firmer payment reminder.",
                )

        return PTPEvaluationResult(
            case_id=case_id,
            state=PTPState.ACTIVE,
            promised_amount=active.promised_amount,
            paid_amount=actual_paid,
            is_habitual_breaker=active.is_habitual_breaker,
            recommended_action=ActionType.DO_NOTHING,
            policy_action="outreach_frozen",
            notes=f"Promise active until {active.promise_date}. Outreach frozen.",
        )



# Alias
PTPTracker = PromiseToPayTracker
