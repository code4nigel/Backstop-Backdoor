"""
Backstop Canonical Domain Models, Enums, Dataclasses, and State Transitions.

This module defines the architectural contracts shared across all Backstop agents.
Every component (diagnosis, policy, execution, API, dashboard, and evaluation)
communicates using these typed contracts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Union


class LeakType(str, Enum):
    """Supported revenue leak categories in Backstop."""

    FAILED_PAYMENT = "failed_payment"            # Card/UPI/Netbanking one-off failure
    ABANDONED_CHECKOUT = "abandoned_checkout"    # Cart dropped at checkout/OTP
    SUBSCRIPTION_MANDATE = "subscription_mandate" # Recurring e-Mandate/UPI Autopay renewal failure
    B2B_INVOICE = "b2b_invoice"                  # Overdue receivables / Net-30 invoice

    # Lowercase aliases for convenience & backwards compatibility
    failed_payment = "failed_payment"
    abandoned_checkout = "abandoned_checkout"
    subscription_mandate = "subscription_mandate"
    b2b_invoice = "b2b_invoice"


class CaseState(str, Enum):
    """Canonical lifecycle states for a recovery case."""

    DETECTED = "DETECTED"              # Ingested, unanalyzed
    DIAGNOSED = "DIAGNOSED"            # Root cause and confidence calculated
    POLICY_BLOCKED = "POLICY_BLOCKED"  # Suppressed by stopping rules (DND, quiet hours, low EV, legal hold)
    READY = "READY"                    # Approved for execution
    EXECUTING = "EXECUTING"            # Action dispatched to outbox/channel
    PROMISED = "PROMISED"              # Customer committed to a future payment date (Promise-to-Pay)
    DISPUTED = "DISPUTED"              # Customer claimed already paid or disputed charge (frozen)
    RECOVERED = "RECOVERED"            # Funds successfully collected
    CLOSED = "CLOSED"                  # Terminal state (recovered, exhausted retries, or written off)


# Allowed state transitions for life cycle validation
VALID_CASE_TRANSITIONS: Dict[CaseState, Set[CaseState]] = {
    CaseState.DETECTED: {CaseState.DIAGNOSED, CaseState.POLICY_BLOCKED, CaseState.READY, CaseState.CLOSED},
    CaseState.DIAGNOSED: {CaseState.READY, CaseState.POLICY_BLOCKED, CaseState.CLOSED},
    CaseState.POLICY_BLOCKED: {CaseState.READY, CaseState.CLOSED, CaseState.DETECTED},
    CaseState.READY: {CaseState.EXECUTING, CaseState.POLICY_BLOCKED, CaseState.CLOSED},
    CaseState.EXECUTING: {
        CaseState.RECOVERED,
        CaseState.PROMISED,
        CaseState.DISPUTED,
        CaseState.POLICY_BLOCKED,
        CaseState.READY,
        CaseState.CLOSED,
    },
    CaseState.PROMISED: {
        CaseState.RECOVERED,
        CaseState.READY,
        CaseState.EXECUTING,
        CaseState.DISPUTED,
        CaseState.CLOSED,
    },
    CaseState.DISPUTED: {CaseState.RECOVERED, CaseState.READY, CaseState.CLOSED},
    CaseState.RECOVERED: {CaseState.CLOSED},
    CaseState.CLOSED: set(),  # Terminal state: no outgoing transitions
}

# Alias
VALID_STATE_TRANSITIONS = VALID_CASE_TRANSITIONS


def validate_transition(from_state: Any, to_state: Any) -> bool:
    """Validate whether transitioning from from_state to to_state is permissible.

    Args:
        from_state: Current case state (CaseState or str)
        to_state: Target case state (CaseState or str)

    Returns:
        True if transition is valid, False otherwise.
    """
    try:
        fs = CaseState(from_state) if isinstance(from_state, str) else from_state
        ts = CaseState(to_state) if isinstance(to_state, str) else to_state
    except ValueError:
        return False

    if fs == ts:
        return True

    return ts in VALID_CASE_TRANSITIONS.get(fs, set())


class ActionType(str, Enum):
    """Permitted recovery actions across all channels."""

    RETRY_IMMEDIATE = "retry_immediate"
    RETRY_DELAYED = "retry_delayed"
    REROUTE_GATEWAY = "reroute_gateway"
    CARD_UPDATE_LINK = "card_update_link"
    MANDATE_RETRY_SEQUENCE = "mandate_retry_sequence"
    REMINDER_SMS = "reminder_sms"
    WHATSAPP_NUDGE = "whatsapp_nudge"
    VOICE_CALL_HINGLISH = "voice_call_hinglish"
    DISCOUNT_INCENTIVE = "discount_incentive"
    HUMAN_ESCALATION = "human_escalation"
    DO_NOTHING = "do_nothing"

    # Lowercase aliases
    retry_immediate = "retry_immediate"
    retry_delayed = "retry_delayed"
    reroute_gateway = "reroute_gateway"
    card_update_link = "card_update_link"
    mandate_retry_sequence = "mandate_retry_sequence"
    reminder_sms = "reminder_sms"
    whatsapp_nudge = "whatsapp_nudge"
    voice_call_hinglish = "voice_call_hinglish"
    discount_incentive = "discount_incentive"
    human_escalation = "human_escalation"
    do_nothing = "do_nothing"


@dataclass
class RecoveryCase:
    """Canonical domain representation of an at-risk revenue item."""

    case_id: str
    customer_id: str
    customer_name: str
    phone_masked: str
    email_masked: str
    leak_type: LeakType
    amount: float
    days_at_risk: int
    created_at: float = field(default_factory=time.time)

    # Financial & Leak Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Ground Truth (Hidden from ML during evaluation, used for backtesting & scoring)
    true_root_cause: str = ""
    is_salary_week: bool = False

    # Life Cycle State
    state: CaseState = CaseState.DETECTED
    assigned_run_id: str = "default_run"
    is_control_group: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.leak_type, str):
            self.leak_type = LeakType(self.leak_type)
        if isinstance(self.state, str):
            self.state = CaseState(self.state)
        self.amount = float(self.amount)
        self.days_at_risk = int(self.days_at_risk)

    @property
    def txn_id(self) -> str:
        """Backwards compatibility alias for case_id."""
        return self.case_id

    @property
    def invoice_id(self) -> str:
        """Backwards compatibility alias for case_id."""
        return self.case_id

    def transition_to(self, new_state: Union[CaseState, str], force: bool = False) -> None:
        """Transition the case to a new state with lifecycle validation.

        Args:
            new_state: Target state
            force: If True, bypass validation check

        Raises:
            ValueError: If the state transition is illegal and force is False.
        """
        target = CaseState(new_state) if isinstance(new_state, str) else new_state
        if not force and not validate_transition(self.state, target):
            raise ValueError(
                f"Illegal state transition from {self.state.value} to {target.value} for case {self.case_id}"
            )
        self.state = target

    def is_terminal(self) -> bool:
        """Return True if case is in terminal CLOSED state."""
        return self.state == CaseState.CLOSED

    def to_dict(self) -> Dict[str, Any]:
        """Serialize case to a plain JSON-compatible dictionary."""
        return {
            "case_id": self.case_id,
            "customer_id": self.customer_id,
            "customer_name": self.customer_name,
            "phone_masked": self.phone_masked,
            "email_masked": self.email_masked,
            "leak_type": self.leak_type.value if isinstance(self.leak_type, LeakType) else self.leak_type,
            "amount": float(self.amount),
            "days_at_risk": int(self.days_at_risk),
            "created_at": float(self.created_at),
            "metadata": dict(self.metadata),
            "true_root_cause": self.true_root_cause,
            "is_salary_week": bool(self.is_salary_week),
            "state": self.state.value if isinstance(self.state, CaseState) else self.state,
            "assigned_run_id": self.assigned_run_id,
            "is_control_group": bool(self.is_control_group),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RecoveryCase:
        """Instantiate RecoveryCase from a dictionary."""
        d = dict(data)
        case_id = str(d.get("case_id") or d.get("txn_id") or d.get("invoice_id") or "")
        leak_val = d.get("leak_type", LeakType.FAILED_PAYMENT)
        leak_type = LeakType(leak_val) if isinstance(leak_val, str) else leak_val
        state_val = d.get("state", CaseState.DETECTED)
        state = CaseState(state_val) if isinstance(state_val, str) else state_val

        return cls(
            case_id=case_id,
            customer_id=str(d.get("customer_id", "")),
            customer_name=str(d.get("customer_name", "")),
            phone_masked=str(d.get("phone_masked", "")),
            email_masked=str(d.get("email_masked", "")),
            leak_type=leak_type,
            amount=float(d.get("amount", 0.0)),
            days_at_risk=int(d.get("days_at_risk", 0)),
            created_at=float(d.get("created_at", time.time())),
            metadata=dict(d.get("metadata", {})),
            true_root_cause=str(d.get("true_root_cause", "")),
            is_salary_week=bool(d.get("is_salary_week", False)),
            state=state,
            assigned_run_id=str(d.get("assigned_run_id", "default_run")),
            is_control_group=bool(d.get("is_control_group", False)),
        )


@dataclass
class DiagnosisResult:
    """Output of the root-cause diagnostic classifier."""

    case_id: str
    predicted_cause: str
    confidence: float
    source: str  # "ml_model", "deterministic_rule", "systemic_detector"
    evidence: List[str] = field(default_factory=list)
    is_systemic: bool = False
    probabilities: Dict[str, float] = field(default_factory=dict)
    requires_human_approval: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert diagnosis result to dictionary."""
        return {
            "case_id": self.case_id,
            "predicted_cause": self.predicted_cause,
            "confidence": round(float(self.confidence), 4),
            "source": self.source,
            "evidence": list(self.evidence),
            "is_systemic": bool(self.is_systemic),
            "probabilities": {k: round(float(v), 4) for k, v in self.probabilities.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DiagnosisResult:
        """Create DiagnosisResult from dictionary."""
        return cls(
            case_id=str(data.get("case_id", "")),
            predicted_cause=str(data.get("predicted_cause", "")),
            confidence=float(data.get("confidence", 0.0)),
            source=str(data.get("source", "deterministic_rule")),
            evidence=list(data.get("evidence", [])),
            is_systemic=bool(data.get("is_systemic", False)),
            probabilities={str(k): float(v) for k, v in data.get("probabilities", {}).items()},
        )


@dataclass
class EvaluatedIntervention:
    """Financial & probability breakdown of a potential recovery intervention."""

    action: ActionType
    p_recovery: float
    expected_value: float
    channel_cost: float
    expected_discount: float
    risk_penalty: float
    human_cost: float
    net_recovery: float
    rationale: str

    def __post_init__(self) -> None:
        if isinstance(self.action, str):
            self.action = ActionType(self.action)

    @property
    def name(self) -> str:
        """Return action name string for compatibility."""
        return self.action.value if isinstance(self.action, ActionType) else str(self.action)

    def to_dict(self) -> Dict[str, Any]:
        """Convert intervention to dictionary."""
        return {
            "action": self.action.value if isinstance(self.action, ActionType) else self.action,
            "p_recovery": round(float(self.p_recovery), 4),
            "expected_value": round(float(self.expected_value), 2),
            "channel_cost": round(float(self.channel_cost), 2),
            "expected_discount": round(float(self.expected_discount), 2),
            "risk_penalty": round(float(self.risk_penalty), 2),
            "human_cost": round(float(self.human_cost), 2),
            "net_recovery": round(float(self.net_recovery), 2),
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EvaluatedIntervention:
        """Create EvaluatedIntervention from dictionary."""
        return cls(
            action=ActionType(data.get("action", ActionType.DO_NOTHING)),
            p_recovery=float(data.get("p_recovery", 0.0)),
            expected_value=float(data.get("expected_value", 0.0)),
            channel_cost=float(data.get("channel_cost", 0.0)),
            expected_discount=float(data.get("expected_discount", 0.0)),
            risk_penalty=float(data.get("risk_penalty", 0.0)),
            human_cost=float(data.get("human_cost", 0.0)),
            net_recovery=float(data.get("net_recovery", 0.0)),
            rationale=str(data.get("rationale", "")),
        )


@dataclass
class PolicyDecision:
    """Decision output produced by the compliance and policy engine."""

    case_id: str
    allowed: bool
    chosen_action: ActionType
    blocked_reason: Optional[str] = None
    applied_rules: List[str] = field(default_factory=list)
    requires_human_approval: bool = False
    human_review_reason: Optional[str] = None
    ranked_interventions: List[EvaluatedIntervention] = field(default_factory=list)
    rationale: Optional[str] = None
    root_cause: Optional[str] = None
    confidence: Optional[float] = None
    expected_value: Optional[float] = None
    attempt_number: int = 1
    stopped: bool = False
    stop_reason: Optional[str] = None
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.chosen_action, str):
            self.chosen_action = ActionType(self.chosen_action)

    def to_dict(self) -> Dict[str, Any]:
        """Convert PolicyDecision to dictionary."""
        return {
            "case_id": self.case_id,
            "allowed": self.allowed,
            "chosen_action": self.chosen_action.value if isinstance(self.chosen_action, ActionType) else self.chosen_action,
            "blocked_reason": self.blocked_reason,
            "applied_rules": list(self.applied_rules),
            "requires_human_approval": self.requires_human_approval,
            "human_review_reason": self.human_review_reason,
            "ranked_interventions": [
                i.to_dict() if hasattr(i, "to_dict") else i
                for i in self.ranked_interventions
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PolicyDecision:
        """Create PolicyDecision from dictionary."""
        ranked = [
            EvaluatedIntervention.from_dict(i) if isinstance(i, dict) else i
            for i in data.get("ranked_interventions", [])
        ]
        return cls(
            case_id=str(data.get("case_id", "")),
            allowed=bool(data.get("allowed", True)),
            chosen_action=ActionType(data.get("chosen_action", ActionType.DO_NOTHING)),
            blocked_reason=data.get("blocked_reason"),
            applied_rules=list(data.get("applied_rules", [])),
            requires_human_approval=bool(data.get("requires_human_approval", False)),
            human_review_reason=data.get("human_review_reason"),
            ranked_interventions=ranked,
        )


@dataclass
class AuditEvent:
    """Immutable audit trail record for state transitions and decisions."""

    event_id: str
    case_id: str
    run_id: str
    from_state: Optional[str]
    to_state: str
    rule_or_action: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Convert AuditEvent to dictionary."""
        return {
            "event_id": self.event_id,
            "case_id": self.case_id,
            "run_id": self.run_id,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "rule_or_action": self.rule_or_action,
            "details": dict(self.details),
            "timestamp": float(self.timestamp),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AuditEvent:
        """Create AuditEvent from dictionary."""
        return cls(
            event_id=str(data.get("event_id", "")),
            case_id=str(data.get("case_id", "")),
            run_id=str(data.get("run_id", "default_run")),
            from_state=data.get("from_state"),
            to_state=str(data.get("to_state", "")),
            rule_or_action=str(data.get("rule_or_action", "")),
            details=dict(data.get("details", {})),
            timestamp=float(data.get("timestamp", time.time())),
        )
