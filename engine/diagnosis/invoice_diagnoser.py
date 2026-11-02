"""
Invoice Diagnoser: Analyzes B2B accounts receivable and overdue invoices.
"""
from typing import List, Dict, Any
from engine.models import RecoveryCase, DiagnosisResult
from engine.config import CONFIDENCE_HUMAN_FLOOR


class InvoiceDiagnoser:
    """
    Analyzes B2B enterprise and SMB invoice aging, dispute flags,
    purchase order validation, and corporate disbursement timing.
    """

    def diagnose(self, case: Any) -> DiagnosisResult:
        case_id = getattr(case, "case_id", "")
        if not case_id and isinstance(case, dict):
            case_id = case.get("case_id", case.get("invoice_id", "inv_unknown"))

        if hasattr(case, "to_dict"):
            data = case.to_dict()
        elif isinstance(case, dict):
            data = case
        else:
            data = {
                "amount": getattr(case, "amount", 0.0),
                "metadata": getattr(case, "metadata", {}),
                "is_salary_week": getattr(case, "is_salary_week", False),
            }

        meta = data.get("metadata", {})
        if not isinstance(meta, dict):
            meta = {}

        amount = float(data.get("amount", meta.get("amount", 0.0)))
        days_overdue = int(
            data.get("days_at_risk")
            or meta.get("days_overdue")
            or data.get("days_overdue")
            or 0
        )
        client_tier = str(meta.get("client_tier", data.get("client_tier", "SMB"))).strip()

        dispute_flag = bool(
            data.get("dispute_flag", False)
            or meta.get("dispute_flag", False)
            or meta.get("has_dispute", False)
            or meta.get("is_disputed", False)
        )
        po_mismatch = bool(
            meta.get("po_mismatch", False)
            or meta.get("po_number_mismatch", False)
            or meta.get("po_status") == "mismatch"
            or (meta.get("po_number") and meta.get("expected_po") and meta.get("po_number") != meta.get("expected_po"))
        )
        wrong_contact = bool(meta.get("wrong_contact", False) or meta.get("email_bounced", False))
        is_salary_week = bool(data.get("is_salary_week", meta.get("is_salary_week", False)))
        quarter_end = bool(meta.get("is_quarter_end", False) or meta.get("cash_flow_timing", False))

        evidence = []
        probabilities = {
            "po_number_mismatch": 0.05,
            "invoice_dispute": 0.05,
            "cash_flow_timing": 0.05,
            "wrong_accounts_payable_contact": 0.05,
            "forgot_payment": 0.05,
        }

        # Gate 1: Formal Invoice Dispute
        if dispute_flag:
            cause = "invoice_dispute"
            confidence = 0.98
            evidence = [
                "Client Accounts Payable formally raised dispute regarding line-item or billing terms",
                "CRITICAL COMPLIANCE: All automated recovery outreach must be frozen immediately",
            ]
            probabilities[cause] = 0.98

        # Gate 2: PO Number Mismatch on Aged / Enterprise Invoice
        elif po_mismatch or (days_overdue > 15 and meta.get("po_status") == "mismatch"):
            cause = "po_number_mismatch"
            confidence = 0.95
            po_num = meta.get("po_number", "MISSING")
            evidence = [
                f"{client_tier} account invoice overdue by {days_overdue} days",
                f"Invoice PO number '{po_num}' does not match ERP procurement order; pending corporate GRN approval",
            ]
            probabilities[cause] = 0.95

        # Gate 3: Wrong Accounts Payable Contact
        elif wrong_contact or (days_overdue > 30 and meta.get("reminders_unopened", 0) >= 3):
            cause = "wrong_accounts_payable_contact"
            confidence = 0.90
            evidence = [
                "Primary invoice delivery email bounced or recipient left organization",
                "Human escalation required to verify current finance / AP decision-maker",
            ]
            probabilities[cause] = 0.90

        # Gate 4: Cash Flow & Quarter-End Timing
        elif is_salary_week or quarter_end or meta.get("treasury_hold"):
            cause = "cash_flow_timing"
            confidence = 0.85
            evidence = [
                f"Overdue delay aligns with corporate monthly payroll / quarter-end disbursement calendar",
                f"Invoice amount ₹{amount:,.2f} pending corporate treasury release batch",
            ]
            probabilities[cause] = 0.85

        # Gate 5: Routine Forgot Payment
        else:
            cause = "forgot_payment"
            confidence = 0.70
            evidence = [
                f"Invoice overdue by {days_overdue} days with no active dispute or mismatch recorded",
                "Routine payment delay; invoice overdue without reported dispute",
            ]
            probabilities[cause] = 0.70

        requires_human = confidence < CONFIDENCE_HUMAN_FLOOR or dispute_flag

        return DiagnosisResult(
            case_id=case_id,
            predicted_cause=cause,
            confidence=confidence,
            source="deterministic_rule" if confidence >= 0.90 else "ml_model",
            evidence=evidence,
            is_systemic=False,
            probabilities=probabilities,
            requires_human_approval=requires_human,
        )


_default_invoice = InvoiceDiagnoser()


def diagnose_invoice(case: Any) -> DiagnosisResult:
    return _default_invoice.diagnose(case)
