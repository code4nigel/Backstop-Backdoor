"""
Backstop Core Engine Package.

Provides the foundational domain contracts, idempotent run-isolated ledger,
central configuration, and high-speed PII data sanitization.
"""

from engine.config import Config, config, load_config
from engine.ledger import RecoveryLedger, make_idempotency_key
from engine.models import (
    ActionType,
    AuditEvent,
    CaseState,
    DiagnosisResult,
    EvaluatedIntervention,
    LeakType,
    PolicyDecision,
    RecoveryCase,
    validate_transition,
)
from engine.pii import (
    audit_scan_pii,
    mask_card,
    mask_dict,
    mask_email,
    mask_gstin,
    mask_name,
    mask_phone,
    mask_text,
)

__all__ = [
    # Domain Models & Enums
    "LeakType",
    "CaseState",
    "ActionType",
    "RecoveryCase",
    "DiagnosisResult",
    "EvaluatedIntervention",
    "PolicyDecision",
    "AuditEvent",
    "validate_transition",
    # Configuration
    "Config",
    "config",
    "load_config",
    # Ledger & State Machine
    "RecoveryLedger",
    "make_idempotency_key",
    # PII Sanitization
    "mask_phone",
    "mask_email",
    "mask_name",
    "mask_card",
    "mask_gstin",
    "mask_dict",
    "mask_text",
    "audit_scan_pii",
]
