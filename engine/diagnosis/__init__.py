"""
Unified Multi-Leak Diagnosis Suite:
Dispatches cases across Failed Payments, Abandoned Checkouts,
Subscription Mandates, and B2B Invoices, incorporating Systemic Degradation alerts.
"""
from typing import Optional, Any, Dict
from engine.models import RecoveryCase, LeakType, DiagnosisResult
from engine.diagnosis.payment_classifier import PaymentClassifier, diagnose_payment
from engine.diagnosis.cart_diagnoser import CartDiagnoser, diagnose_cart
from engine.diagnosis.mandate_diagnoser import MandateDiagnoser, diagnose_mandate
from engine.diagnosis.invoice_diagnoser import InvoiceDiagnoser, diagnose_invoice
from engine.systemic import SystemicDetector, SystemicDegradationDetector


class MultiLeakDiagnosisDispatcher:
    """Central multi-leak diagnosis dispatcher."""

    def __init__(
        self,
        systemic_detector: Optional[SystemicDegradationDetector] = None,
        payment_classifier: Optional[PaymentClassifier] = None,
        cart_diagnoser: Optional[CartDiagnoser] = None,
        mandate_diagnoser: Optional[MandateDiagnoser] = None,
        invoice_diagnoser: Optional[InvoiceDiagnoser] = None,
    ):
        self.systemic_detector = systemic_detector
        self.payment_classifier = payment_classifier or PaymentClassifier()
        self.cart_diagnoser = cart_diagnoser or CartDiagnoser()
        self.mandate_diagnoser = mandate_diagnoser or MandateDiagnoser()
        self.invoice_diagnoser = invoice_diagnoser or InvoiceDiagnoser()

    def diagnose_case(self, case: Any) -> DiagnosisResult:
        meta = getattr(case, "metadata", {}) if hasattr(case, "metadata") else case.get("metadata", case)
        if not isinstance(meta, dict):
            meta = {}
        bank = meta.get("bank", "")
        gateway = meta.get("gateway", "")
        upi_app = meta.get("upi_app", "")

        # Check systemic degradation first
        if self.systemic_detector and bank:
            is_degraded, incident = self.systemic_detector.is_degraded(bank, gateway, upi_app)
            if is_degraded and incident:
                case_id = getattr(case, "case_id", "") or (case.get("case_id") if isinstance(case, dict) else "case_unknown")
                return DiagnosisResult(
                    case_id=case_id,
                    predicted_cause="systemic_route_degraded",
                    confidence=0.99,
                    source="systemic_detector",
                    evidence=[
                        f"Active incident on route {incident.route_key}: success rate dropped {incident.drop_pts} points.",
                        "Classified as systemic infrastructure degradation; customer contact suppressed.",
                    ],
                    is_systemic=True,
                    probabilities={"systemic_route_degraded": 0.99, "other": 0.01},
                )

        # Dispatch based on leak_type
        leak_type = getattr(case, "leak_type", None)
        if leak_type is None and isinstance(case, dict):
            leak_type = case.get("leak_type")

        if isinstance(leak_type, str):
            try:
                leak_type = LeakType(leak_type)
            except ValueError:
                leak_type = LeakType.FAILED_PAYMENT

        if leak_type == LeakType.FAILED_PAYMENT:
            return self.payment_classifier.diagnose(case)
        elif leak_type == LeakType.ABANDONED_CHECKOUT:
            return self.cart_diagnoser.diagnose(case)
        elif leak_type == LeakType.SUBSCRIPTION_MANDATE:
            return self.mandate_diagnoser.diagnose(case)
        elif leak_type == LeakType.B2B_INVOICE:
            return self.invoice_diagnoser.diagnose(case)
        else:
            return self.payment_classifier.diagnose(case)

    def diagnose(self, case_or_dict: Any) -> Any:
        res = self.diagnose_case(case_or_dict)
        if isinstance(case_or_dict, dict) and not isinstance(case_or_dict, RecoveryCase):
            # For backward compatibility if dict returned
            return res
        return res

    def diagnose_dict(self, txn_dict: Dict[str, Any]) -> Dict[str, Any]:
        res = self.diagnose_case(txn_dict)
        return {
            "root_cause": res.predicted_cause,
            "confidence": res.confidence,
            "source": res.source,
            "evidence": res.evidence,
            "is_systemic": res.is_systemic,
            "probabilities": res.probabilities,
        }


# Aliases
DiagnosisEngine = MultiLeakDiagnosisDispatcher

__all__ = [
    "MultiLeakDiagnosisDispatcher",
    "DiagnosisEngine",
    "PaymentClassifier",
    "CartDiagnoser",
    "MandateDiagnoser",
    "InvoiceDiagnoser",
    "diagnose_payment",
    "diagnose_cart",
    "diagnose_mandate",
    "diagnose_invoice",
]
