"""
Transactional Outbox State Machine.
Guarantees that every outbound action (payment retry, mandate debit, WhatsApp, SMS, email)
is durably committed to SQLite before external network dispatch.
Handles crash-recovery, idempotency keys, and reconciliation of in-flight dispatches.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple, Union
import os
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager

from engine.models import CaseState, ActionType, RecoveryCase
from engine.execution.providers import (
    OmnichannelDispatcher,
    ProviderResponse,
    MockRazorpayGateway,
    MockWhatsAppProvider,
    MockSMSProvider,
    MockEmailProvider,
    PROVIDER_LOG,
)


class OutboxState(str, Enum):
    PENDING = "PENDING"          # Written to outbox, ready for worker dispatch
    SENT = "SENT"                # Dispatched to provider, awaiting acknowledgment
    CONFIRMED = "CONFIRMED"      # Provider confirmed execution (e.g. 200 OK + ref ID)
    FAILED = "FAILED"            # Provider returned hard error (4xx/5xx)
    RECONCILING = "RECONCILING"  # In-flight state during crashes or network dropouts
    ABORTED = "ABORTED"          # Aborted due to dispute freeze or PTP pause


def generate_idempotency_key(
    run_id: str,
    customer_id: str,
    case_id: str,
    attempt_number: int,
    channel: str,
) -> str:
    """
    Computes a deterministic, collision-resistant idempotency key:
    sha256(f"{run_id}:{customer_id}:{case_id}:{attempt_number}:{channel}").hexdigest()[:24]
    """
    raw = f"{run_id}:{customer_id}:{case_id}:{attempt_number}:{channel}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


# Shorthand alias
make_outbox_key = generate_idempotency_key


@dataclass
class OutboxRecord:
    idempotency_key: str
    run_id: str
    customer_id: str
    case_id: str
    attempt_number: int
    channel: str
    action_type: str
    payload: Dict[str, Any]
    state: OutboxState
    provider_ref: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "idempotency_key": self.idempotency_key,
            "run_id": self.run_id,
            "customer_id": self.customer_id,
            "case_id": self.case_id,
            "attempt_number": self.attempt_number,
            "channel": self.channel,
            "action_type": self.action_type,
            "payload": self.payload,
            "state": self.state.value if isinstance(self.state, OutboxState) else str(self.state),
            "provider_ref": self.provider_ref,
            "error_message": self.error_message,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "OutboxRecord":
        payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        metadata = json.loads(row["metadata_json"]) if ("metadata_json" in row.keys() and row["metadata_json"]) else {}
        action_val = row["action_type"] if "action_type" in row.keys() else row.get("action", "")
        return cls(
            idempotency_key=row["idempotency_key"],
            run_id=row["run_id"],
            customer_id=row["customer_id"],
            case_id=row["case_id"],
            attempt_number=int(row["attempt_number"]) if "attempt_number" in row.keys() else int(row.get("attempts", 1)),
            channel=row["channel"],
            action_type=action_val,
            payload=payload,
            state=OutboxState(row["state"]),
            provider_ref=row["provider_ref"] if "provider_ref" in row.keys() else None,
            error_message=row["error_message"] if "error_message" in row.keys() else row.get("last_error"),
            metadata=metadata,
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )


class TransactionalOutbox:
    """
    SQLite-backed transactional outbox state machine.
    Ensures zero double-charges, zero duplicate messages, and safe crash recovery.
    """
    def __init__(
        self,
        db_path: Any = "models/outbox.db",
        dispatcher: Optional[OmnichannelDispatcher] = None,
        ledger: Optional[Any] = None,
    ):
        if not isinstance(db_path, str):
            ledger = db_path
            db_path = getattr(ledger, "db_path", "models/outbox.db")
            if not isinstance(db_path, str):
                db_path = "models/outbox.db"

        self.ledger = ledger
        self.db_path = db_path
        self.dispatcher = dispatcher or OmnichannelDispatcher()
        self.gateway = self.dispatcher.razorpay
        self.whatsapp = self.dispatcher.whatsapp
        self.sms = self.dispatcher.sms
        self.email = self.dispatcher.email

        self._is_memory = db_path == ":memory:"
        self._mem_conn: Optional[sqlite3.Connection] = None
        if self._is_memory:
            self._mem_conn = sqlite3.connect(":memory:")
            self._mem_conn.row_factory = sqlite3.Row
        self._init_schema()

    @contextmanager
    def _conn(self):
        if self.ledger and hasattr(self.ledger, "_conn"):
            with self.ledger._conn() as conn:
                conn.row_factory = sqlite3.Row
                yield conn
        elif self._is_memory and self._mem_conn:
            yield self._mem_conn
            self._mem_conn.commit()
        else:
            conn = sqlite3.connect(self.db_path, timeout=15)
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
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS outbox_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    details_json TEXT,
                    ts REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_outbox_run ON outbox (run_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_outbox_case ON outbox (case_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_outbox_state ON outbox (state)")

    def _log_audit(self, conn: sqlite3.Connection, key: str, from_state: Optional[str], to_state: str, event_type: str, details: Optional[Dict[str, Any]] = None):
        conn.execute(
            """INSERT INTO outbox_audit (idempotency_key, from_state, to_state, event_type, details_json, ts)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (key, from_state, to_state, event_type, json.dumps(details or {}), time.time()),
        )

    def enqueue(
        self,
        run_id: str,
        customer_id: str,
        case_id: str,
        attempt_number: int,
        channel: str,
        action_type: str,
        payload: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> OutboxRecord:
        """
        Durable insert into outbox with idempotency check.
        If already enqueued or confirmed, returns existing record without duplicating.
        """
        key = generate_idempotency_key(run_id, customer_id, case_id, attempt_number, channel)
        now = time.time()

        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM outbox WHERE idempotency_key = ?", (key,))
            existing = cur.fetchone()
            if existing is not None:
                record = OutboxRecord.from_row(existing)
                self._log_audit(conn, key, record.state.value, record.state.value, "IDEMPOTENT_ENQUEUE_HIT", {"key": key})
                return record

            conn.execute(
                """INSERT INTO outbox (
                    idempotency_key, run_id, customer_id, case_id, attempt_number,
                    channel, action_type, payload_json, state, provider_ref,
                    error_message, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    key,
                    run_id,
                    customer_id,
                    case_id,
                    attempt_number,
                    channel,
                    action_type,
                    json.dumps(payload),
                    OutboxState.PENDING.value,
                    None,
                    None,
                    json.dumps(metadata or {}),
                    now,
                    now,
                ),
            )
            self._log_audit(conn, key, None, OutboxState.PENDING.value, "ENQUEUE_PENDING", {"case_id": case_id, "channel": channel})

        return self.get_record(key)  # type: ignore

    def get_record(self, idempotency_key: str) -> Optional[OutboxRecord]:
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM outbox WHERE idempotency_key = ?", (idempotency_key,))
            row = cur.fetchone()
            return OutboxRecord.from_row(row) if row else None

    def mark_state(
        self,
        idempotency_key: str,
        new_state: OutboxState,
        provider_ref: Optional[str] = None,
        error_message: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[OutboxRecord]:
        now = time.time()
        with self._conn() as conn:
            cur = conn.execute("SELECT state, provider_ref FROM outbox WHERE idempotency_key = ?", (idempotency_key,))
            row = cur.fetchone()
            if row is None:
                return None
            old_state = row["state"]
            ref_to_save = provider_ref or row["provider_ref"]

            conn.execute(
                """UPDATE outbox
                   SET state = ?, provider_ref = ?, error_message = ?, updated_at = ?
                   WHERE idempotency_key = ?""",
                (new_state.value, ref_to_save, error_message, now, idempotency_key),
            )
            self._log_audit(conn, idempotency_key, old_state, new_state.value, "STATE_TRANSITION", details)

        return self.get_record(idempotency_key)

    def dispatch_record(
        self,
        idempotency_key: str,
        dispatcher: Optional[OmnichannelDispatcher] = None,
    ) -> OutboxRecord:
        """
        Executes the outbox state machine transition:
        PENDING -> SENT -> CONFIRMED (or FAILED).
        Uses OmnichannelDispatcher with automatic fallback.
        """
        disp = dispatcher or self.dispatcher
        record = self.get_record(idempotency_key)
        if not record:
            raise ValueError(f"Outbox record {idempotency_key} not found")

        if record.state == OutboxState.CONFIRMED:
            return record

        if record.state == OutboxState.ABORTED:
            return record

        # Transition PENDING -> SENT
        self.mark_state(idempotency_key, OutboxState.SENT)

        resp: ProviderResponse = disp.dispatch(
            idempotency_key=idempotency_key,
            channel=record.channel,
            payload={**record.payload, "customer_id": record.customer_id, "case_id": record.case_id},
        )

        if resp.success:
            meta = {**record.metadata, "fallback_occurred": resp.fallback_occurred, "audit_notes": resp.audit_notes}
            with self._conn() as conn:
                conn.execute(
                    """UPDATE outbox
                       SET state = ?, provider_ref = ?, error_message = NULL, metadata_json = ?, updated_at = ?
                       WHERE idempotency_key = ?""",
                    (OutboxState.CONFIRMED.value, resp.reference_id, json.dumps(meta), time.time(), idempotency_key),
                )
                self._log_audit(conn, idempotency_key, OutboxState.SENT.value, OutboxState.CONFIRMED.value, "DISPATCH_CONFIRMED", resp.to_dict())
            return self.get_record(idempotency_key)  # type: ignore
        else:
            with self._conn() as conn:
                conn.execute(
                    """UPDATE outbox
                       SET state = ?, error_message = ?, updated_at = ?
                       WHERE idempotency_key = ?""",
                    (OutboxState.FAILED.value, resp.error, time.time(), idempotency_key),
                )
                self._log_audit(conn, idempotency_key, OutboxState.SENT.value, OutboxState.FAILED.value, "DISPATCH_FAILED", {"error": resp.error})
            return self.get_record(idempotency_key)  # type: ignore

    def dispatch_action(
        self,
        run_id: str,
        case: RecoveryCase,
        action: ActionType,
        attempt_number: int = 1,
        simulate_wa_failure: bool = False,
    ) -> Tuple[str, str, Dict[str, Any]]:
        """
        Direct dispatch helper for RecoveryCase items.
        Returns (idempotency_key, final_state, result_dict)
        """
        channel = "payment_gateway" if action in (ActionType.RETRY_IMMEDIATE, ActionType.RETRY_DELAYED, ActionType.REROUTE_GATEWAY, ActionType.MANDATE_RETRY_SEQUENCE) else (
            "whatsapp" if action in (ActionType.WHATSAPP_NUDGE, ActionType.CARD_UPDATE_LINK, ActionType.DISCOUNT_INCENTIVE) else (
                "sms" if action == ActionType.REMINDER_SMS else (
                    "voice" if action == ActionType.VOICE_CALL_HINGLISH else "human"
                )
            )
        )
        if simulate_wa_failure and hasattr(self.dispatcher.whatsapp, "simulate_network_failure"):
            self.dispatcher.whatsapp.simulate_network_failure = True

        payload = {
            "amount": case.amount,
            "customer_id": case.customer_id,
            "case_id": case.case_id,
            "phone": case.phone_masked,
            "message": f"Payment intervention: {action.value} for ₹{case.amount}",
        }
        record = self.enqueue(run_id, case.customer_id, case.case_id, attempt_number, channel, action.value, payload)
        if record.state == OutboxState.CONFIRMED:
            return record.idempotency_key, "SKIPPED", {"status": "SKIPPED", "existing_state": "CONFIRMED"}

        updated = self.dispatch_record(record.idempotency_key, dispatcher=self.dispatcher)
        return updated.idempotency_key, updated.state.value, updated.to_dict()

    def abort_case(self, case_id: str, reason: str = "Dispute freeze") -> int:
        """Immediately aborts any scheduled or pending outreach for a disputed case."""
        now = time.time()
        aborted_count = 0
        with self._conn() as conn:
            cur = conn.execute(
                """SELECT idempotency_key, state FROM outbox
                   WHERE case_id = ? AND state IN (?, ?, ?)""",
                (case_id, OutboxState.PENDING.value, OutboxState.SENT.value, OutboxState.RECONCILING.value),
            )
            rows = cur.fetchall()
            for r in rows:
                key = r["idempotency_key"]
                old = r["state"]
                conn.execute(
                    """UPDATE outbox
                       SET state = ?, error_message = ?, updated_at = ?
                       WHERE idempotency_key = ?""",
                    (OutboxState.ABORTED.value, f"Aborted: {reason}", now, key),
                )
                self._log_audit(conn, key, old, OutboxState.ABORTED.value, "ABORT_DISPUTED", {"reason": reason})
                aborted_count += 1
        return aborted_count

    def recover_inflight_actions(
        self,
        run_id: str,
        dispatcher: Optional[OmnichannelDispatcher] = None,
    ) -> List[OutboxRecord]:
        """
        Crash Recovery Method:
        Inspects all PENDING or unconfirmed actions (SENT, RECONCILING).
        If already executed by provider, marks CONFIRMED; otherwise safely resets or executes
        without duplication.
        """
        disp = dispatcher or self.dispatcher
        recovered: List[OutboxRecord] = []

        with self._conn() as conn:
            cur = conn.execute(
                """SELECT * FROM outbox
                   WHERE run_id = ? AND state IN (?, ?, ?)""",
                (run_id, OutboxState.SENT.value, OutboxState.RECONCILING.value, OutboxState.PENDING.value),
            )
            rows = cur.fetchall()

        for row in rows:
            rec = OutboxRecord.from_row(row)
            existing_resp = disp.query_status(rec.idempotency_key, channel=rec.channel)

            if existing_resp is not None and existing_resp.success:
                updated = self.mark_state(
                    rec.idempotency_key,
                    OutboxState.CONFIRMED,
                    provider_ref=existing_resp.reference_id,
                    details={"recovery_reason": "Identified executed at provider during crash reconciliation"},
                )
                if updated:
                    recovered.append(updated)
            elif rec.state in (OutboxState.SENT, OutboxState.RECONCILING):
                updated = self.mark_state(
                    rec.idempotency_key,
                    OutboxState.PENDING,
                    details={"recovery_reason": "Inflight state reset to PENDING after crash verify"},
                )
                if updated:
                    recovered.append(updated)
            else:
                recovered.append(rec)

        return recovered

    def list_records(
        self,
        run_id: Optional[str] = None,
        case_id: Optional[str] = None,
        state: Optional[OutboxState] = None,
    ) -> List[OutboxRecord]:
        query = "SELECT * FROM outbox WHERE 1=1"
        params: List[Any] = []
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        if case_id:
            query += " AND case_id = ?"
            params.append(case_id)
        if state:
            query += " AND state = ?"
            params.append(state.value if isinstance(state, OutboxState) else str(state))
        query += " ORDER BY created_at ASC"

        with self._conn() as conn:
            cur = conn.execute(query, tuple(params))
            return [OutboxRecord.from_row(r) for r in cur.fetchall()]

    def get_audit_trail(self, idempotency_key: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM outbox_audit WHERE idempotency_key = ? ORDER BY ts ASC",
                (idempotency_key,),
            )
            return [dict(r) for r in cur.fetchall()]
