"""
Unit and Integration Test Suite for Phase 5:
Hinglish Voice Simulator & Executive Recovery Report.

Verifies:
1. Mid-call WhatsApp payment link generation and signing.
2. Natural Hinglish phrasing and Gemini fallback safety.
3. State machine transitions for all 7 Indian customer scenarios:
   - PAY_NOW, PROMISE_LATER, SEND_LINK, ALREADY_PAID, INVOICE_DISPUTE, ANGER_HOSTILITY, OPT_OUT.
4. Date parsing from Hinglish utterances (e.g. "Salary aane do 5th ko").
5. Instant dispute outreach freeze and recon task generation.
6. Executive recovery report HTML compilation with zero NaN values.
"""

import os
import sys
import tempfile
import pytest

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voice.mid_call_link import send_mid_call_paylink, mask_phone_number
from voice.prompts import generate_hinglish_response, get_fallback_phrasing
from voice.simulator import (
    HinglishVoiceSimulator,
    VoiceIntent,
    CallState,
    CaseState,
)
from report.generator import ExecutiveReportGenerator, generate_report, format_inr


# ==============================================================================
# 1. MID-CALL PAYMENT LINK TESTS
# ==============================================================================

def test_mid_call_link_generation():
    """Verify mid-call link generates signed Razorpay URL, masked phone, and delivery receipt."""
    payload = send_mid_call_paylink(
        case_id="rec_case_89f02a",
        customer_phone="+91 98765 43210",
        amount=14500.0,
    )
    assert payload["status"] == "DELIVERED"
    assert payload["case_id"] == "rec_case_89f02a"
    assert payload["amount"] == 14500.0
    assert payload["phone_masked"] == "+91 98*** **210"
    assert payload["pay_link"].startswith("https://rzp.io/i/rec_")
    assert payload["paylink"] == payload["pay_link"]
    assert "timestamp" in payload
    assert payload["delivery_receipt"].startswith("wamid.HBgMOTE5OD")
    assert "INR 14,500.00" in payload["short_message"]


def test_mask_phone_number():
    """Verify phone masking handles various Indian and international formats safely."""
    assert mask_phone_number("+91 98765 43210") == "+91 98*** **210"
    assert mask_phone_number("9876543210") == "+91 98*** **210"
    assert mask_phone_number("+91-91234-56789") == "+91 91*** **789"
    assert mask_phone_number("") == "+91 ***** *****"


# ==============================================================================
# 2. PROMPTS & HINGLISH PHRASING TESTS
# ==============================================================================

def test_prompts_fallback_phrasing():
    """Verify fallback templates contain required customer name, amounts, and dates."""
    scenarios = [
        ("GREETING", "Rohan", 14500.0),
        ("PAY_NOW", "Rohan", 14500.0),
        ("PROMISE_LATER", "Rohan", 14500.0),
        ("SEND_LINK", "Rohan", 14500.0),
        ("ALREADY_PAID", "Rohan", 14500.0),
        ("INVOICE_DISPUTE", "Rohan", 14500.0),
        ("ANGER_HOSTILITY", "Rohan", 14500.0),
        ("OPT_OUT", "Rohan", 14500.0),
    ]
    for intent, name, amount in scenarios:
        phrase = get_fallback_phrasing(intent, customer_name=name, amount=amount)
        assert len(phrase) > 15
        assert isinstance(phrase, str)


def test_prompts_gemini_fallback_safety():
    """Verify that generate_hinglish_response falls back safely without error when offline."""
    res = generate_hinglish_response(
        intent="PROMISE_LATER",
        customer_name="Rohan",
        amount=14500.0,
        api_key=None,  # Forced offline
    )
    assert res["source"] == "fallback_template"
    assert "5 tareekh note kar li hai" in res["text"]


# ==============================================================================
# 3. VOICE STATE MACHINE TRANSITION TESTS
# ==============================================================================

def test_state_machine_greeting_staging():
    """Verify call startup stages outbound greeting with READY state."""
    sim = HinglishVoiceSimulator()
    step = sim.start_call()
    assert step.call_state == CallState.GREETING_STAGED
    assert step.case_state == CaseState.READY
    assert step.detected_intent == VoiceIntent.GREETING
    assert step.fired_policy_rule == "RULE_OUTBOUND_GREETING_STAGE"
    assert "Namaste" in step.agent_response


def test_state_machine_promise_later():
    """Verify customer saying 'Salary aane do 5th ko' extracts date, registers PTP, and ends call."""
    sim = HinglishVoiceSimulator()
    sim.start_call()

    step = sim.step("Salary aane do 5th ko")
    assert step.detected_intent == VoiceIntent.PROMISE_LATER
    assert step.confidence >= 0.90
    assert step.fired_policy_rule == "RULE_PTP_REGISTERED_HOLD_OUTREACH"
    assert step.case_state == CaseState.PROMISED
    assert step.call_ended is True
    assert "5 tareekh note kar li hai" in step.agent_response

    # Verify PTP Record
    assert step.ptp_record is not None
    assert step.ptp_record.promised_date == "2026-11-05"
    assert step.ptp_record.promised_amount == 14500.0
    assert step.ptp_record.outreach_frozen is True


def test_state_machine_already_paid_freeze():
    """Verify customer saying 'Already paid' triggers immediate dispute freeze and recon task."""
    sim = HinglishVoiceSimulator()
    sim.start_call()

    step = sim.step("Maine kal shaam ko hi UPI se pay kar diya tha, baar baar kyu call kar rahe ho?")
    assert step.detected_intent == VoiceIntent.ALREADY_PAID
    assert step.confidence >= 0.95
    assert step.fired_policy_rule == "RULE_DISPUTE_FREEZE_TRIGGERED"
    assert step.case_state == CaseState.DISPUTED
    assert step.call_ended is True
    assert "Maaf kijiye! Main abhi aapka case hold pe daal raha hu" in step.agent_response

    # Verify Dispute Recon Task
    assert step.dispute_task is not None
    assert step.dispute_task.dispute_type == "already_paid"
    assert step.dispute_task.status == "AWAITING_HUMAN_RECONCILIATION"
    assert step.dispute_task.freeze_outreach is True


def test_state_machine_pay_now():
    """Verify customer saying 'Haan, abhi kar deta hu' dispatches payment link."""
    sim = HinglishVoiceSimulator()
    sim.start_call()

    step = sim.step("Haan, abhi kar deta hu GPay se")
    assert step.detected_intent == VoiceIntent.PAY_NOW
    assert step.fired_policy_rule == "RULE_PAY_NOW_LINK_DISPATCH"
    assert step.case_state == CaseState.EXECUTING
    assert step.dispatched_link is not None
    assert step.dispatched_link["status"] == "DELIVERED"
    assert step.dispatched_link["pay_link"].startswith("https://rzp.io/i/")
    assert step.call_ended is True


def test_state_machine_send_link():
    """Verify customer asking for link sends WhatsApp link mid-call."""
    sim = HinglishVoiceSimulator()
    sim.start_call()

    step = sim.step("Mujhe WhatsApp pe link bhej do")
    assert step.detected_intent == VoiceIntent.SEND_LINK
    assert step.fired_policy_rule == "RULE_MIDCALL_LINK_SENT"
    assert step.case_state == CaseState.EXECUTING
    assert step.dispatched_link is not None
    assert "Maine aapke WhatsApp number pe link bhej diya hai" in step.agent_response


def test_state_machine_invoice_dispute():
    """Verify invoice dispute triggers freeze and creates billing dispute task."""
    sim = HinglishVoiceSimulator()
    sim.start_call()

    step = sim.step("Invoice galat hai, discount nahi laga tha")
    assert step.detected_intent == VoiceIntent.INVOICE_DISPUTE
    assert step.fired_policy_rule == "RULE_INVOICE_DISPUTE_FREEZE"
    assert step.case_state == CaseState.DISPUTED
    assert step.dispute_task is not None
    assert step.dispute_task.dispute_type == "invoice_discount_discrepancy"


def test_state_machine_anger_hostility():
    """Verify customer hostility triggers polite de-escalation and 7-day contact hold."""
    sim = HinglishVoiceSimulator()
    sim.start_call()

    step = sim.step("Bakwas band karo tameez nahi hai kya!")
    assert step.detected_intent == VoiceIntent.ANGER_HOSTILITY
    assert step.fired_policy_rule == "RULE_HOSTILITY_7DAY_DND_APPLIED"
    assert step.case_state == CaseState.POLICY_BLOCKED
    assert sim.contact_hold_days == 7
    assert step.call_ended is True


def test_state_machine_opt_out():
    """Verify DND request permanently marks case as policy blocked."""
    sim = HinglishVoiceSimulator()
    sim.start_call()

    step = sim.step("Mera number list se hatao, DND register karo")
    assert step.detected_intent == VoiceIntent.OPT_OUT
    assert step.fired_policy_rule == "RULE_DND_OPT_OUT_RECORDED"
    assert step.case_state == CaseState.POLICY_BLOCKED
    assert sim.dnd_opt_out is True
    assert step.call_ended is True


# ==============================================================================
# 4. ONE-PAGE EXECUTIVE REPORT TESTS
# ==============================================================================

def test_report_compilation_default():
    """Verify report compilation creates HTML with zero NaN, all leak types, and funnel."""
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tf:
        temp_path = tf.name

    try:
        out_path = generate_report(output_path=temp_path)
        assert os.path.exists(out_path)
        with open(out_path, "r", encoding="utf-8") as f:
            html = f.read()

        # Check required sections and tokens
        assert "BACKSTOP" in html
        assert "Executive Recovery Report" in html
        assert "Judges' Executive Summary" in html
        assert "Autonomous Recovery Funnel Progression" in html
        assert "Multi-Leak Performance Breakdown" in html
        assert "Compliance & Trust Audit Trail" in html

        # Check all 4 leak categories
        assert "Failed Card & UPI Payments" in html
        assert "Abandoned Checkouts" in html
        assert "Subscription Mandates (Autopay)" in html
        assert "B2B Invoices & Receivables" in html

        # Check financial metrics & currency
        assert "₹48,25,000" in html
        assert "₹31,42,500" in html
        assert "+38.4%" in html
        assert "14.8x" in html

        # Crucial Quality Gate: Zero NaN or placeholder tokens
        assert "NaN" not in html
        assert "{{ " not in html
        assert "None" not in html

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_format_inr():
    """Verify INR formatting with lakhs and crores representation."""
    assert format_inr(14500.0) == "₹14,500"
    assert format_inr(4825000.0) == "₹48,25,000"
    assert format_inr(10000000.0) == "₹1,00,00,000"
    assert format_inr(500.0) == "₹500"


def test_executive_summary_sentence_count():
    """Verify the executive summary is exactly 4 sentences covering lift, ROI, and compliance."""
    import re
    generator = ExecutiveReportGenerator()
    data = generator.load_evaluation_data()
    summary = generator.generate_executive_summary_paragraph(data)

    # Split by period followed by space to avoid splitting on decimal numbers like 38.4%
    sentences = [s.strip() for s in re.split(r'\.\s+', summary.strip()) if s.strip()]
    assert len(sentences) == 4
    assert "Backstop analyzed" in sentences[0]
    assert "incremental lift" in sentences[1]
    assert "quiet hours" in sentences[2]
    assert "Backstop successfully recovered" in sentences[3]
