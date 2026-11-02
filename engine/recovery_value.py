"""
Cost-Aware Expected Economic Value (Net EV) Engine:
Evaluates interventions using:
Net EV = P(recovery) * Amount - Channel Cost - Discount - Risk Penalty - Human Cost
Always includes a DO_NOTHING baseline with Net EV = 0.00.
"""
from typing import List, Dict, Optional, Any
from engine.models import ActionType, EvaluatedIntervention
from engine.config import (
    CHANNEL_UNIT_COSTS,
    MARGIN_DISCOUNT_CAP_PCT,
    HUMAN_REVIEW_FEE,
)

# Aliases for backward and cross-agent compatibility
CHANNEL_COSTS = {
    ActionType[k.upper()]: v for k, v in CHANNEL_UNIT_COSTS.items() if hasattr(ActionType, k.upper())
}
HUMAN_OPERATIONAL_COST = HUMAN_REVIEW_FEE

INTERVENTION_PRIORS: Dict[str, Dict[ActionType, float]] = {
    "systemic_route_degraded": {
        ActionType.REROUTE_GATEWAY: 0.82,
        ActionType.RETRY_DELAYED: 0.76,
        ActionType.RETRY_IMMEDIATE: 0.05,
    },
    "issuer_down": {
        ActionType.REROUTE_GATEWAY: 0.80,
        ActionType.RETRY_DELAYED: 0.72,
        ActionType.RETRY_IMMEDIATE: 0.04,
        ActionType.HUMAN_ESCALATION: 0.20,
    },
    "expired_card": {
        ActionType.CARD_UPDATE_LINK: 0.65,
        ActionType.WHATSAPP_NUDGE: 0.40,
        ActionType.REMINDER_SMS: 0.35,
        ActionType.VOICE_CALL_HINGLISH: 0.40,
        ActionType.HUMAN_ESCALATION: 0.50,
        ActionType.RETRY_IMMEDIATE: 0.00,
        ActionType.RETRY_DELAYED: 0.00,
    },
    "insufficient_funds": {
        ActionType.RETRY_DELAYED: 0.68,
        ActionType.VOICE_CALL_HINGLISH: 0.48,
        ActionType.WHATSAPP_NUDGE: 0.45,
        ActionType.REMINDER_SMS: 0.35,
        ActionType.RETRY_IMMEDIATE: 0.12,
        ActionType.HUMAN_ESCALATION: 0.55,
    },
    "bank_risk_block": {
        ActionType.REROUTE_GATEWAY: 0.65,
        ActionType.RETRY_DELAYED: 0.40,
        ActionType.WHATSAPP_NUDGE: 0.30,
        ActionType.REMINDER_SMS: 0.20,
        ActionType.RETRY_IMMEDIATE: 0.10,
        ActionType.HUMAN_ESCALATION: 0.55,
    },
    "3ds_friction": {
        ActionType.WHATSAPP_NUDGE: 0.62,
        ActionType.REMINDER_SMS: 0.50,
        ActionType.VOICE_CALL_HINGLISH: 0.45,
        ActionType.RETRY_DELAYED: 0.30,
        ActionType.RETRY_IMMEDIATE: 0.20,
        ActionType.HUMAN_ESCALATION: 0.55,
    },
    "fraud_flag": {
        ActionType.HUMAN_ESCALATION: 0.40,
        ActionType.RETRY_IMMEDIATE: 0.00,
        ActionType.RETRY_DELAYED: 0.00,
        ActionType.WHATSAPP_NUDGE: 0.02,
        ActionType.REMINDER_SMS: 0.02,
    },
    "upi_timeout": {
        ActionType.RETRY_DELAYED: 0.70,
        ActionType.WHATSAPP_NUDGE: 0.55,
        ActionType.RETRY_IMMEDIATE: 0.20,
        ActionType.VOICE_CALL_HINGLISH: 0.35,
        ActionType.HUMAN_ESCALATION: 0.45,
    },
    "shipping_shock": {
        ActionType.DISCOUNT_INCENTIVE: 0.65,
        ActionType.WHATSAPP_NUDGE: 0.35,
        ActionType.REMINDER_SMS: 0.22,
        ActionType.VOICE_CALL_HINGLISH: 0.25,
        ActionType.HUMAN_ESCALATION: 0.35,
        ActionType.RETRY_IMMEDIATE: 0.00,
    },
    "otp_delay": {
        ActionType.WHATSAPP_NUDGE: 0.72,
        ActionType.REMINDER_SMS: 0.58,
        ActionType.RETRY_DELAYED: 0.20,
        ActionType.DISCOUNT_INCENTIVE: 0.40,
        ActionType.HUMAN_ESCALATION: 0.30,
    },
    "coupon_invalid": {
        ActionType.DISCOUNT_INCENTIVE: 0.68,
        ActionType.WHATSAPP_NUDGE: 0.38,
        ActionType.REMINDER_SMS: 0.30,
        ActionType.HUMAN_ESCALATION: 0.40,
    },
    "payment_page_crash": {
        ActionType.CARD_UPDATE_LINK: 0.64,
        ActionType.WHATSAPP_NUDGE: 0.52,
        ActionType.REMINDER_SMS: 0.38,
        ActionType.HUMAN_ESCALATION: 0.45,
    },
    "payment_method_missing": {
        ActionType.WHATSAPP_NUDGE: 0.52,
        ActionType.REMINDER_SMS: 0.38,
        ActionType.VOICE_CALL_HINGLISH: 0.35,
        ActionType.HUMAN_ESCALATION: 0.45,
    },
    "casual_browsing": {
        ActionType.DISCOUNT_INCENTIVE: 0.22,
        ActionType.WHATSAPP_NUDGE: 0.15,
        ActionType.REMINDER_SMS: 0.10,
    },
    "pre_debit_notification_missed": {
        ActionType.MANDATE_RETRY_SEQUENCE: 0.78,
        ActionType.RETRY_DELAYED: 0.65,
        ActionType.WHATSAPP_NUDGE: 0.52,
        ActionType.HUMAN_ESCALATION: 0.40,
        ActionType.RETRY_IMMEDIATE: 0.00,
    },
    "mandate_limit_exceeded": {
        ActionType.WHATSAPP_NUDGE: 0.65,
        ActionType.CARD_UPDATE_LINK: 0.62,
        ActionType.VOICE_CALL_HINGLISH: 0.55,
        ActionType.HUMAN_ESCALATION: 0.60,
        ActionType.MANDATE_RETRY_SEQUENCE: 0.10,
        ActionType.RETRY_IMMEDIATE: 0.00,
    },
    "card_expired_before_cycle": {
        ActionType.CARD_UPDATE_LINK: 0.68,
        ActionType.WHATSAPP_NUDGE: 0.50,
        ActionType.VOICE_CALL_HINGLISH: 0.45,
        ActionType.HUMAN_ESCALATION: 0.55,
        ActionType.MANDATE_RETRY_SEQUENCE: 0.00,
        ActionType.RETRY_IMMEDIATE: 0.00,
    },
    "issuer_auth_decline": {
        ActionType.MANDATE_RETRY_SEQUENCE: 0.60,
        ActionType.CARD_UPDATE_LINK: 0.55,
        ActionType.WHATSAPP_NUDGE: 0.50,
        ActionType.VOICE_CALL_HINGLISH: 0.45,
        ActionType.HUMAN_ESCALATION: 0.50,
    },
    "po_number_mismatch": {
        ActionType.HUMAN_ESCALATION: 0.85,
        ActionType.VOICE_CALL_HINGLISH: 0.45,
        ActionType.WHATSAPP_NUDGE: 0.40,
        ActionType.REMINDER_SMS: 0.25,
        ActionType.RETRY_IMMEDIATE: 0.00,
    },
    "invoice_dispute": {
        ActionType.HUMAN_ESCALATION: 0.90,
        ActionType.VOICE_CALL_HINGLISH: 0.00,
        ActionType.WHATSAPP_NUDGE: 0.00,
        ActionType.REMINDER_SMS: 0.00,
        ActionType.RETRY_IMMEDIATE: 0.00,
    },
    "cash_flow_timing": {
        ActionType.RETRY_DELAYED: 0.65,
        ActionType.VOICE_CALL_HINGLISH: 0.58,
        ActionType.WHATSAPP_NUDGE: 0.55,
        ActionType.REMINDER_SMS: 0.40,
        ActionType.HUMAN_ESCALATION: 0.60,
    },
    "forgot_payment": {
        ActionType.VOICE_CALL_HINGLISH: 0.52,
        ActionType.WHATSAPP_NUDGE: 0.60,
        ActionType.REMINDER_SMS: 0.50,
        ActionType.RETRY_DELAYED: 0.45,
        ActionType.HUMAN_ESCALATION: 0.65,
    },
    "wrong_accounts_payable_contact": {
        ActionType.HUMAN_ESCALATION: 0.80,
        ActionType.VOICE_CALL_HINGLISH: 0.45,
        ActionType.WHATSAPP_NUDGE: 0.20,
    },
}

RECOVERY_PROB_TABLE = INTERVENTION_PRIORS


def compute_net_ev(
    action: ActionType,
    root_cause: str,
    amount: float,
    p_recovery: Optional[float] = None,
    discount_pct: float = 0.05,
    risk_penalty: float = 0.0,
) -> EvaluatedIntervention:
    """Computes Net Expected Value for a specific intervention."""
    if action == ActionType.DO_NOTHING:
        return EvaluatedIntervention(
            action=action,
            p_recovery=0.0,
            expected_value=0.0,
            channel_cost=0.0,
            expected_discount=0.0,
            risk_penalty=0.0,
            human_cost=0.0,
            net_recovery=0.0,
            rationale="Baseline do_nothing: Zero intervention cost, zero active recovery.",
        )

    if p_recovery is None:
        p_recovery = INTERVENTION_PRIORS.get(root_cause, {}).get(action, 0.05)

    channel_cost = CHANNEL_UNIT_COSTS.get(action.value, 2.00)
    expected_discount = 0.0
    if action == ActionType.DISCOUNT_INCENTIVE:
        capped_pct = min(discount_pct, MARGIN_DISCOUNT_CAP_PCT)
        expected_discount = round(capped_pct * amount, 2)

    human_cost = HUMAN_REVIEW_FEE if action == ActionType.HUMAN_ESCALATION else 0.00
    if root_cause == "fraud_flag" and action in [
        ActionType.RETRY_IMMEDIATE,
        ActionType.RETRY_DELAYED,
        ActionType.REROUTE_GATEWAY,
    ]:
        risk_penalty = max(risk_penalty, 500.00)

    gross_recovery = p_recovery * amount
    net_recovery = round(
        gross_recovery - channel_cost - expected_discount - risk_penalty - human_cost, 2
    )

    rationale = (
        f"{action.value}: Net EV = ₹{net_recovery:,.2f} "
        f"(P = {p_recovery:.0%}, Gross = ₹{gross_recovery:,.2f}, Cost = ₹{channel_cost:,.2f}, "
        f"Disc = ₹{expected_discount:,.2f}, Human = ₹{human_cost:,.2f}, Risk = ₹{risk_penalty:,.2f})"
    )

    return EvaluatedIntervention(
        action=action,
        p_recovery=round(p_recovery, 4),
        expected_value=net_recovery,
        channel_cost=channel_cost,
        expected_discount=expected_discount,
        risk_penalty=risk_penalty,
        human_cost=human_cost,
        net_recovery=net_recovery,
        rationale=rationale,
    )


def rank_interventions(
    root_cause: str,
    amount: float,
    prior_failures: int = 0,
    is_salary_week: bool = False,
    discount_cap_pct: float = MARGIN_DISCOUNT_CAP_PCT,
    confidence: float = 1.0,
    is_systemic: bool = False,
    discount_pct: Optional[float] = None,
    custom_probs: Optional[Dict[str, float]] = None,
    candidate_actions: Optional[List[ActionType]] = None,
    leak_type: Optional[str] = None,
) -> List[EvaluatedIntervention]:
    """
    Evaluates candidate interventions against root cause, computing Net EV and ranking them.
    Always includes DO_NOTHING with Net EV = 0.0.
    """
    if discount_pct is None:
        discount_pct = discount_cap_pct

    priors = INTERVENTION_PRIORS.get(root_cause, {
        ActionType.WHATSAPP_NUDGE: 0.40,
        ActionType.REMINDER_SMS: 0.30,
        ActionType.RETRY_DELAYED: 0.35,
    })

    # Include candidate actions
    actions_to_evaluate: List[ActionType] = []
    if candidate_actions:
        actions_to_evaluate = [a for a in candidate_actions if a != ActionType.DO_NOTHING]
    else:
        # Candidate set from priors plus common alternatives
        action_set = set(priors.keys())
        if root_cause == "expired_card":
            action_set.update([ActionType.RETRY_IMMEDIATE, ActionType.RETRY_DELAYED, ActionType.CARD_UPDATE_LINK])
        elif root_cause == "casual_browsing":
            action_set.update([ActionType.REMINDER_SMS, ActionType.WHATSAPP_NUDGE, ActionType.DISCOUNT_INCENTIVE])
        actions_to_evaluate = list(action_set)

    evaluated: List[EvaluatedIntervention] = []

    for action in actions_to_evaluate:
        base_p = priors.get(action, 0.0)
        p = base_p * (0.85 if confidence < 0.7 else 1.0)
        if is_salary_week and action in (ActionType.RETRY_DELAYED, ActionType.VOICE_CALL_HINGLISH):
            p = min(0.95, p * 1.15)
        if prior_failures > 0 and p > 0:
            p = max(0.01, p * (0.82 ** prior_failures))

        if custom_probs and action.value in custom_probs:
            p = custom_probs[action.value]

        interv = compute_net_ev(
            action=action,
            root_cause=root_cause,
            amount=amount,
            p_recovery=p,
            discount_pct=discount_pct,
        )
        evaluated.append(interv)

    # Always include DO_NOTHING baseline with Net EV = 0.00
    evaluated.append(compute_net_ev(ActionType.DO_NOTHING, root_cause, amount))

    # Rank descending by Net EV
    evaluated.sort(key=lambda x: x.net_recovery, reverse=True)
    return evaluated
