"""
Mandate Diagnoser: Analyzes recurring subscription e-Mandate and UPI Autopay failures.
"""
from typing import List, Dict, Any
from engine.models import RecoveryCase, DiagnosisResult
from engine.config import CONFIDENCE_HUMAN_FLOOR


class MandateDiagnoser:
    """
    Evaluates recurring mandate lifecycle against RBI circular compliance,
    UPI Autopay limits, and instrument validity.
    """

    def diagnose(self, case: Any) -> DiagnosisResult:
        case_id = getattr(case, "case_id", "")
        if not case_id and isinstance(case, dict):
            case_id = case.get("case_id", case.get("mandate_id", "mandate_unknown"))

        if hasattr(case, "to_dict"):
            data = case.to_dict()
        elif isinstance(case, dict):
            data = case
        else:
            data = {"amount": getattr(case, "amount", 0.0), "metadata": getattr(case, "metadata", {})}

        meta = data.get("metadata", {})
        if not isinstance(meta, dict):
            meta = {}

        amount = float(data.get("amount", meta.get("amount", 0.0)))
        mandate_type = str(meta.get("mandate_type", data.get("mandate_type", "upi_autopay"))).lower()
        decline_code = str(data.get("decline_code", meta.get("decline_code", ""))).lower()
        days_to_card_expiry = meta.get("days_to_card_expiry", data.get("days_to_card_expiry", 180))
        pre_debit_sent = meta.get("pre_debit_notification_sent", meta.get("pre_debit_sent", None))
        consecutive_failures = int(meta.get("consecutive_failed_cycles", meta.get("prior_failures", 0)))

        evidence = []
        probabilities = {
            "pre_debit_notification_missed": 0.05,
            "mandate_limit_exceeded": 0.05,
            "card_expired_before_cycle": 0.05,
            "issuer_auth_decline": 0.05,
            "insufficient_funds": 0.05,
        }

        # Rule 1: Card Expired Before Mandate Renewal
        if days_to_card_expiry is not None and float(days_to_card_expiry) <= 0:
            cause = "card_expired_before_cycle"
            confidence = 1.0
            evidence = [
                f"Card backing subscription expired ({abs(int(days_to_card_expiry))} days ago)",
                "Underlying debit instrument invalid; automatic standing instruction cannot execute",
                "Customer must re-link an active card or switch to UPI Autopay",
            ]
            probabilities[cause] = 1.0

        # Rule 2: Pre-Debit Notification Missed (RBI Norm)
        elif (
            pre_debit_sent is False
            or decline_code in ["pre_debit_missed", "pre_debit_notification_missed", "rbi_pre_debit_error"]
            or meta.get("pre_debit_missed") is True
            or meta.get("pre_debit_notification_missed") is True
        ):
            cause = "pre_debit_notification_missed"
            confidence = 0.96
            evidence = [
                "RBI Mandate Compliance: 24-hour pre-debit alert was not delivered to customer prior to debit",
                "Issuer gateway automatically rejected debit to uphold consumer consent rules",
                "Resolution: Trigger pre-debit SMS/email notification and schedule execution in +24h window",
            ]
            probabilities[cause] = 0.96

        # Rule 3: UPI Autopay Limit Exceeded (> ₹15,000 without AFA)
        elif (
            (amount > 15000.0 and ("upi" in mandate_type or "autopay" in mandate_type) and not meta.get("has_afa"))
            or decline_code in ["mandate_limit_exceeded", "limit_exceeded", "afa_required"]
        ):
            cause = "mandate_limit_exceeded"
            confidence = 0.98
            evidence = [
                f"Mandate renewal amount ₹{amount:,.2f} exceeds RBI ₹15,000 threshold for frictionless UPI Autopay",
                "RBI regulations mandate Additional Factor Authentication (AFA / UPI PIN) for debits above ₹15,000",
                "Customer prompt via WhatsApp with direct UPI intent link required for 1-click PIN approval",
            ]
            probabilities[cause] = 0.98

        # Rule 4: Consecutive Cycle Failures / Issuer Auth Decline
        elif consecutive_failures >= 2 or decline_code in ["issuer_auth_decline", "standing_instruction_revoked"]:
            cause = "issuer_auth_decline"
            confidence = 0.85
            evidence = [
                f"Recurring mandate declined across {consecutive_failures} consecutive billing cycles",
                "Issuer standing instruction revoked, card blocked, or bank authentication rejected",
                "Customer outreach required to establish fresh mandate or alternate payment channel",
            ]
            probabilities[cause] = 0.85

        # Rule 5: Default Soft Decline (Insufficient Funds)
        else:
            cause = "issuer_auth_decline"
            confidence = 0.75
            evidence = [
                f"Mandate recurring debit failed with soft decline on {mandate_type}",
                "Account balance deficit or authorization failure indicated; timed sequence retry recommended",
            ]
            probabilities[cause] = 0.75

        requires_human = confidence < CONFIDENCE_HUMAN_FLOOR

        return DiagnosisResult(
            case_id=case_id,
            predicted_cause=cause,
            confidence=confidence,
            source="deterministic_rule",
            evidence=evidence,
            is_systemic=False,
            probabilities=probabilities,
            requires_human_approval=requires_human,
        )


_default_mandate = MandateDiagnoser()


def diagnose_mandate(case: Any) -> DiagnosisResult:
    return _default_mandate.diagnose(case)
