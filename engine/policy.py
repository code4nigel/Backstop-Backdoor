"""
Deterministic Policy & Compliance Engine for Backstop.

Enforces deterministic stopping rules, regulatory compliance gates,
quiet hours (RBI recovery norms), TRAI DND, touch frequency caps,
and automated human review queue routing.

Guarantees:
- 100% deterministic Python logic — never delegated to an LLM.
- Gate 1: Opt-Out / TRAI DND blocking of outreach.
- Gate 2: RBI Quiet Hours (19:00 - 08:00) suppression of voice/promotional touches.
- Gate 3: Dispute & Hardship outreach freezing.
- Gate 4: Frequency caps (max 2 touches / 7 days, max 3 attempts total).
- Gate 5: Human review queue routing for high-value (>= ₹50k), low-confidence (< 0.35),
          disputes, or discount cap breaches.
- Systemic degradation routing: suppresses outreach and selects technical reroute.
- Explainable decision rationale generated for every evaluated case.
"""
from typing import List, Optional, Dict, Any, Set
from datetime import datetime

from engine.models import (
    RecoveryCase,
    ActionType,
    PolicyDecision,
    EvaluatedIntervention,
    CaseState,
    DiagnosisResult,
)
from engine.config import (
    MAX_ATTEMPTS,
    MAX_CUSTOMER_TOUCHES_7D,
    QUIET_HOURS_START,
    QUIET_HOURS_END,
    HIGH_VALUE_THRESHOLD,
    CONFIDENCE_HUMAN_FLOOR,
    MARGIN_DISCOUNT_CAP_PCT,
    MIN_CASE_VALUE,
    HUMAN_REVIEW_FEE,
)
from engine.recovery_value import rank_interventions, HUMAN_OPERATIONAL_COST
from engine.diagnosis import MultiLeakDiagnosisDispatcher, DiagnosisEngine
from engine.systemic import SystemicDegradationDetector

CUSTOMER_FACING_ACTIONS: Set[ActionType] = {
    ActionType.REMINDER_SMS,
    ActionType.WHATSAPP_NUDGE,
    ActionType.VOICE_CALL_HINGLISH,
    ActionType.DISCOUNT_INCENTIVE,
    ActionType.CARD_UPDATE_LINK,
}

QUIET_HOURS_RESTRICTED_ACTIONS: Set[ActionType] = {
    ActionType.VOICE_CALL_HINGLISH,
    ActionType.WHATSAPP_NUDGE,
    ActionType.DISCOUNT_INCENTIVE,
    ActionType.REMINDER_SMS,
}


class PolicyEngine:
    """
    Deterministic Policy & Compliance Engine.
    Evaluates cases against RBI norms, TRAI rules, and economic thresholds.
    """

    def __init__(
        self,
        diagnosis: Optional[Any] = None,
        systemic_detector: Optional[SystemicDegradationDetector] = None,
        ledger: Any = None,
        quiet_hours_start: int = QUIET_HOURS_START,
        quiet_hours_end: int = QUIET_HOURS_END,
        max_attempts: int = MAX_ATTEMPTS,
        max_touches_7d: int = MAX_CUSTOMER_TOUCHES_7D,
        high_value_floor: float = HIGH_VALUE_THRESHOLD,
        confidence_floor: float = CONFIDENCE_HUMAN_FLOOR,
        min_case_value: float = MIN_CASE_VALUE,
    ):
        self.systemic_detector = systemic_detector or SystemicDegradationDetector()
        self.diagnosis = diagnosis or MultiLeakDiagnosisDispatcher(
            systemic_detector=self.systemic_detector
        )
        self.ledger = ledger
        self.quiet_hours_start = quiet_hours_start
        self.quiet_hours_end = quiet_hours_end
        self.max_attempts = max_attempts
        self.max_touches_7d = max_touches_7d
        self.high_value_floor = high_value_floor
        self.confidence_floor = confidence_floor
        self.min_case_value = min_case_value

    def is_quiet_hours(self, current_hour: Optional[int] = None) -> bool:
        """RBI Recovery Norm: Contact between 19:00 (7 PM) and 08:00 (8 AM) is prohibited."""
        if current_hour is None:
            current_hour = datetime.now().hour
        if self.quiet_hours_start > self.quiet_hours_end:
            return current_hour >= self.quiet_hours_start or current_hour < self.quiet_hours_end
        return self.quiet_hours_start <= current_hour < self.quiet_hours_end

    def evaluate(
        self,
        case: Any,
        diagnosed_cause: Optional[Any] = None,
        confidence: Optional[float] = None,
        current_hour: Optional[int] = None,
        override_quiet_hours: Optional[bool] = None,
        discount_pct: float = 0.05,
        alternate_gateway_available: bool = True,
        prior_history: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> PolicyDecision:
        """
        Evaluate a case against deterministic compliance rules and Net EV ranking.
        Supports both RecoveryCase and dicts, and handles various positional arg conventions.
        """
        # Handle 2nd argument passed as prior_history dict: e.g. evaluate(txn, state_dict)
        if isinstance(diagnosed_cause, dict):
            prior_history = diagnosed_cause
            diagnosed_cause = None

        if prior_history is None:
            prior_history = {}

        # Merge kwargs
        if "discount_pct" in kwargs:
            discount_pct = kwargs["discount_pct"]
        if "alternate_gateway_available" in kwargs:
            alternate_gateway_available = kwargs["alternate_gateway_available"]

        # Normalize case fields
        case_id = getattr(case, "case_id", "")
        if not case_id and isinstance(case, dict):
            case_id = case.get("case_id", case.get("txn_id", case.get("invoice_id", "case_unknown")))

        amount = float(getattr(case, "amount", 0.0) if hasattr(case, "amount") else case.get("amount", 0.0))
        meta = getattr(case, "metadata", {}) if hasattr(case, "metadata") else case.get("metadata", case)
        if not isinstance(meta, dict):
            meta = {}

        applied_rules: List[str] = []
        human_reasons: List[str] = []
        requires_human = False

        # Attempt and touch tracking
        attempts = int(
            prior_history.get(
                "attempts",
                meta.get("prior_failures", meta.get("attempt_number", 0)),
            )
        )
        attempt_number = attempts + 1

        prior_touches_7d = int(
            prior_history.get(
                "prior_touches_7d",
                meta.get("customer_touches_7d", meta.get("prior_touches_7d", 0)),
            )
        )

        opt_out = bool(
            prior_history.get("opt_out", False)
            or meta.get("opt_out", False)
            or meta.get("is_dnd", False)
            or meta.get("dnd_registered", False)
            or (case.get("opt_out", False) if isinstance(case, dict) else False)
        )

        dispute_flag = bool(
            getattr(case, "state", None) == CaseState.DISPUTED
            or meta.get("dispute_flag", False)
            or meta.get("has_dispute", False)
            or meta.get("is_disputed", False)
            or (case.get("dispute_flag", False) if isinstance(case, dict) else False)
            or "already paid" in str(meta.get("notes", "")).lower()
            or "hardship" in str(meta.get("notes", "")).lower()
            or "bereavement" in str(meta.get("notes", "")).lower()
        )

        # -------------------------------------------------------------
        # Gate 3: Dispute & Hardship Freeze
        # -------------------------------------------------------------
        if dispute_flag:
            applied_rules.append("RULE_DISPUTE_FREEZE_TRIGGERED")
            ranked = rank_interventions(root_cause="invoice_dispute", amount=amount)
            return PolicyDecision(
                case_id=case_id,
                allowed=False,
                chosen_action=ActionType.DO_NOTHING,
                blocked_reason="Customer dispute or hardship active - outreach frozen",
                applied_rules=applied_rules,
                requires_human_approval=True,
                human_review_reason="Active dispute or customer hardship claim ('already paid' / bereavement)",
                ranked_interventions=ranked,
                rationale="Outreach frozen: Case is disputed or customer claimed hardship ('already paid'). Flagged for human review.",
                root_cause="invoice_dispute",
                confidence=1.0,
                expected_value=0.0,
                attempt_number=attempt_number,
                stopped=True,
                stop_reason="Customer dispute or hardship active - outreach frozen",
            )

        # -------------------------------------------------------------
        # Gate 4: Max Total Attempts Cap
        # -------------------------------------------------------------
        if attempts >= self.max_attempts or attempt_number > self.max_attempts:
            applied_rules.append("RULE_MAX_ATTEMPTS_EXHAUSTED")
            ranked = rank_interventions(root_cause="insufficient_funds", amount=amount)
            return PolicyDecision(
                case_id=case_id,
                allowed=False,
                chosen_action=ActionType.DO_NOTHING,
                blocked_reason="Max frequency cap reached",
                applied_rules=applied_rules,
                requires_human_approval=False,
                ranked_interventions=ranked,
                rationale=f"Policy Blocked: Exceeded maximum allowed attempts ({self.max_attempts}).",
                root_cause="max_attempts_exceeded",
                confidence=1.0,
                expected_value=0.0,
                attempt_number=attempt_number,
                stopped=True,
                stop_reason="Max frequency cap reached",
            )

        # -------------------------------------------------------------
        # Step: Root Cause Diagnosis (if not supplied)
        # -------------------------------------------------------------
        is_systemic = False
        if diagnosed_cause is None:
            diag: DiagnosisResult = self.diagnosis.diagnose(case)
            diagnosed_cause = diag.predicted_cause
            if confidence is None:
                confidence = diag.confidence
            is_systemic = diag.is_systemic
            if diag.requires_human_approval:
                requires_human = True
                human_reasons.append("Diagnosis confidence floor triggered human review")
        else:
            if confidence is None:
                confidence = 1.0

        # Check systemic detector directly if route metadata is present
        bank = meta.get("bank", case.get("bank", "")) if isinstance(case, dict) else meta.get("bank", "")
        gateway = meta.get("gateway", None)
        upi_app = meta.get("upi_app", None)
        if bank and self.systemic_detector.is_route_degraded(bank, gateway, upi_app):
            is_systemic = True
            diagnosed_cause = "issuer_down"

        # -------------------------------------------------------------
        # Step: Net EV Ranking over Interventions
        # -------------------------------------------------------------
        is_salary_week = getattr(case, "is_salary_week", False) or meta.get("is_salary_week", False)
        ranked = rank_interventions(
            root_cause=diagnosed_cause,
            amount=amount,
            prior_failures=attempts,
            is_salary_week=bool(is_salary_week),
            confidence=confidence,
            is_systemic=is_systemic,
            discount_pct=discount_pct,
        )

        top_intervention = ranked[0]
        chosen_action = top_intervention.action

        # Negative Net EV Check: if top action has EV <= 0 or is DO_NOTHING
        if chosen_action == ActionType.DO_NOTHING or top_intervention.net_recovery <= 0:
            applied_rules.append("RULE_NEGATIVE_NET_EV")
            return PolicyDecision(
                case_id=case_id,
                allowed=False,
                chosen_action=ActionType.DO_NOTHING,
                blocked_reason="Negative Net EV: expected cost of recovery exceeds projected return",
                applied_rules=applied_rules,
                requires_human_approval=False,
                ranked_interventions=ranked,
                rationale="Economic baseline do_nothing selected: all active interventions produce negative Net EV.",
                root_cause=diagnosed_cause,
                confidence=confidence,
                expected_value=0.0,
                attempt_number=attempt_number,
                stopped=True,
                stop_reason="Negative Net EV",
            )

        # -------------------------------------------------------------
        # Gate: Systemic Route Degradation Override
        # -------------------------------------------------------------
        if is_systemic:
            applied_rules.append("RULE_SYSTEMIC_DEGRADATION_REROUTE")
            chosen_action = (
                ActionType.REROUTE_GATEWAY
                if alternate_gateway_available
                else ActionType.RETRY_DELAYED
            )
            # Find chosen intervention
            chosen_interv = next((i for i in ranked if i.action == chosen_action), top_intervention)
            rationale = (
                f"Systemic Degradation active on {bank}. Suppressed all customer outreach to protect merchant reputation. "
                f"Chose '{chosen_action.value}' (EV = ₹{chosen_interv.net_recovery:,.2f}, P = {chosen_interv.p_recovery:.0%})."
            )
            return PolicyDecision(
                case_id=case_id,
                allowed=True,
                chosen_action=chosen_action,
                blocked_reason=None,
                applied_rules=applied_rules,
                requires_human_approval=False,
                ranked_interventions=ranked,
                rationale=rationale,
                root_cause=diagnosed_cause,
                confidence=confidence,
                expected_value=chosen_interv.net_recovery,
                attempt_number=attempt_number,
                stopped=False,
            )

        # -------------------------------------------------------------
        # Gate 1: Opt-Out / TRAI DND
        # -------------------------------------------------------------
        if opt_out and chosen_action in CUSTOMER_FACING_ACTIONS:
            applied_rules.append("RULE_DND_OPTOUT_BLOCKED")
            return PolicyDecision(
                case_id=case_id,
                allowed=False,
                chosen_action=ActionType.DO_NOTHING,
                blocked_reason="Customer DND / Opt-Out active",
                applied_rules=applied_rules,
                requires_human_approval=False,
                ranked_interventions=ranked,
                rationale="Blocked by policy: Customer DND / Opt-Out active. Outbound outreach prohibited.",
                root_cause=diagnosed_cause,
                confidence=confidence,
                expected_value=0.0,
                attempt_number=attempt_number,
                stopped=True,
                stop_reason="Customer DND / Opt-Out active",
            )

        # -------------------------------------------------------------
        # Gate 4B: 7-Day Rolling Touch Limit
        # -------------------------------------------------------------
        if prior_touches_7d >= self.max_touches_7d and chosen_action in CUSTOMER_FACING_ACTIONS:
            applied_rules.append("RULE_TOUCH_LIMIT_EXCEEDED")
            return PolicyDecision(
                case_id=case_id,
                allowed=False,
                chosen_action=ActionType.DO_NOTHING,
                blocked_reason="Max frequency cap reached",
                applied_rules=applied_rules,
                requires_human_approval=False,
                ranked_interventions=ranked,
                rationale=f"Blocked by policy: Max frequency cap reached ({prior_touches_7d}/{self.max_touches_7d} touches in last 7 days).",
                root_cause=diagnosed_cause,
                confidence=confidence,
                expected_value=0.0,
                attempt_number=attempt_number,
                stopped=True,
                stop_reason="Max frequency cap reached",
            )

        # -------------------------------------------------------------
        # Gate 2: Quiet Hours (RBI Norms)
        # -------------------------------------------------------------
        quiet_hours = (
            self.is_quiet_hours(current_hour)
            if override_quiet_hours is None
            else override_quiet_hours
        )
        if quiet_hours and chosen_action in QUIET_HOURS_RESTRICTED_ACTIONS:
            applied_rules.append("RULE_QUIET_HOURS_DEFERRED")
            return PolicyDecision(
                case_id=case_id,
                allowed=False,
                chosen_action=ActionType.DO_NOTHING,
                blocked_reason="Quiet hours (19:00 - 08:00) active - deferred to next morning",
                applied_rules=applied_rules,
                requires_human_approval=False,
                ranked_interventions=ranked,
                rationale="Blocked by policy: Quiet hours (19:00 - 08:00) active - deferred to next morning.",
                root_cause=diagnosed_cause,
                confidence=confidence,
                expected_value=0.0,
                attempt_number=attempt_number,
                stopped=True,
                stop_reason="Quiet hours (19:00 - 08:00) active - deferred to next morning",
            )

        # -------------------------------------------------------------
        # Gate 5: Human Review Queue Routing Checks
        # -------------------------------------------------------------
        if amount >= self.high_value_floor:
            requires_human = True
            human_reasons.append(
                f"High-value case (₹{amount:,.2f} >= ₹{self.high_value_floor:,.2f})"
            )
            applied_rules.append("RULE_HIGH_VALUE_REVIEW")

        if confidence < self.confidence_floor:
            requires_human = True
            human_reasons.append(
                f"Low diagnosis confidence ({confidence:.2f} < {self.confidence_floor:.2f})"
            )
            applied_rules.append("RULE_LOW_CONFIDENCE_REVIEW")

        if discount_pct > MARGIN_DISCOUNT_CAP_PCT:
            requires_human = True
            human_reasons.append(
                f"Requested discount ({discount_pct:.1%}) exceeds 10% margin cap guardrail"
            )
            applied_rules.append("RULE_MARGIN_CAP_BREACH")

        applied_rules.append("RULE_POLICY_APPROVED")

        # Explainable Rationale Construction
        chosen_interv = next((i for i in ranked if i.action == chosen_action), top_intervention)
        rationale_parts = [
            f"Chose '{chosen_action.value}' (EV = ₹{chosen_interv.net_recovery:,.2f}, P = {chosen_interv.p_recovery:.0%})."
        ]

        for alt in ranked[:3]:
            if alt.action != chosen_action:
                if alt.p_recovery == 0.0:
                    rationale_parts.append(
                        f"'{alt.action.value}' rejected because root cause is {diagnosed_cause} (P = 0.0)."
                    )
                elif alt.action == ActionType.HUMAN_ESCALATION:
                    rationale_parts.append(
                        f"'{alt.action.value}' rejected due to operational cost (₹{HUMAN_OPERATIONAL_COST:.0f}) yielding lower Net EV (₹{alt.net_recovery:,.2f})."
                    )
                elif alt.net_recovery < chosen_interv.net_recovery:
                    rationale_parts.append(
                        f"'{alt.action.value}' yields lower Net EV (₹{alt.net_recovery:,.2f})."
                    )

        return PolicyDecision(
            case_id=case_id,
            allowed=True,
            chosen_action=chosen_action,
            blocked_reason=None,
            applied_rules=applied_rules,
            requires_human_approval=requires_human,
            human_review_reason="; ".join(human_reasons) if human_reasons else None,
            ranked_interventions=ranked,
            rationale=" ".join(rationale_parts),
            root_cause=diagnosed_cause,
            confidence=confidence,
            expected_value=chosen_interv.net_recovery,
            attempt_number=attempt_number,
            stopped=False,
        )
