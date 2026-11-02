"""
API Validation Schemas and Payload Models for Backstop Operations API:
Includes:
1. Pydantic input/output validation contracts for the operations command center (compatible with Pydantic v1 & v2).
2. Input sanitization and injection defense helpers (protects against SQL injection & XSS).
"""
import re
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from pydantic import BaseModel as _PydanticBaseModel, Field

try:
    from pydantic import ConfigDict
    HAS_PYDANTIC_V2 = True
except ImportError:
    ConfigDict = None
    HAS_PYDANTIC_V2 = False


class BaseModel(_PydanticBaseModel):
    """Compatibility wrapper supporting both Pydantic v1 and Pydantic v2."""
    if HAS_PYDANTIC_V2:
        model_config = ConfigDict(extra="ignore")
    else:
        class Config:
            extra = "ignore"

    @classmethod
    def model_validate(cls, obj: Any):
        if hasattr(super(), "model_validate"):
            return super().model_validate(obj)
        return cls.parse_obj(obj)

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        if hasattr(super(), "model_dump"):
            return super().model_dump(**kwargs)
        return self.dict(**kwargs)


# =====================================================================
# INJECTION DEFENSE & SANITIZATION HELPERS
# =====================================================================

RE_SQL_INJECTION = re.compile(
    r"(\b(UNION(\s+ALL)?|SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|EXEC|TRUNCATE)\b|--|;|\/\*|\*\/|'|\"|\bOR\b|\bAND\b\s+['\d]=['\d])",
    re.IGNORECASE
)
RE_XSS_INJECTION = re.compile(
    r"(<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>|<[a-zA-Z0-9_\-]+[^>]*on\w+\s*=|javascript:|data:text\/html)",
    re.IGNORECASE
)


def sanitize_string(value: str) -> str:
    """Sanitizes user input string against HTML tags and script injection."""
    if not isinstance(value, str):
        return str(value)
    clean = re.sub(r'<[^>]*>', '', value)
    clean = clean.replace('<', '&lt;').replace('>', '&gt;')
    return clean.strip()


def check_for_injection(value: Any) -> Tuple[bool, str]:
    """
    Returns (is_suspicious, message).
    Scans for classic SQL injection patterns and XSS payloads.
    """
    if isinstance(value, str):
        if RE_XSS_INJECTION.search(value):
            return True, "XSS injection pattern detected"
        if RE_SQL_INJECTION.search(value):
            if any(sqli in value.upper() for sqli in ("' OR '1'='1", "' OR 1=1", "DROP TABLE", "--", "UNION SELECT")):
                return True, "SQL injection pattern detected"
    elif isinstance(value, dict):
        for k, v in value.items():
            suspicious, msg = check_for_injection(v)
            if suspicious:
                return True, f"{msg} in key '{k}'"
    elif isinstance(value, list):
        for item in value:
            suspicious, msg = check_for_injection(item)
            if suspicious:
                return True, msg
    return False, "clean"


# =====================================================================
# DOMAIN ENUMS
# =====================================================================

class LeakType(str, Enum):
    FAILED_PAYMENT = "failed_payment"
    ABANDONED_CHECKOUT = "abandoned_checkout"
    SUBSCRIPTION_MANDATE = "subscription_mandate"
    B2B_INVOICE = "b2b_invoice"


class CaseState(str, Enum):
    DETECTED = "DETECTED"
    DIAGNOSED = "DIAGNOSED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    READY = "READY"
    EXECUTING = "EXECUTING"
    PROMISED = "PROMISED"
    DISPUTED = "DISPUTED"
    RECOVERED = "RECOVERED"
    CLOSED = "CLOSED"


class ActionType(str, Enum):
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


# =====================================================================
# OVERVIEW & METRICS SCHEMAS
# =====================================================================

class LeakMetric(BaseModel):
    leak_type: LeakType
    title: str
    total_at_risk: float
    at_risk_count: int
    net_recovered: float
    recovered_count: int
    recovery_rate_pct: float
    top_root_cause: str
    avg_ticket_size: float


class ConfidenceInterval(BaseModel):
    lower_pct: float
    upper_pct: float
    confidence_level: float = 0.95


class OverviewResponse(BaseModel):
    total_revenue_at_risk: float
    net_money_recovered: float
    recovery_rate_pct: float
    control_recovery_rate_pct: float
    incremental_lift_pct: float
    confidence_interval_95: ConfidenceInterval
    total_outreach_cost: float
    total_discount_cost: float
    net_roi_multiplier: float
    headline_stat: str
    leak_breakdown: List[LeakMetric]
    active_cases_count: int
    suppressed_count: int
    human_queue_count: int


# =====================================================================
# SYSTEMIC ALERTS SCHEMAS
# =====================================================================

class SystemicAlert(BaseModel):
    incident_id: str
    title: str
    severity: str
    impacted_entity: str
    baseline_success_rate_pct: float
    current_success_rate_pct: float
    drop_pct: float
    active_since: str
    status: str
    transactions_affected: int
    transactions_rerouted_or_held: int
    spam_prevented_count: int
    resolution_action: str
    description: str


class SystemicAlertsResponse(BaseModel):
    alerts: List[SystemicAlert]
    active_incident_count: int
    total_spam_prevented: int


# =====================================================================
# CASE DETAIL & DRILL-DOWN SCHEMAS
# =====================================================================

class InterventionRanking(BaseModel):
    action: ActionType
    p_recovery: float
    expected_value: float
    channel_cost: float
    expected_discount: float
    risk_penalty: float
    net_ev: float
    is_chosen: bool
    rationale: str


class DiagnosisDetail(BaseModel):
    predicted_cause: str
    confidence: float
    evidence: List[str]
    is_systemic: bool = False
    probabilities: Dict[str, float] = Field(default_factory=dict)


class PolicyCheckDetail(BaseModel):
    passed_all: bool
    dnd_status: str
    quiet_hours: str
    margin_guardrail: str
    frequency_cap: str
    dispute_hold: str
    requires_human: bool
    human_review_reason: Optional[str] = None
    applied_rules: List[str] = Field(default_factory=list)


class ExecutionDetail(BaseModel):
    action: ActionType
    channel: str
    outbox_status: str
    delivery_receipt_id: Optional[str] = None
    idempotency_key: str
    timestamp: str
    dispatch_note: str


class AuditLogEntry(BaseModel):
    timestamp: str
    from_state: str
    to_state: str
    actor: str
    action_or_rule: str
    notes: str


class CaseDetail(BaseModel):
    case_id: str
    customer_id: str
    customer_name: str
    phone_masked: str
    email_masked: str
    leak_type: LeakType
    amount: float
    days_at_risk: int
    created_at_str: str
    state: CaseState
    is_control_group: bool
    true_root_cause: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    # 5-Step Explainability Chain
    diagnosis: DiagnosisDetail
    ranked_interventions: List[InterventionRanking]
    policy_check: PolicyCheckDetail
    execution: ExecutionDetail
    audit_trail: List[AuditLogEntry]


class CaseListItem(BaseModel):
    case_id: str
    customer_name: str
    phone_masked: str
    email_masked: Optional[str] = None
    leak_type: LeakType
    amount: float
    state: CaseState
    predicted_cause: str
    confidence: float
    chosen_action: str
    net_ev: float
    is_control_group: bool
    is_systemic: bool
    requires_human_approval: bool
    days_at_risk: int
    created_at_str: str


class CasesResponse(BaseModel):
    cases: List[CaseListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


# =====================================================================
# POLICY LAB SCHEMAS
# =====================================================================

class PolicySimulationRequest(BaseModel):
    retry_limit: int = Field(default=3, ge=1, le=5)
    discount_cap_pct: float = Field(default=10.0, ge=0.0, le=20.0)
    quiet_hours_enabled: bool = Field(default=True)
    min_case_value: float = Field(default=200.0, ge=0.0, le=1000.0)
    human_review_threshold: float = Field(default=50000.0, ge=10000.0, le=100000.0)


class PolicySimulationResponse(BaseModel):
    overrides_applied: Dict[str, Any]
    projected_gross_recovered: float
    projected_net_recovered: float
    projected_recovery_rate_pct: float
    projected_outreach_cost: float
    projected_discount_cost: float
    projected_net_roi_multiplier: float
    blocked_cases_count: int
    blocked_by_min_value_count: int
    blocked_by_quiet_hours_count: int
    cases_sent_to_human_count: int
    compliance_risk_score: float
    customer_friction_score: float
    delta_vs_baseline_pct: float
    tradeoff_radar: Dict[str, float]
    simulation_notes: List[str]


# =====================================================================
# HUMAN QUEUE SCHEMAS
# =====================================================================

class HumanQueueItem(BaseModel):
    case_id: str
    customer_name: str
    leak_type: LeakType
    amount: float
    urgency: str
    escalation_reason: str
    recommended_action: ActionType
    suggested_discount_pct: Optional[float] = None
    diagnosis_summary: str
    created_at_str: str
    state: CaseState


class HumanActionRequest(BaseModel):
    action: str = Field(description="approve | reject | modify_discount | resolve_dispute")
    notes: Optional[str] = "Operator action via command center"
    new_discount_pct: Optional[float] = Field(default=None, ge=0.0, le=25.0)
    override_action: Optional[ActionType] = None


class HumanActionResponse(BaseModel):
    success: bool
    case_id: str
    action_taken: str
    new_state: CaseState
    message: str
    audit_event: AuditLogEntry


# =====================================================================
# SUPPRESSED CASES ("CASES WE CHOSE NOT TO CHASE")
# =====================================================================

class SuppressedCase(BaseModel):
    case_id: str
    customer_name: str
    phone_masked: str
    leak_type: LeakType
    amount: float
    block_reason: str
    stopping_rule_fired: str
    wasted_spend_saved: float
    customer_goodwill_preserved: str
    timestamp: str


class SuppressedCasesResponse(BaseModel):
    suppressed_cases: List[SuppressedCase]
    total_suppressed: int
    total_spend_saved: float
    customer_relationships_preserved: int
    breakdown_by_rule: Dict[str, int]
    trust_statement: str
