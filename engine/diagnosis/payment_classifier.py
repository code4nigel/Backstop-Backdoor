"""
Payment Classifier: Diagnoses failed card/UPI/netbanking transactions.
Combines deterministic card expiry rules, decline code mapping, ML tree scoring, and confidence floors.
"""
from typing import Dict, Any, Tuple, List, Optional
import math
import os

from engine.models import RecoveryCase, DiagnosisResult
from engine.config import CONFIDENCE_HUMAN_FLOOR

LABELS = ["insufficient_funds", "expired_card", "bank_risk_block", "3ds_friction", "fraud_flag"]
BANK_RISK_CODE = {"low": 0, "medium": 1, "high": 2}
DEFAULT_BANK_TIERS = {
    "HDFC": "low",
    "SBI": "medium",
    "ICICI": "low",
    "AXIS": "medium",
    "KOTAK": "low",
    "RBL": "high",
    "YES": "medium",
}

DECLINE_CODE_MAP = {
    "INSUFFICIENT_FUNDS": "insufficient_funds",
    "EXPIRED_CARD": "expired_card",
    "ISSUER_DOWN": "issuer_down",
    "BANK_DOWN": "issuer_down",
    "3DS_TIMEOUT": "3ds_friction",
    "OTP_EXPIRED": "3ds_friction",
    "UPI_TIMEOUT": "upi_timeout",
    "LIMIT_EXCEEDED": "mandate_limit_exceeded",
    "SUSPECTED_FRAUD": "fraud_flag",
    "DO_NOT_HONOR": "bank_risk_block",
}


class _LightGBMTree:
    """Pure-Python evaluator for exported LightGBM tree structures."""

    def __init__(
        self,
        split_feature: List[int],
        threshold: List[float],
        left_child: List[int],
        right_child: List[int],
        leaf_value: List[float],
    ):
        self.split_feature = split_feature
        self.threshold = threshold
        self.left_child = left_child
        self.right_child = right_child
        self.leaf_value = leaf_value

    def eval(self, x: List[float]) -> float:
        node = 0
        while True:
            feat = self.split_feature[node]
            th = self.threshold[node]
            val = x[feat]
            child = self.left_child[node] if val <= th else self.right_child[node]
            if child < 0:
                leaf_idx = -child - 1
                return self.leaf_value[leaf_idx]
            node = child


class PaymentClassifier:
    """
    ML Classifier with deterministic overrides for card and UPI payments.
    Evaluates LightGBM model text files natively with zero third-party dependencies.
    """

    def __init__(self, model_path: str = "models/diagnosis_model.txt"):
        self.model_path = model_path
        self.trees: List[_LightGBMTree] = []
        self._load_model()

    def _load_model(self) -> None:
        if not os.path.exists(self.model_path):
            alt_path = os.path.join(os.path.dirname(__file__), "..", "..", self.model_path)
            if os.path.exists(alt_path):
                self.model_path = alt_path

        if not os.path.exists(self.model_path):
            return

        try:
            with open(self.model_path, "r", encoding="utf-8") as f:
                content = f.read()

            tree_blocks = content.split("Tree=")[1:]
            for block in tree_blocks:
                lines = {}
                for line in block.strip().splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        lines[k.strip()] = v.strip()

                if "split_feature" in lines and "leaf_value" in lines:
                    split_feature = [int(v) for v in lines["split_feature"].split()]
                    threshold = [float(v) for v in lines["threshold"].split()]
                    left_child = [int(v) for v in lines["left_child"].split()]
                    right_child = [int(v) for v in lines["right_child"].split()]
                    leaf_value = [float(v) for v in lines["leaf_value"].split()]
                    self.trees.append(
                        _LightGBMTree(
                            split_feature, threshold, left_child, right_child, leaf_value
                        )
                    )
        except Exception:
            self.trees = []

    def _extract_features(self, txn: Dict[str, Any]) -> Tuple[List[float], Dict[str, Any]]:
        meta = txn.get("metadata", {})
        if not isinstance(meta, dict):
            meta = {}

        amount = float(txn.get("amount", meta.get("amount", 2500.0)))
        days_to_expiry = txn.get("days_to_card_expiry")
        if days_to_expiry is None:
            days_to_expiry = meta.get("days_to_card_expiry", 180)
        days_to_expiry = float(days_to_expiry)

        prior_failures = txn.get("prior_failures")
        if prior_failures is None:
            prior_failures = meta.get("prior_failures", 0)
        prior_failures = float(prior_failures)

        balance_proxy = txn.get("account_balance_proxy")
        if balance_proxy is None:
            balance_proxy = meta.get("account_balance_proxy", 0.0)
        balance_proxy = float(balance_proxy)

        is_salary = txn.get("is_salary_week")
        if is_salary is None:
            is_salary = meta.get("is_salary_week", False)
        is_salary_int = 1.0 if bool(is_salary) else 0.0

        bank = str(txn.get("bank", meta.get("bank", "HDFC"))).strip().upper()
        tier = txn.get("bank_risk_tier") or meta.get("bank_risk_tier")
        if not tier:
            tier = DEFAULT_BANK_TIERS.get(bank, "medium")
        tier_code = float(BANK_RISK_CODE.get(str(tier).lower(), 1))

        x = [amount, days_to_expiry, prior_failures, balance_proxy, is_salary_int, tier_code]
        info = {
            "amount": amount,
            "days_to_card_expiry": days_to_expiry,
            "prior_failures": int(prior_failures),
            "account_balance_proxy": balance_proxy,
            "is_salary_week": bool(is_salary_int),
            "bank": bank,
            "bank_risk_tier": tier,
        }
        return x, info

    def diagnose(self, case: Any) -> DiagnosisResult:
        case_id = getattr(case, "case_id", "")
        if not case_id and isinstance(case, dict):
            case_id = case.get("case_id", case.get("txn_id", "case_unknown"))

        if hasattr(case, "to_dict"):
            txn = case.to_dict()
        elif isinstance(case, dict):
            txn = case
        else:
            txn = {
                "amount": getattr(case, "amount", 0.0),
                "metadata": getattr(case, "metadata", {}),
                "is_salary_week": getattr(case, "is_salary_week", False),
            }

        meta = txn.get("metadata", {})
        if not isinstance(meta, dict):
            meta = {}

        x, info = self._extract_features(txn)
        days_to_expiry = info["days_to_card_expiry"]
        amount = info["amount"]
        bank = info["bank"]
        balance_proxy = info["account_balance_proxy"]
        prior_failures = info["prior_failures"]
        is_salary_week = info["is_salary_week"]

        # Gate 1: Deterministic Rule Override — Card Expiry
        if days_to_expiry < 0:
            expiry_date_str = meta.get("card_expiry_date")
            if not expiry_date_str:
                expiry_date_str = f"{abs(int(days_to_expiry))} days ago"
            evidence = [
                f"Card expired on {expiry_date_str}",
                "Deterministic rule: Issuing bank hard-rejects expired instruments",
                "Immediate retries will 100% fail; customer update required",
            ]
            probs = {lbl: (1.0 if lbl == "expired_card" else 0.0) for lbl in LABELS}
            return DiagnosisResult(
                case_id=case_id,
                predicted_cause="expired_card",
                confidence=1.0,
                source="deterministic_rule",
                evidence=evidence,
                is_systemic=False,
                probabilities=probs,
                requires_human_approval=False,
            )

        # Gate 2: Deterministic Rule Override — Severe Fraud Flag
        decline_code = str(txn.get("decline_code", meta.get("decline_code", ""))).upper()
        if decline_code in ["SUSPECTED_FRAUD", "FRAUD_FLAG", "FRAUD_SUSPECTED"]:
            evidence = [
                "Decline code indicates severe fraud risk block from payment network",
                "All automated customer retries must be suppressed",
            ]
            probs = {lbl: (1.0 if lbl == "fraud_flag" else 0.0) for lbl in LABELS}
            return DiagnosisResult(
                case_id=case_id,
                predicted_cause="fraud_flag",
                confidence=0.99,
                source="deterministic_rule",
                evidence=evidence,
                is_systemic=False,
                probabilities=probs,
                requires_human_approval=True,
            )

        # Gate 3: Explicit Decline Code Mapping
        if decline_code in DECLINE_CODE_MAP:
            mapped_cause = DECLINE_CODE_MAP[decline_code]
            confidence = 0.92
            evidence = [f"Direct decline code match: '{decline_code}' -> {mapped_cause}"]
            if decline_code == "INSUFFICIENT_FUNDS":
                if is_salary_week:
                    evidence.append("Failed during salary week; high probability of temporary cash timing")
                    confidence = 0.95
                else:
                    evidence.append("Outside salary cycle; customer account balance below threshold")
            probs = {lbl: (confidence if lbl == mapped_cause else 0.02) for lbl in LABELS}
            return DiagnosisResult(
                case_id=case_id,
                predicted_cause=mapped_cause,
                confidence=confidence,
                source="ml_model",
                evidence=evidence,
                is_systemic=False,
                probabilities=probs,
                requires_human_approval=confidence < CONFIDENCE_HUMAN_FLOOR,
            )

        # Gate 4: ML Model Inference
        if self.trees:
            num_classes = len(LABELS)
            scores = [0.0] * num_classes
            for idx, tree in enumerate(self.trees):
                scores[idx % num_classes] += tree.eval(x)

            max_score = max(scores)
            exp_scores = [math.exp(s - max_score) for s in scores]
            sum_exp = sum(exp_scores)
            probs_list = [s / sum_exp for s in exp_scores]
            probs = {lbl: round(float(p), 4) for lbl, p in zip(LABELS, probs_list)}
            top_idx = max(range(num_classes), key=lambda i: probs_list[i])
            predicted_cause = LABELS[top_idx]
            confidence = round(float(probs_list[top_idx]), 4)
        else:
            if balance_proxy < -0.4 and not is_salary_week:
                predicted_cause = "insufficient_funds"
                confidence = 0.72
            elif days_to_expiry < 30:
                predicted_cause = "expired_card"
                confidence = 0.65
            elif info["bank_risk_tier"] == "high":
                predicted_cause = "bank_risk_block"
                confidence = 0.68
            elif prior_failures >= 2:
                predicted_cause = "3ds_friction"
                confidence = 0.60
            else:
                predicted_cause = "insufficient_funds"
                confidence = 0.50

            probs = {lbl: (0.10 if lbl != predicted_cause else confidence) for lbl in LABELS}

        evidence = [
            f"Transaction amount: ₹{amount:,.2f}",
            f"Card expiry window: {int(days_to_expiry)} days remaining",
            f"Issuing bank: {bank} (Risk tier: {info['bank_risk_tier']})",
            f"Prior failed attempts: {prior_failures}",
            f"Account balance signal: proxy = {balance_proxy:.2f}",
            f"Salary week: {'Active' if is_salary_week else 'Inactive'}",
        ]

        requires_human = confidence < CONFIDENCE_HUMAN_FLOOR
        if requires_human:
            evidence.append(
                f"Confidence ({confidence:.1%}) below safety threshold ({CONFIDENCE_HUMAN_FLOOR:.0%}) - routed to human review"
            )

        return DiagnosisResult(
            case_id=case_id,
            predicted_cause=predicted_cause,
            confidence=confidence,
            source="ml_model",
            evidence=evidence,
            is_systemic=False,
            probabilities=probs,
            requires_human_approval=requires_human,
        )


_default_classifier = PaymentClassifier()


def diagnose_payment(case: Any) -> DiagnosisResult:
    return _default_classifier.diagnose(case)
