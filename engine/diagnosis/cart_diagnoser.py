"""
Cart Diagnoser: Analyzes abandoned checkout drop-off steps.
"""
from typing import List, Dict, Any
from engine.models import RecoveryCase, DiagnosisResult
from engine.config import CONFIDENCE_HUMAN_FLOOR


class CartDiagnoser:
    """
    Analyzes abandoned checkout sessions based on funnel drop-off steps,
    cart age, item count, and applied promotional attempts.
    """

    STEP_MAPPINGS = {
        "otp_screen": {
            "cause": "otp_delay",
            "confidence": 0.90,
            "evidence": [
                "Customer reached final OTP challenge screen before abandoning",
                "SMS OTP delivery latency or banking aggregator timeout suspected",
                "High purchase intent confirmed; friction is technical/auth",
            ],
        },
        "shipping_info": {
            "cause": "shipping_shock",
            "confidence": 0.88,
            "evidence": [
                "Customer dropped immediately upon shipping calculation display",
                "Delivery fee added unexpected surcharge over cart subtotal",
                "Price sensitivity barrier; responsive to shipping waiver or discount incentive",
            ],
        },
        "coupon_apply": {
            "cause": "coupon_invalid",
            "confidence": 0.92,
            "evidence": [
                "Customer attempted promo code application prior to drop-off",
                "Coupon validation error: code expired, invalid, or minimum cart threshold unmet",
                "Discount expectation unmet; responsive to alternative valid coupon incentive",
            ],
        },
        "payment_selection": {
            "cause": "payment_method_missing",
            "confidence": 0.85,
            "evidence": [
                "Customer dropped during payment instrument selection",
                "Preferred payment method (e.g. UPI, Bajaj Finserv EMI, or specific wallet) absent or failed",
                "Checkout UI friction; responsive to omnichannel payment link offering broader rails",
            ],
        },
        "cart": {
            "cause": "casual_browsing",
            "confidence": 0.75,
            "evidence": [
                "Customer added items to cart without initiating shipping or payment steps",
                "Low checkout intent or competitive comparison browsing",
            ],
        },
    }

    def diagnose(self, case: Any) -> DiagnosisResult:
        case_id = getattr(case, "case_id", "")
        if not case_id and isinstance(case, dict):
            case_id = case.get("case_id", case.get("cart_id", "cart_unknown"))

        if hasattr(case, "to_dict"):
            data = case.to_dict()
        elif isinstance(case, dict):
            data = case
        else:
            data = {"metadata": getattr(case, "metadata", {})}

        meta = data.get("metadata", {})
        if not isinstance(meta, dict):
            meta = {}

        drop_step = str(
            data.get("drop_step")
            or meta.get("drop_step")
            or meta.get("funnel_step")
            or "cart"
        ).strip().lower()

        cart_value = float(data.get("amount", meta.get("cart_value", 0.0)))
        cart_age_hours = float(meta.get("cart_age_hours", data.get("cart_age_hours", 2.0)))
        items_count = int(meta.get("cart_items_count", meta.get("items_count", 1)))

        config = self.STEP_MAPPINGS.get(drop_step, self.STEP_MAPPINGS["cart"])
        cause = config["cause"]
        confidence = config["confidence"]

        if cart_age_hours > 48.0 and cause != "casual_browsing":
            confidence = max(0.40, confidence - 0.20)

        evidence = list(config["evidence"])
        evidence.insert(0, f"Funnel drop step: '{drop_step}'")
        evidence.append(f"Cart value: ₹{cart_value:,.2f} ({items_count} item(s), age: {cart_age_hours:.1f} hrs)")

        requires_human = confidence < CONFIDENCE_HUMAN_FLOOR

        probabilities = {
            "otp_delay": 0.05,
            "shipping_shock": 0.05,
            "coupon_invalid": 0.05,
            "payment_method_missing": 0.05,
            "casual_browsing": 0.05,
        }
        probabilities[cause] = confidence
        rem = (1.0 - confidence) / max(len(probabilities) - 1, 1)
        for k in probabilities:
            if k != cause:
                probabilities[k] = round(rem, 3)

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


_default_cart = CartDiagnoser()


def diagnose_cart(case: Any) -> DiagnosisResult:
    return _default_cart.diagnose(case)
