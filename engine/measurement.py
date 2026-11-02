"""
Scientific & Truthful Measurement Engine for Revenue Recovery.
Provides:
  - Ground-truth outcome transition matrix across all 4 leak types and 11 action types.
  - Deterministic pseudo-random simulation seeded with hash(case_id, run_id).
  - Stratified holdout control assignment (15-20% ratio across leak_type and amount buckets).
  - Multi-strategy financial & statistical evaluation:
      * Strategy 0: Do Nothing (Organic self-cure only)
      * Strategy 1: Naive Baseline (Immediate retry / generic SMS blast)
      * Strategy 2: Backstop Agent (Diagnosis + Cost-Aware EV + Stopping Rules + Holdout)
  - Financial P&L: Gross, Channel Cost, Discounts Given, Human Review Cost (₹150), Net Recovery,
    Incremental Lift over Control and Naive, ROI, and 95% Confidence Intervals.
"""
from dataclasses import dataclass, field
import hashlib
import math
import random
from typing import Any, Dict, List, Optional, Tuple, Union

from engine.models import ActionType, LeakType, RecoveryCase
from engine.config import CHANNEL_UNIT_COSTS, DEFAULT_SEED, HOLDOUT_RATIO, HUMAN_REVIEW_FEE

# Alias for compatibility
ACTION_UNIT_COST: Dict[str, float] = CHANNEL_UNIT_COSTS
DEFAULT_DISCOUNT_RATE: float = 0.08  # 8% discount
ORGANIC_CURE_BASE_RATE: float = 0.065

# ---------------------------------------------------------------------------
# 1. GROUND TRUTH OUTCOME TRANSITION MATRIX
# ---------------------------------------------------------------------------
# Probability of recovery P(Recovery | True_Root_Cause, Action, Context)
# Evaluated strictly against the TRUE hidden root cause, NOT the agent's diagnosis!
# This strictly penalizes ML misdiagnoses (e.g., retrying an expired card has 0.00 recovery).

GROUND_TRUTH_RECOVERY_PROB: Dict[str, Dict[str, float]] = {
    # --- Systemic & Gateway Degradation ---
    "systemic_route_degraded": {
        "reroute_gateway": 0.84,
        "retry_delayed": 0.78,
        "retry_immediate": 0.04,
        "reminder_sms": 0.02,
        "whatsapp_nudge": 0.02,
        "voice_call_hinglish": 0.02,
        "do_nothing": 0.03,
    },
    "issuer_down": {
        "reroute_gateway": 0.82,
        "retry_delayed": 0.75,
        "retry_immediate": 0.02,      # Immediate retry fails because route/issuer is down
        "whatsapp_nudge": 0.15,
        "reminder_sms": 0.10,
        "voice_call_hinglish": 0.20,
        "card_update_link": 0.10,
        "mandate_retry_sequence": 0.15,
        "discount_incentive": 0.05,
        "human_escalation": 0.55,
        "do_nothing": 0.04,
    },

    # --- Failed Payment Causes ---
    "insufficient_funds": {
        "retry_delayed": 0.68,       # High recovery if timed well (payday/salary cycle)
        "voice_call_hinglish": 0.62,
        "whatsapp_nudge": 0.48,
        "retry_immediate": 0.08,     # Immediate retry has low success
        "reminder_sms": 0.35,
        "human_escalation": 0.65,
        "reroute_gateway": 0.08,
        "card_update_link": 0.20,
        "mandate_retry_sequence": 0.15,
        "discount_incentive": 0.20,
        "do_nothing": 0.06,          # Base organic cure rate
    },
    "expired_card": {
        "card_update_link": 0.68,     # Primary correct action
        "whatsapp_nudge": 0.52,
        "voice_call_hinglish": 0.58,
        "reminder_sms": 0.40,
        "human_escalation": 0.70,
        "retry_immediate": 0.00,      # Will ALWAYS decline at gateway!
        "retry_delayed": 0.02,        # In case new card issued, but rare
        "reroute_gateway": 0.00,
        "mandate_retry_sequence": 0.01,
        "discount_incentive": 0.05,
        "do_nothing": 0.01,
    },
    "bank_risk_block": {
        "reroute_gateway": 0.65,      # Alternative route circumvents issuer block
        "voice_call_hinglish": 0.58,
        "human_escalation": 0.68,
        "whatsapp_nudge": 0.38,
        "retry_delayed": 0.28,
        "reminder_sms": 0.25,
        "card_update_link": 0.20,
        "mandate_retry_sequence": 0.18,
        "retry_immediate": 0.04,
        "discount_incentive": 0.05,
        "do_nothing": 0.03,
    },
    "3ds_friction": {
        "whatsapp_nudge": 0.65,       # Frictionless WhatsApp deep-link
        "reminder_sms": 0.52,
        "voice_call_hinglish": 0.55,
        "reroute_gateway": 0.35,
        "retry_delayed": 0.32,
        "card_update_link": 0.30,
        "retry_immediate": 0.18,
        "mandate_retry_sequence": 0.15,
        "human_escalation": 0.58,
        "discount_incentive": 0.15,
        "do_nothing": 0.08,
    },
    "upi_timeout": {
        "reroute_gateway": 0.72,      # Reroute to resilient UPI PSP handle
        "retry_delayed": 0.65,
        "whatsapp_nudge": 0.55,
        "voice_call_hinglish": 0.48,
        "reminder_sms": 0.38,
        "retry_immediate": 0.18,
        "mandate_retry_sequence": 0.20,
        "card_update_link": 0.10,
        "human_escalation": 0.50,
        "discount_incentive": 0.10,
        "do_nothing": 0.07,
    },
    "fraud_flag": {
        "human_escalation": 0.35,     # Requires compliance/fraud manual review
        "voice_call_hinglish": 0.10,
        "reminder_sms": 0.02,
        "whatsapp_nudge": 0.02,
        "retry_delayed": 0.01,
        "retry_immediate": 0.00,
        "reroute_gateway": 0.00,
        "card_update_link": 0.01,
        "mandate_retry_sequence": 0.00,
        "discount_incentive": 0.00,
        "do_nothing": 0.00,
    },

    # --- Abandoned Checkout Causes ---
    "shipping_shock": {
        "discount_incentive": 0.70,   # Free shipping or discount directly addresses shock
        "whatsapp_nudge": 0.38,
        "voice_call_hinglish": 0.40,
        "reminder_sms": 0.22,
        "human_escalation": 0.45,
        "retry_delayed": 0.02,
        "retry_immediate": 0.00,
        "reroute_gateway": 0.00,
        "card_update_link": 0.00,
        "mandate_retry_sequence": 0.00,
        "do_nothing": 0.04,
    },
    "coupon_invalid": {
        "discount_incentive": 0.74,   # Working replacement voucher recovers checkout
        "whatsapp_nudge": 0.45,
        "voice_call_hinglish": 0.42,
        "reminder_sms": 0.30,
        "human_escalation": 0.50,
        "retry_immediate": 0.00,
        "retry_delayed": 0.01,
        "reroute_gateway": 0.00,
        "card_update_link": 0.00,
        "mandate_retry_sequence": 0.00,
        "do_nothing": 0.03,
    },
    "otp_delay": {
        "whatsapp_nudge": 0.72,       # WhatsApp 1-tap UPI or instant link
        "reminder_sms": 0.58,
        "voice_call_hinglish": 0.52,
        "retry_delayed": 0.30,
        "retry_immediate": 0.18,
        "reroute_gateway": 0.28,
        "card_update_link": 0.15,
        "human_escalation": 0.50,
        "discount_incentive": 0.25,
        "mandate_retry_sequence": 0.10,
        "do_nothing": 0.08,
    },
    "payment_page_crash": {
        "whatsapp_nudge": 0.68,       # Direct hosted checkout URL bypasses buggy page
        "card_update_link": 0.62,
        "reminder_sms": 0.52,
        "voice_call_hinglish": 0.54,
        "reroute_gateway": 0.35,
        "retry_delayed": 0.25,
        "retry_immediate": 0.10,
        "human_escalation": 0.55,
        "discount_incentive": 0.30,
        "mandate_retry_sequence": 0.10,
        "do_nothing": 0.05,
    },
    "casual_browsing": {
        "whatsapp_nudge": 0.22,
        "discount_incentive": 0.24,
        "voice_call_hinglish": 0.18,
        "reminder_sms": 0.12,
        "human_escalation": 0.20,
        "retry_immediate": 0.00,
        "retry_delayed": 0.01,
        "reroute_gateway": 0.00,
        "card_update_link": 0.00,
        "mandate_retry_sequence": 0.00,
        "do_nothing": 0.02,
    },

    # --- Subscription Mandate Causes ---
    "mandate_limit_exceeded": {
        "card_update_link": 0.65,     # Update mandate limit or switch payment method
        "whatsapp_nudge": 0.54,
        "human_escalation": 0.72,
        "voice_call_hinglish": 0.60,
        "reminder_sms": 0.38,
        "mandate_retry_sequence": 0.12,
        "retry_delayed": 0.05,
        "retry_immediate": 0.01,
        "reroute_gateway": 0.02,
        "discount_incentive": 0.10,
        "do_nothing": 0.03,
    },
    "card_expired_before_cycle": {
        "card_update_link": 0.70,     # Prompt user to replace expired mandate card
        "whatsapp_nudge": 0.56,
        "human_escalation": 0.70,
        "voice_call_hinglish": 0.62,
        "reminder_sms": 0.40,
        "mandate_retry_sequence": 0.02,
        "retry_delayed": 0.02,
        "retry_immediate": 0.00,
        "reroute_gateway": 0.00,
        "discount_incentive": 0.08,
        "do_nothing": 0.02,
    },
    "issuer_auth_decline": {
        "mandate_retry_sequence": 0.64, # Controlled sequence following RBI mandate guidelines
        "whatsapp_nudge": 0.50,
        "voice_call_hinglish": 0.54,
        "retry_delayed": 0.46,
        "human_escalation": 0.62,
        "card_update_link": 0.40,
        "reminder_sms": 0.36,
        "reroute_gateway": 0.35,
        "retry_immediate": 0.05,
        "discount_incentive": 0.10,
        "do_nothing": 0.05,
    },
    "pre_debit_notification_missed": {
        "mandate_retry_sequence": 0.78, # Re-issue pre-debit notice and execute sequence
        "whatsapp_nudge": 0.58,
        "voice_call_hinglish": 0.52,
        "reminder_sms": 0.44,
        "human_escalation": 0.60,
        "retry_delayed": 0.35,
        "card_update_link": 0.25,
        "reroute_gateway": 0.10,
        "retry_immediate": 0.04,
        "discount_incentive": 0.08,
        "do_nothing": 0.05,
    },

    # --- B2B Invoice Causes ---
    "forgot_payment": {
        "voice_call_hinglish": 0.78,
        "whatsapp_nudge": 0.68,
        "human_escalation": 0.86,
        "reminder_sms": 0.52,
        "discount_incentive": 0.15,
        "retry_delayed": 0.10,
        "card_update_link": 0.05,
        "mandate_retry_sequence": 0.05,
        "retry_immediate": 0.00,
        "reroute_gateway": 0.00,
        "do_nothing": 0.08,          # Accounts payable often cleans up backlog organically
    },
    "cash_flow_timing": {
        "voice_call_hinglish": 0.74,
        "whatsapp_nudge": 0.60,
        "human_escalation": 0.80,
        "retry_delayed": 0.58,       # Scheduling payment for upcoming invoice cycle / Net-15
        "reminder_sms": 0.42,
        "discount_incentive": 0.35,  # Early payment settlement discount
        "card_update_link": 0.05,
        "mandate_retry_sequence": 0.05,
        "retry_immediate": 0.00,
        "reroute_gateway": 0.00,
        "do_nothing": 0.08,
    },
    "invoice_dispute": {
        "human_escalation": 0.88,    # Dispute reconciliation requires finance ops human
        "voice_call_hinglish": 0.45,
        "discount_incentive": 0.25,
        "whatsapp_nudge": 0.14,
        "reminder_sms": 0.06,
        "retry_delayed": 0.00,
        "retry_immediate": 0.00,
        "reroute_gateway": 0.00,
        "card_update_link": 0.00,
        "mandate_retry_sequence": 0.00,
        "do_nothing": 0.02,
    },
    "po_number_mismatch": {
        "human_escalation": 0.88,    # Ops corrects PO metadata with buyer finance team
        "voice_call_hinglish": 0.54,
        "whatsapp_nudge": 0.25,
        "reminder_sms": 0.12,
        "discount_incentive": 0.05,
        "retry_delayed": 0.00,
        "retry_immediate": 0.00,
        "reroute_gateway": 0.00,
        "card_update_link": 0.00,
        "mandate_retry_sequence": 0.00,
        "do_nothing": 0.02,
    },
    "wrong_accounts_payable_contact": {
        "human_escalation": 0.85,    # Escalation discovers correct AP personnel
        "voice_call_hinglish": 0.60,
        "whatsapp_nudge": 0.20,
        "reminder_sms": 0.06,
        "discount_incentive": 0.02,
        "retry_delayed": 0.00,
        "retry_immediate": 0.00,
        "reroute_gateway": 0.00,
        "card_update_link": 0.00,
        "mandate_retry_sequence": 0.00,
        "do_nothing": 0.02,
    },
}


# ---------------------------------------------------------------------------
# 2. DETERMINISTIC PSEUDO-RANDOM SIMULATION SEEDING
# ---------------------------------------------------------------------------

def get_deterministic_rng(case_id: str, run_id: str) -> random.Random:
    """Generates a deterministic RNG instance from sha256(case_id:run_id)."""
    seed_str = f"{case_id}:{run_id}"
    seed_int = int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest()[:8], 16)
    return random.Random(seed_int)


def get_ground_truth_prob(
    true_root_cause: str,
    action: Union[ActionType, str],
    is_salary_week: bool = False,
) -> float:
    """
    Returns the ground-truth probability of recovery for a given true root cause and action.
    Applies salary-week uplift if applicable.
    """
    action_str = action.value if isinstance(action, ActionType) else str(action)
    if action_str == "do_nothing":
        cause_map = GROUND_TRUTH_RECOVERY_PROB.get(true_root_cause, {})
        return float(cause_map.get("do_nothing", ORGANIC_CURE_BASE_RATE))

    cause_map = GROUND_TRUTH_RECOVERY_PROB.get(true_root_cause)
    if not cause_map:
        return 0.15

    base_p = cause_map.get(action_str, 0.08)

    # Context adjustment: salary week boosts funds-related delayed interventions
    if is_salary_week and true_root_cause in ("insufficient_funds", "cash_flow_timing"):
        if action_str in ("retry_delayed", "mandate_retry_sequence", "voice_call_hinglish"):
            base_p = min(0.92, base_p + 0.12)
        elif action_str == "retry_immediate":
            base_p = min(0.35, base_p + 0.05)

    return float(base_p)


def simulate_outcome(
    case_id: str,
    run_id: str,
    true_root_cause: str,
    action: Union[ActionType, str],
    is_salary_week: bool = False,
) -> bool:
    """
    Determines whether a recovery action succeeds using deterministic seeding:
      seed = int(hashlib.sha256(f"{case_id}:{run_id}".encode()).hexdigest()[:8], 16)
      rng = random.Random(seed)
      recovered = rng.random() < true_p_recovery
    """
    p = get_ground_truth_prob(true_root_cause, action, is_salary_week)
    rng = get_deterministic_rng(case_id, run_id)
    return rng.random() < p


def simulate_recovery_outcome(
    case: RecoveryCase,
    action: Union[ActionType, str],
    run_id: str,
) -> Tuple[bool, float]:
    """
    Simulates outcome against hidden true_root_cause on a RecoveryCase instance.
    Returns (recovered: bool, p_recovery: float).
    """
    p = get_ground_truth_prob(case.true_root_cause, action, case.is_salary_week)
    rng = get_deterministic_rng(case.case_id, run_id)
    recovered = rng.random() < p
    return recovered, p


# ---------------------------------------------------------------------------
# 3. STRATIFIED HOLDOUT CONTROL GROUP ASSIGNMENT
# ---------------------------------------------------------------------------

def get_amount_bucket(amount: float) -> str:
    """Classifies case into amount bucket: low, medium, high."""
    if amount < 2000.0:
        return "low"
    elif amount <= 10000.0:
        return "medium"
    else:
        return "high"


def assign_stratified_holdout(
    cases: List[RecoveryCase],
    holdout_ratio: float = HOLDOUT_RATIO,
    seed: int = DEFAULT_SEED,
) -> List[RecoveryCase]:
    """
    Assigns is_control_group = True for exactly ~holdout_ratio (15-20%) of cases,
    stratified across leak_type and amount_bucket.
    Modifies cases in-place and returns the list.
    """
    rng = random.Random(seed)

    # Group case indices by stratum (leak_type, amount_bucket)
    strata: Dict[Tuple[str, str], List[int]] = {}
    for idx, case in enumerate(cases):
        leak_val = case.leak_type.value if isinstance(case.leak_type, LeakType) else str(case.leak_type)
        bucket = get_amount_bucket(case.amount)
        key = (leak_val, bucket)
        strata.setdefault(key, []).append(idx)

    # Reset all control group flags first
    for case in cases:
        case.is_control_group = False

    # Within each stratum, randomly pick holdout_ratio of cases
    for stratum_indices in strata.values():
        shuffled = list(stratum_indices)
        rng.shuffle(shuffled)
        k = int(round(len(shuffled) * holdout_ratio))
        # Ensure at least 1 holdout if stratum has >= 5 items and holdout_ratio > 0
        if k == 0 and len(shuffled) >= 5 and holdout_ratio > 0:
            k = 1
        for idx in shuffled[:k]:
            cases[idx].is_control_group = True

    return cases


# ---------------------------------------------------------------------------
# 4. STATISTICAL CONFIDENCE INTERVALS (95%)
# ---------------------------------------------------------------------------

@dataclass
class ConfidenceInterval:
    p_hat: float
    lower: float
    upper: float
    margin_of_error: float
    confidence_level: float = 0.95
    is_statistically_significant: bool = False

    def __iter__(self):
        return iter((self.lower, self.upper))

    def __getitem__(self, idx):
        return (self.lower, self.upper)[idx]

    @property
    def ci_low(self) -> float:
        return self.lower

    @property
    def ci_high(self) -> float:
        return self.upper

    def to_dict(self) -> Dict[str, Any]:
        return {
            "p_hat": round(self.p_hat, 4),
            "lower": round(self.lower, 4),
            "upper": round(self.upper, 4),
            "margin_of_error": round(self.margin_of_error, 4),
            "confidence_level": self.confidence_level,
            "is_statistically_significant": self.is_statistically_significant,
        }


def compute_confidence_interval(
    k_or_successes: int,
    n_or_total: int,
    confidence: float = 0.95,
) -> Union[Tuple[float, float], ConfidenceInterval]:
    """
    Computes 95% Confidence Interval for binomial recovery rate using Wilson score interval.
    Returns ConfidenceInterval object (which unpacks as tuple if needed via helper or properties).
    """
    k = k_or_successes
    n = n_or_total
    if n <= 0:
        return ConfidenceInterval(0.0, 0.0, 0.0, 0.0, confidence, False)

    p_hat = k / n
    z = 1.95996  # 95% critical value

    denominator = 1 + (z**2) / n
    center = (p_hat + (z**2) / (2 * n)) / denominator
    spread = (z * math.sqrt((p_hat * (1 - p_hat) / n) + (z**2) / (4 * n**2))) / denominator

    lower = max(0.0, center - spread)
    upper = min(1.0, center + spread)
    margin = (upper - lower) / 2.0

    return ConfidenceInterval(
        p_hat=round(p_hat, 4),
        lower=round(lower, 4),
        upper=round(upper, 4),
        margin_of_error=round(margin, 4),
        confidence_level=confidence,
        is_statistically_significant=(lower > 0.0),
    )


def compute_wilson_ci(k: int, n: int, confidence: float = 0.95) -> Tuple[float, float]:
    """Wilson score interval returning raw (lower, upper) float tuple."""
    ci = compute_confidence_interval(k, n, confidence)
    return ci.lower, ci.upper


# ---------------------------------------------------------------------------
# 5. FINANCIAL METRICS & COMPARATIVE STRATEGY EVALUATION
# ---------------------------------------------------------------------------

@dataclass
class StrategyMetrics:
    name: str = ""
    strategy_name: str = ""
    total_cases: int = 0
    recovered_cases: int = 0
    recovery_rate: float = 0.0
    gross_recovered: float = 0.0
    outreach_cost: float = 0.0
    channel_cost: float = 0.0
    discounts_given: float = 0.0
    discount_cost: float = 0.0
    human_costs: float = 0.0
    human_review_cost: float = 0.0
    total_cost: float = 0.0
    net_recovered: float = 0.0
    roi: float = 0.0
    ci_low: float = 0.0
    ci_high: float = 0.0
    confidence_interval_95: Optional[ConfidenceInterval] = None
    lift_over_control_pct: float = 0.0
    lift_over_naive_pct: float = 0.0

    def __post_init__(self):
        if not self.strategy_name and self.name:
            self.strategy_name = self.name
        elif not self.name and self.strategy_name:
            self.name = self.strategy_name
        if not self.channel_cost and self.outreach_cost:
            self.channel_cost = self.outreach_cost
        elif not self.outreach_cost and self.channel_cost:
            self.outreach_cost = self.channel_cost
        if not self.discount_cost and self.discounts_given:
            self.discount_cost = self.discounts_given
        elif not self.discounts_given and self.discount_cost:
            self.discounts_given = self.discount_cost
        if not self.human_review_cost and self.human_costs:
            self.human_review_cost = self.human_costs
        elif not self.human_costs and self.human_review_cost:
            self.human_costs = self.human_review_cost
        if self.confidence_interval_95 is None and (self.ci_low or self.ci_high):
            self.confidence_interval_95 = ConfidenceInterval(
                p_hat=self.recovery_rate, lower=self.ci_low, upper=self.ci_high,
                margin_of_error=(self.ci_high - self.ci_low) / 2.0
            )
        elif self.confidence_interval_95 is not None and not (self.ci_low or self.ci_high):
            self.ci_low = self.confidence_interval_95.lower
            self.ci_high = self.confidence_interval_95.upper
        if self.total_cost == 0.0 and (self.channel_cost or self.discount_cost or self.human_review_cost):
            self.total_cost = self.channel_cost + self.discount_cost + self.human_review_cost

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "name": self.strategy_name,
            "total_cases": self.total_cases,
            "recovered_cases": self.recovered_cases,
            "recovery_rate": round(self.recovery_rate, 4),
            "gross_recovered": round(self.gross_recovered, 2),
            "channel_cost": round(self.channel_cost, 2),
            "outreach_cost": round(self.channel_cost, 2),
            "discount_cost": round(self.discount_cost, 2),
            "discounts_given": round(self.discount_cost, 2),
            "human_review_cost": round(self.human_review_cost, 2),
            "human_costs": round(self.human_review_cost, 2),
            "total_cost": round(self.total_cost, 2),
            "net_recovered": round(self.net_recovered, 2),
            "roi": round(self.roi, 2),
            "lift_over_control_pct": round(self.lift_over_control_pct, 2),
            "lift_over_naive_pct": round(self.lift_over_naive_pct, 2),
            "confidence_interval_95": self.confidence_interval_95.to_dict(),
            "ci_95": [round(self.ci_low, 4), round(self.ci_high, 4)],
        }


def calculate_case_cost(
    action: Union[ActionType, str],
    amount: float,
    recovered: bool,
    discount_rate: float = DEFAULT_DISCOUNT_RATE,
) -> Tuple[float, float, float]:
    """
    Calculates (channel_cost, discount_cost, human_cost) for an action execution.
    """
    action_str = action.value if isinstance(action, ActionType) else str(action)
    
    # 1. Base channel / outreach cost
    channel_cost = CHANNEL_UNIT_COSTS.get(action_str, 2.0)

    # 2. Human escalation cost
    human_cost = HUMAN_REVIEW_FEE if action_str == "human_escalation" else 0.0

    # 3. Discount cost: applied only if recovered
    discount_cost = 0.0
    if action_str == "discount_incentive" and recovered:
        discount_cost = round(amount * discount_rate, 2)

    return channel_cost, discount_cost, human_cost


def evaluate_single_strategy(
    strategy_name: str,
    decisions: List[Dict[str, Any]],
    cases: List[RecoveryCase],
    run_id: str,
) -> StrategyMetrics:
    """Evaluates one strategy across all cases."""
    gross_recovered = 0.0
    channel_cost_total = 0.0
    discount_cost_total = 0.0
    human_cost_total = 0.0
    recovered_count = 0
    total_cases = len(decisions)

    for d in decisions:
        case_id = d["case_id"]
        action = d["action"]
        cause = d["true_root_cause"]
        amount = float(d["amount"])
        is_sal = bool(d.get("is_salary_week", False))

        recovered = simulate_outcome(case_id, run_id, cause, action, is_sal)
        ch_cost, disc_cost, hm_cost = calculate_case_cost(action, amount, recovered)

        channel_cost_total += ch_cost
        discount_cost_total += disc_cost
        human_cost_total += hm_cost

        if recovered:
            recovered_count += 1
            gross_recovered += amount

    total_cost = channel_cost_total + discount_cost_total + human_cost_total
    net_recovered = gross_recovered - total_cost
    recovery_rate = recovered_count / total_cases if total_cases > 0 else 0.0
    roi = (net_recovered - total_cost) / max(total_cost, 1.0) if total_cost > 0 else 0.0

    ci = compute_confidence_interval(recovered_count, total_cases)

    return StrategyMetrics(
        strategy_name=strategy_name,
        total_cases=total_cases,
        recovered_cases=recovered_count,
        recovery_rate=recovery_rate,
        gross_recovered=gross_recovered,
        channel_cost=channel_cost_total,
        discount_cost=discount_cost_total,
        human_review_cost=human_cost_total,
        total_cost=total_cost,
        net_recovered=net_recovered,
        roi=roi,
        confidence_interval_95=ci,
    )


def compute_comparative_metrics(
    control_metrics: StrategyMetrics,
    naive_metrics: StrategyMetrics,
    agent_metrics: StrategyMetrics,
) -> Tuple[StrategyMetrics, StrategyMetrics, StrategyMetrics]:
    """
    Calculates incremental lift (%) over control and naive baseline.
    Formulas:
        Lift_control = ((Net_Agent - Net_Control) / max(Net_Control, 1)) * 100
        Lift_naive = ((Net_Agent - Net_Naive) / max(Net_Naive, 1)) * 100
    """
    control_metrics.lift_over_control_pct = 0.0
    control_metrics.lift_over_naive_pct = 0.0

    naive_metrics.lift_over_control_pct = (
        (naive_metrics.net_recovered - control_metrics.net_recovered)
        / max(control_metrics.net_recovered, 1.0)
    ) * 100.0

    agent_metrics.lift_over_control_pct = (
        (agent_metrics.net_recovered - control_metrics.net_recovered)
        / max(control_metrics.net_recovered, 1.0)
    ) * 100.0

    agent_metrics.lift_over_naive_pct = (
        (agent_metrics.net_recovered - naive_metrics.net_recovered)
        / max(naive_metrics.net_recovered, 1.0)
    ) * 100.0

    return control_metrics, naive_metrics, agent_metrics
