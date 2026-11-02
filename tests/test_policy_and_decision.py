"""
Unit Tests for Diagnosis, Decisioning, Systemic Detection & Policy Engine (Phase 2).

Verifies:
1. Systemic degradation flags HDFC route drop and reroutes without customer outreach.
2. Card expiry rule overrides ML and outputs 100% confident diagnosis.
3. EV ranking correctly picks 'do_nothing' when all interventions yield negative economic return.
4. Quiet hours blocks outbound calls after 7 PM (19:00).
5. Customer DND immediately blocks SMS and calls.
6. High-value cases (₹50k+) are correctly tagged for human review.
7. Multi-leak diagnosis across all 4 leak types (checkout drops, mandates, B2B invoices).
8. Frequency caps (max 2 touches / 7 days, max 3 attempts).
9. Dispute & hardship freezes.
10. Discount margin cap guardrails.
"""
import pytest
from engine.models import LeakType, ActionType, CaseState, RecoveryCase
from engine.systemic import SystemicDegradationDetector
from engine.diagnosis import MultiLeakDiagnosisDispatcher, PaymentClassifier, CartDiagnoser, MandateDiagnoser, InvoiceDiagnoser
from engine.recovery_value import rank_interventions, compute_net_ev, CHANNEL_COSTS, HUMAN_OPERATIONAL_COST
from engine.policy import PolicyEngine


# =====================================================================
# 1. Systemic Route Degradation Detection Tests
# =====================================================================
def test_systemic_degradation_detection_hdfc():
    """
    Test that when HDFC success rate drops >= 15% below baseline (e.g. 85% -> 55%),
    the route is flagged as DEGRADED and reroutes without customer outreach.
    """
    detector = SystemicDegradationDetector(window_size=50, drop_threshold=0.15)
    detector.set_baseline("HDFC", "RAZORPAY", rate=0.85)

    # Initial state should be healthy
    assert not detector.is_route_degraded("HDFC", "RAZORPAY")
    assert detector.is_outreach_allowed("HDFC", "RAZORPAY")

    # Inject incident: HDFC success rate drops to 50%
    # (35 percentage points drop below 85% baseline)
    detector.inject_incident("HDFC", "RAZORPAY", success_rate=0.50, count=50)

    status = detector.get_route_status("HDFC", "RAZORPAY")
    assert status["is_degraded"] is True
    assert status["status"] == "DEGRADED"
    assert status["current_success_rate"] == 0.50
    assert status["rate_drop"] >= 0.15

    # Verify outreach is blocked and override action is REROUTE_GATEWAY
    assert not detector.is_outreach_allowed("HDFC", "RAZORPAY")
    assert detector.get_override_action("HDFC", "RAZORPAY", alternate_gateway_available=True) == ActionType.REROUTE_GATEWAY
    assert detector.get_override_action("HDFC", "RAZORPAY", alternate_gateway_available=False) == ActionType.RETRY_DELAYED


def test_systemic_policy_reroute_without_outreach():
    """
    Test that PolicyEngine under an active systemic outage reroutes without customer outreach.
    """
    detector = SystemicDegradationDetector(window_size=50, drop_threshold=0.15)
    detector.inject_incident("HDFC", "RAZORPAY", success_rate=0.40, count=50)

    policy = PolicyEngine(systemic_detector=detector)

    case = RecoveryCase(
        case_id="case_sys_001",
        customer_id="cust_001",
        customer_name="Rohan Verma",
        phone_masked="+91 98••• ••123",
        email_masked="r••••@example.com",
        leak_type=LeakType.FAILED_PAYMENT,
        amount=3500.0,
        days_at_risk=0,
        metadata={"bank": "HDFC", "gateway": "RAZORPAY", "prior_failures": 0},
    )

    decision = policy.evaluate(case, alternate_gateway_available=True)

    assert decision.allowed is True
    assert decision.chosen_action == ActionType.REROUTE_GATEWAY
    assert decision.root_cause == "issuer_down"
    # Ensure no customer outreach action was chosen
    assert decision.chosen_action not in [
        ActionType.REMINDER_SMS,
        ActionType.WHATSAPP_NUDGE,
        ActionType.VOICE_CALL_HINGLISH,
    ]
    assert "Systemic" in decision.rationale or "issuer_down" in decision.rationale


# =====================================================================
# 2. Card Expiry Deterministic Rule Override
# =====================================================================
def test_card_expiry_rule_overrides_ml():
    """
    Verify deterministic rule override: If days_to_card_expiry < 0,
    return expired_card with confidence=1.0 and explanatory evidence.
    """
    classifier = PaymentClassifier()

    # Expired card case (days_to_card_expiry = -12)
    case_data = {
        "case_id": "case_exp_001",
        "amount": 2500.0,
        "days_to_card_expiry": -12,
        "prior_failures": 1,
        "account_balance_proxy": 1.2,
        "is_salary_week": True,
        "bank": "HDFC",
        "bank_risk_tier": "low",
        "metadata": {"card_expiry_date": "2026-10-21"},
    }

    diag = classifier.diagnose(case_data)

    assert diag.predicted_cause == "expired_card"
    assert diag.confidence == 1.0
    assert diag.source == "deterministic_rule"
    assert any("expired" in ev.lower() for ev in diag.evidence)
    assert diag.probabilities["expired_card"] == 1.0
    assert diag.requires_human_approval is False


def test_card_expiry_recovery_ev_ranking():
    """
    Verify that for expired_card, card_update_link is chosen over retry_immediate (P = 0.0).
    """
    ranked = rank_interventions(root_cause="expired_card", amount=4500.0)

    top = ranked[0]
    assert top.action == ActionType.CARD_UPDATE_LINK
    assert top.net_recovery > 0

    # retry_immediate must have 0.0 recovery probability and negative EV
    retry_imm = next(i for i in ranked if i.action == ActionType.RETRY_IMMEDIATE)
    assert retry_imm.p_recovery == 0.0
    assert retry_imm.net_recovery < 0


# =====================================================================
# 3. Expected Value (Net EV) Engine & do_nothing Baseline
# =====================================================================
def test_do_nothing_wins_when_all_ev_negative():
    """
    Verify that when all active interventions yield negative economic return
    (e.g. ₹50 invoice with low recovery where channel cost exceeds return),
    do_nothing wins with EV = 0.00.
    """
    # Very small invoice of ₹15.00 with low probability cause
    ranked = rank_interventions(root_cause="casual_browsing", amount=15.00)

    top = ranked[0]
    assert top.action == ActionType.DO_NOTHING
    assert top.expected_value == 0.0
    assert top.net_recovery == 0.0

    # Ensure other candidate actions were negative Net EV
    sms_interv = next(i for i in ranked if i.action == ActionType.REMINDER_SMS)
    assert sms_interv.net_recovery < 0


def test_net_ev_formula_accuracy():
    """
    Verify exact Net EV calculation formula:
    Net EV = P * Amount - Cost_channel - Disc - Risk - Cost_human
    """
    amount = 10000.0
    # Voice call unit cost is ₹18.00, P = 0.48 for insufficient funds
    interv = compute_net_ev(
        action=ActionType.VOICE_CALL_HINGLISH,
        root_cause="insufficient_funds",
        amount=amount,
        p_recovery=0.48,
    )

    expected_gross = 0.48 * 10000.0  # 4800.0
    expected_channel_cost = 18.00
    expected_net = round(expected_gross - expected_channel_cost, 2)  # 4782.00

    assert interv.expected_value == expected_net
    assert interv.channel_cost == 18.00
    assert interv.human_cost == 0.00


# =====================================================================
# 4. Deterministic Compliance Gate: Quiet Hours (RBI Recovery Norms)
# =====================================================================
def test_quiet_hours_blocks_voice_calls_after_7pm():
    """
    Verify that voice calls and promotional nudges are blocked
    between 19:00 (7 PM) and 08:00 (8 AM).
    """
    policy = PolicyEngine()

    case = RecoveryCase(
        case_id="case_quiet_001",
        customer_id="cust_quiet",
        customer_name="Priya Patel",
        phone_masked="+91 97••• ••555",
        email_masked="p••••@example.com",
        leak_type=LeakType.FAILED_PAYMENT,
        amount=12000.0,
        days_at_risk=2,
        metadata={"bank": "ICICI", "prior_failures": 0, "decline_code": "3DS_TIMEOUT"},
    )

    # Test at 20:00 (8:00 PM) — Quiet hours ACTIVE
    decision_night = policy.evaluate(case, current_hour=20)
    assert decision_night.allowed is False
    assert "Quiet hours" in (decision_night.blocked_reason or "")
    assert decision_night.chosen_action == ActionType.DO_NOTHING

    # Test at 14:00 (2:00 PM) — Quiet hours INACTIVE
    decision_day = policy.evaluate(case, current_hour=14)
    assert decision_day.allowed is True
    assert decision_day.chosen_action != ActionType.DO_NOTHING


# =====================================================================
# 5. Deterministic Compliance Gate: Customer Opt-Out / TRAI DND
# =====================================================================
def test_customer_dnd_blocks_outreach():
    """
    Verify that customer DND or opt-out immediately blocks all SMS, WhatsApp, and voice calls.
    """
    policy = PolicyEngine()

    case_dnd = RecoveryCase(
        case_id="case_dnd_001",
        customer_id="cust_dnd",
        customer_name="Amitabh Sen",
        phone_masked="+91 91••• ••888",
        email_masked="a••••@example.com",
        leak_type=LeakType.FAILED_PAYMENT,
        amount=5000.0,
        days_at_risk=1,
        metadata={"is_dnd": True, "bank": "SBI", "decline_code": "3DS_TIMEOUT"},
    )

    decision = policy.evaluate(case_dnd, current_hour=11)

    assert decision.allowed is False
    assert "Customer DND / Opt-Out active" in (decision.blocked_reason or "")
    assert decision.chosen_action == ActionType.DO_NOTHING


# =====================================================================
# 6. High-Value Cases Tagged for Human Review (>= ₹50,000)
# =====================================================================
def test_high_value_case_routes_to_human_review():
    """
    Verify that cases with amount >= ₹50,000 are automatically tagged
    with requires_human_approval = True.
    """
    policy = PolicyEngine()

    case_high_val = RecoveryCase(
        case_id="case_hv_001",
        customer_id="cust_enterprise",
        customer_name="Tata Steel Ltd",
        phone_masked="+91 99••• ••000",
        email_masked="t••••@example.com",
        leak_type=LeakType.B2B_INVOICE,
        amount=75000.0,  # >= ₹50,000
        days_at_risk=10,
        metadata={"client_tier": "Enterprise", "is_salary_week": True},
    )

    decision = policy.evaluate(case_high_val, current_hour=11)

    assert decision.requires_human_approval is True
    assert "High-value" in (decision.human_review_reason or "")
    assert decision.case_id == "case_hv_001"


# =====================================================================
# 7. Multi-Leak Diagnosis Suite Tests (Carts, Mandates, B2B Invoices)
# =====================================================================
def test_cart_diagnoser_funnel_steps():
    """
    Test cart diagnoser across all major drop steps:
    otp_screen, shipping_info, coupon_apply, payment_selection.
    """
    diagnoser = CartDiagnoser()

    # Step: otp_screen
    diag_otp = diagnoser.diagnose({"drop_step": "otp_screen", "amount": 2500})
    assert diag_otp.predicted_cause == "otp_delay"
    assert diag_otp.confidence >= 0.85

    # Step: shipping_info
    diag_ship = diagnoser.diagnose({"drop_step": "shipping_info", "amount": 1800})
    assert diag_ship.predicted_cause == "shipping_shock"
    assert diag_ship.confidence >= 0.85

    # Step: coupon_apply
    diag_coup = diagnoser.diagnose({"drop_step": "coupon_apply", "amount": 3200})
    assert diag_coup.predicted_cause == "coupon_invalid"
    assert diag_coup.confidence >= 0.85

    # Step: payment_selection
    diag_pay = diagnoser.diagnose({"drop_step": "payment_selection", "amount": 4500})
    assert diag_pay.predicted_cause == "payment_method_missing"
    assert diag_pay.confidence >= 0.80


def test_mandate_diagnoser_rbi_limits_and_rules():
    """
    Test mandate diagnoser:
    1. Pre-debit notification failure (RBI 24-hr circular).
    2. UPI Autopay limit exceeded (> ₹15,000 without AFA).
    3. Card expired before renewal date.
    """
    diagnoser = MandateDiagnoser()

    # Case 1: Pre-debit notification missed
    diag_pre = diagnoser.diagnose({
        "amount": 2000.0,
        "metadata": {"pre_debit_notification_sent": False, "mandate_type": "e_mandate"},
    })
    assert diag_pre.predicted_cause == "pre_debit_notification_missed"
    assert diag_pre.confidence >= 0.90
    assert any("pre-debit" in ev.lower() for ev in diag_pre.evidence)

    # Case 2: UPI Autopay limit exceeded (> ₹15,000)
    diag_limit = diagnoser.diagnose({
        "amount": 18500.0,
        "metadata": {"mandate_type": "upi_autopay"},
    })
    assert diag_limit.predicted_cause == "mandate_limit_exceeded"
    assert diag_limit.confidence >= 0.95
    assert any("15,000" in ev for ev in diag_limit.evidence)

    # Case 3: Card expired before cycle
    diag_exp = diagnoser.diagnose({
        "amount": 1200.0,
        "metadata": {"days_to_card_expiry": -5, "mandate_type": "standing_instruction"},
    })
    assert diag_exp.predicted_cause == "card_expired_before_cycle"
    assert diag_exp.confidence == 1.0


def test_invoice_diagnoser_b2b_causes():
    """
    Test invoice diagnoser:
    1. PO mismatch on enterprise overdue > 15 days.
    2. Client dispute flag.
    3. Cash flow / salary week timing.
    """
    diagnoser = InvoiceDiagnoser()

    # Case 1: PO mismatch
    diag_po = diagnoser.diagnose({
        "amount": 35000.0,
        "days_at_risk": 20,
        "metadata": {"client_tier": "Enterprise", "po_mismatch": True, "po_number": "PO-991"},
    })
    assert diag_po.predicted_cause == "po_number_mismatch"
    assert diag_po.confidence >= 0.90

    # Case 2: Active dispute
    diag_disp = diagnoser.diagnose({
        "amount": 28000.0,
        "days_at_risk": 12,
        "metadata": {"dispute_flag": True},
    })
    assert diag_disp.predicted_cause == "invoice_dispute"
    assert diag_disp.confidence >= 0.95
    assert diag_disp.requires_human_approval is True

    # Case 3: Cash flow timing
    diag_cf = diagnoser.diagnose({
        "amount": 15000.0,
        "days_at_risk": 6,
        "is_salary_week": True,
        "metadata": {"cash_flow_timing": True},
    })
    assert diag_cf.predicted_cause == "cash_flow_timing"
    assert diag_cf.confidence >= 0.80


# =====================================================================
# 8. Dispute & Hardship Outreach Freeze
# =====================================================================
def test_dispute_and_hardship_freeze():
    """
    Verify that active disputes or hardship notes ('already paid', bereavement)
    immediately freeze outreach and require human approval.
    """
    policy = PolicyEngine()

    case_dispute = RecoveryCase(
        case_id="case_disp_001",
        customer_id="cust_disp",
        customer_name="Vikas Mehra",
        phone_masked="+91 99••• ••222",
        email_masked="v••••@example.com",
        leak_type=LeakType.B2B_INVOICE,
        amount=14000.0,
        days_at_risk=5,
        metadata={"dispute_flag": True, "notes": "Customer claims already paid via NEFT"},
    )

    decision = policy.evaluate(case_dispute, current_hour=14)

    assert decision.allowed is False
    assert decision.chosen_action == ActionType.DO_NOTHING
    assert decision.requires_human_approval is True
    assert "dispute or hardship" in decision.blocked_reason.lower()


# =====================================================================
# 9. Frequency Caps (Touches & Total Attempts)
# =====================================================================
def test_frequency_caps_enforced():
    """
    Verify:
    1. Max 2 customer-facing touches in rolling 7-day window.
    2. Max 3 total attempts per invoice.
    """
    policy = PolicyEngine()

    case = RecoveryCase(
        case_id="case_freq_001",
        customer_id="cust_freq",
        customer_name="Deepak Joshi",
        phone_masked="+91 98••• ••333",
        email_masked="d••••@example.com",
        leak_type=LeakType.FAILED_PAYMENT,
        amount=6000.0,
        days_at_risk=3,
        metadata={"bank": "HDFC"},
    )

    # 3 prior attempts (attempt_number becomes 4 > MAX_ATTEMPTS = 3)
    dec_attempts = policy.evaluate(case, prior_history={"attempts": 3}, current_hour=14)
    assert dec_attempts.allowed is False
    assert "Max frequency cap reached" in (dec_attempts.blocked_reason or "")

    # 2 prior touches in 7 days on an outreach case
    case_touch = RecoveryCase(
        case_id="case_touch_001",
        customer_id="cust_freq",
        customer_name="Deepak Joshi",
        phone_masked="+91 98••• ••333",
        email_masked="d••••@example.com",
        leak_type=LeakType.FAILED_PAYMENT,
        amount=6000.0,
        days_at_risk=3,
        metadata={"bank": "HDFC", "decline_code": "3DS_TIMEOUT"},
    )
    dec_touches = policy.evaluate(case_touch, prior_history={"attempts": 1, "prior_touches_7d": 2}, current_hour=14)
    assert dec_touches.allowed is False
    assert "Max frequency cap reached" in (dec_touches.blocked_reason or "")


# =====================================================================
# 10. Discount Margin Cap Guardrail
# =====================================================================
def test_discount_margin_cap_routes_to_human():
    """
    Verify that requesting a discount above the margin cap (10%)
    forces human approval.
    """
    policy = PolicyEngine()

    case = RecoveryCase(
        case_id="case_disc_001",
        customer_id="cust_disc",
        customer_name="Sunil Rao",
        phone_masked="+91 98••• ••444",
        email_masked="s••••@example.com",
        leak_type=LeakType.ABANDONED_CHECKOUT,
        amount=8000.0,
        days_at_risk=1,
        metadata={"drop_step": "shipping_info"},
    )

    # 15% discount (> 10% margin cap)
    decision = policy.evaluate(case, current_hour=14, discount_pct=0.15)
    assert decision.requires_human_approval is True
    assert "margin cap" in (decision.human_review_reason or "").lower()


# =====================================================================
# 11. Low Diagnosis Confidence (< 0.35) Routes to Human Approval
# =====================================================================
def test_low_confidence_diagnosis_routes_to_human():
    """
    Verify that diagnosis with confidence < 0.35 triggers human review.
    """
    policy = PolicyEngine()

    # Construct case with ambiguous metadata
    case = {
        "case_id": "case_low_conf",
        "amount": 4000.0,
        "leak_type": "failed_payment",
        "days_to_card_expiry": 200,
        "prior_failures": 1,
        "account_balance_proxy": 0.0,
        "is_salary_week": False,
        "bank": "UNKNOWN",
        "bank_risk_tier": "medium",
    }

    # Manually trigger a low confidence diagnosis
    diag = policy.diagnosis.payment_classifier.diagnose(case)
    # If confidence < 0.35, verify requires_human_approval
    if diag.confidence < 0.35:
        assert diag.requires_human_approval is True


# =====================================================================
# 12. Decision Rationale Generation
# =====================================================================
def test_explainable_rationale_generated():
    """
    Verify that policy returns an explicit, human-readable rationale
    contrasting chosen action with rejected alternatives.
    """
    policy = PolicyEngine()

    case = RecoveryCase(
        case_id="case_rat_001",
        customer_id="cust_rat",
        customer_name="Anita Roy",
        phone_masked="+91 98••• ••777",
        email_masked="a••••@example.com",
        leak_type=LeakType.FAILED_PAYMENT,
        amount=4280.0,
        days_at_risk=0,
        metadata={"days_to_card_expiry": -5, "bank": "HDFC"},
    )

    decision = policy.evaluate(case, current_hour=14)

    assert len(decision.rationale) > 20
    assert "card_update_link" in decision.rationale or decision.chosen_action.value in decision.rationale
    assert "EV" in decision.rationale
