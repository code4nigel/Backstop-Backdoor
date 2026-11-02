"""
Unit and Integration Tests for Agent 3: Safe Execution & Recovery Workflows.
Verifies:
1. Outbox state machine transitions: PENDING -> SENT -> CONFIRMED.
2. Crash recovery & idempotency without duplicate API calls or double charges.
3. Promise-to-Pay (PTP) tracker, outreach freeze, reminder scheduling, and broken promise escalation.
4. Immediate dispute outreach freeze & human reconciliation task creation.
5. Automatic WhatsApp-to-SMS failover fallback with full audit trail.
6. Omnichannel escalation ladder, mandate retry sequencer, and salary-cycle timing.
7. Cross-channel duplicate contact suppression (12h rule).
"""
import sys
import os
from datetime import datetime, date, timedelta
import pytest

# Ensure BACKSTOP--RAZORPAY is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.models import CaseState, ActionType, LeakType, RecoveryCase
from engine.execution.outbox import (
    TransactionalOutbox,
    OutboxRecord,
    OutboxState,
    generate_idempotency_key,
)
from engine.execution.providers import (
    MockRazorpayGateway,
    MockWhatsAppProvider,
    MockSMSProvider,
    MockEmailProvider,
    OmnichannelDispatcher,
    ProviderResponse,
)
from engine.execution.scheduler import (
    WorkflowScheduler,
    calculate_salary_retry_time,
    should_suppress_contact,
)
from engine.execution.ptp import (
    PromiseToPayTracker,
    PTPState,
)
from engine.execution.dispute import (
    DisputeManager,
    DisputeType,
    DisputeStatus,
)


@pytest.fixture
def mock_gateway():
    return MockRazorpayGateway()


@pytest.fixture
def mock_whatsapp():
    return MockWhatsAppProvider()


@pytest.fixture
def mock_sms():
    return MockSMSProvider()


@pytest.fixture
def mock_dispatcher(mock_gateway, mock_whatsapp, mock_sms):
    return OmnichannelDispatcher(
        razorpay=mock_gateway,
        whatsapp=mock_whatsapp,
        sms=mock_sms,
    )


@pytest.fixture
def outbox(mock_dispatcher):
    return TransactionalOutbox(db_path=":memory:", dispatcher=mock_dispatcher)


@pytest.fixture
def ptp_tracker():
    return PromiseToPayTracker(db_path=":memory:")


@pytest.fixture
def dispute_mgr(outbox):
    return DisputeManager(db_path=":memory:", outbox=outbox)


@pytest.fixture
def sample_case():
    return RecoveryCase(
        case_id="case_101",
        customer_id="cust_888",
        customer_name="Aarav Sharma",
        phone_masked="+91 98••• ••210",
        email_masked="a•••••a@example.com",
        leak_type=LeakType.FAILED_PAYMENT,
        amount=4500.0,
        days_at_risk=3,
        created_at=datetime(2026, 11, 2, 10, 0, 0).timestamp(),
        state=CaseState.READY,
    )


# ==============================================================================
# 1. TRANSACTIONAL OUTBOX STATE MACHINE & IDEMPOTENCY TESTS
# ==============================================================================

def test_outbox_transitions_pending_sent_confirmed(outbox, mock_dispatcher):
    """Verifies that an outbox record transitions cleanly: PENDING -> SENT -> CONFIRMED."""
    run_id = "test_run_01"
    customer_id = "cust_001"
    case_id = "case_001"
    channel = "whatsapp"
    action = "whatsapp_nudge"
    payload = {"phone": "+919876543210", "message": "Please pay ₹1,500"}

    # 1. Enqueue -> State must be PENDING
    record = outbox.enqueue(
        run_id=run_id,
        customer_id=customer_id,
        case_id=case_id,
        attempt_number=1,
        channel=channel,
        action_type=action,
        payload=payload,
    )
    assert record is not None
    assert record.state == OutboxState.PENDING
    assert record.provider_ref is None

    # 2. Dispatch -> Should transition to SENT then CONFIRMED
    dispatched = outbox.dispatch_record(record.idempotency_key, dispatcher=mock_dispatcher)
    assert dispatched.state == OutboxState.CONFIRMED
    assert dispatched.provider_ref is not None
    assert dispatched.provider_ref.startswith("wamid_")

    # 3. Check durable audit trail
    audit = outbox.get_audit_trail(record.idempotency_key)
    states = [a["to_state"] for a in audit]
    assert OutboxState.PENDING.value in states
    assert OutboxState.SENT.value in states
    assert OutboxState.CONFIRMED.value in states


def test_outbox_idempotency_prevents_duplicate_enqueue(outbox):
    """Verifies that enqueuing the exact same action twice returns the same record without duplicating."""
    run_id = "run_idem_01"
    customer_id = "cust_002"
    case_id = "case_002"

    rec1 = outbox.enqueue(run_id, customer_id, case_id, 1, "sms", "reminder_sms", {"msg": "Hello"})
    rec2 = outbox.enqueue(run_id, customer_id, case_id, 1, "sms", "reminder_sms", {"msg": "Hello"})

    assert rec1.idempotency_key == rec2.idempotency_key
    assert rec1.created_at == rec2.created_at

    all_records = outbox.list_records(run_id=run_id)
    assert len(all_records) == 1


def test_payment_gateway_double_charge_prevention(mock_gateway):
    """Verifies that duplicate capture calls with the same idempotency key never charge twice."""
    key = "idem_key_pay_999999"
    resp1 = mock_gateway.capture_payment(key, amount=5000.0, customer_id="cust_99")
    assert resp1.success is True
    first_payment_id = resp1.reference_id

    # Second call with identical idempotency key
    resp2 = mock_gateway.capture_payment(key, amount=5000.0, customer_id="cust_99")
    assert resp2.success is True
    assert resp2.reference_id == first_payment_id  # Returns identical receipt
    assert mock_gateway.call_count == 2
    # Only 1 unique transaction exists in processed map
    assert len(mock_gateway._processed_transactions) == 1


# ==============================================================================
# 2. CRASH RECOVERY TESTS
# ==============================================================================

def test_crash_recovery_already_executed_by_provider(outbox, mock_dispatcher):
    """
    Simulates process crashing AFTER provider acknowledged execution,
    before the outbox recorded CONFIRMED.
    Crash recovery must detect that provider executed it and mark CONFIRMED
    without making another API call.
    """
    run_id = "crash_run_1"
    record = outbox.enqueue(run_id, "cust_c1", "case_c1", 1, "whatsapp", "whatsapp_nudge", {"phone": "+919999911111"})

    # Transition to SENT (dispatched into the network)
    outbox.mark_state(record.idempotency_key, OutboxState.SENT)

    # Provider actually received and processed it:
    prov_resp = mock_dispatcher.whatsapp.send_message(
        idempotency_key=record.idempotency_key,
        to_phone="+919999911111",
        message_text="Simulated message before crash",
    )
    initial_call_count = mock_dispatcher.whatsapp.call_count

    # Process crashes here! Outbox still has state SENT.
    # On reboot, recover_inflight_actions is called:
    recovered = outbox.recover_inflight_actions(run_id, dispatcher=mock_dispatcher)
    assert len(recovered) == 1
    assert recovered[0].idempotency_key == record.idempotency_key
    assert recovered[0].state == OutboxState.CONFIRMED
    assert recovered[0].provider_ref == prov_resp.reference_id

    # Crucial guarantee: Provider was NOT called a second time!
    assert mock_dispatcher.whatsapp.call_count == initial_call_count


def test_crash_recovery_unexecuted_requeues_to_pending(outbox, mock_dispatcher):
    """
    Simulates process crashing BEFORE provider received the call (or network dropout).
    Crash recovery must reset the in-flight state to PENDING so worker can dispatch it cleanly.
    """
    run_id = "crash_run_2"
    record = outbox.enqueue(run_id, "cust_c2", "case_c2", 1, "sms", "reminder_sms", {"phone": "+919999922222"})

    # State left in RECONCILING due to network dropout mid-batch
    outbox.mark_state(record.idempotency_key, OutboxState.RECONCILING)

    # Provider has NO record of this idempotency key
    assert mock_dispatcher.query_status(record.idempotency_key) is None

    # Crash recovery should reset it back to PENDING safely
    recovered = outbox.recover_inflight_actions(run_id, dispatcher=mock_dispatcher)
    assert len(recovered) == 1
    assert recovered[0].state == OutboxState.PENDING

    # Now dispatching it works normally
    dispatched = outbox.dispatch_record(record.idempotency_key, dispatcher=mock_dispatcher)
    assert dispatched.state == OutboxState.CONFIRMED


# ==============================================================================
# 3. PROMISE-TO-PAY (PTP) LIFECYCLE TESTS
# ==============================================================================

def test_ptp_registration_freezes_outreach_and_schedules_reminder(ptp_tracker, sample_case):
    """
    Registering a promise:
    - Updates case state to CaseState.PROMISED.
    - Freezes outreach until 1 day prior to promise date.
    - Schedules reminder for 9:00 AM on the promised date.
    """
    promise_date = date(2026, 11, 10)
    promised_amount = 4500.0

    promise = ptp_tracker.register_promise(
        case_id=sample_case.case_id,
        customer_id=sample_case.customer_id,
        promise_date=promise_date,
        promised_amount=promised_amount,
        case=sample_case,
        notes="Customer promised: Main 10 tareekh ko pay kar dunga",
    )

    # 1. Case state must be PROMISED
    assert sample_case.state == CaseState.PROMISED
    assert promise.state == PTPState.ACTIVE
    assert promise.reminder_scheduled_at == "2026-11-10 09:00:00"

    # 2. Check outreach freeze
    # On Nov 5 (5 days prior): outreach MUST be frozen
    assert ptp_tracker.is_outreach_frozen(sample_case.case_id, current_date=date(2026, 11, 5)) is True
    # On Nov 8 (2 days prior): outreach MUST be frozen
    assert ptp_tracker.is_outreach_frozen(sample_case.case_id, current_date=date(2026, 11, 8)) is True
    # On Nov 9 (1 day prior): freeze lifts so reminder can prep/send
    assert ptp_tracker.is_outreach_frozen(sample_case.case_id, current_date=date(2026, 11, 9)) is False
    # On Nov 10 (promised date): freeze is lifted
    assert ptp_tracker.is_outreach_frozen(sample_case.case_id, current_date=date(2026, 11, 10)) is False


def test_ptp_evaluation_payment_kept(ptp_tracker, sample_case):
    """When customer pays >= promised amount, mark PTP KEPT and transition case to RECOVERED."""
    promise_date = date(2026, 11, 10)
    ptp_tracker.register_promise(
        case_id=sample_case.case_id,
        customer_id=sample_case.customer_id,
        promise_date=promise_date,
        promised_amount=4500.0,
        case=sample_case,
    )

    result = ptp_tracker.evaluate_promise(
        case_id=sample_case.case_id,
        current_date=date(2026, 11, 10),
        payments_ledger={"case_101": 4500.0},
        case=sample_case,
    )

    assert result.state == PTPState.KEPT
    assert sample_case.state == CaseState.RECOVERED
    assert result.recommended_action == ActionType.DO_NOTHING


def test_ptp_broken_promise_escalation_ladder(ptp_tracker, sample_case):
    """
    1st broken promise: re-queues with firmer reminder.
    >= 2 broken promises: flags habitual promise-breaker and escalates to human debt manager.
    """
    promise_date = date(2026, 11, 5)

    # --- 1st Promise ---
    ptp_tracker.register_promise(
        case_id=sample_case.case_id,
        customer_id=sample_case.customer_id,
        promise_date=promise_date,
        promised_amount=4500.0,
        case=sample_case,
    )

    # Day is Nov 6 (promise date passed without payment)
    eval1 = ptp_tracker.evaluate_promise(
        case_id=sample_case.case_id,
        current_date=date(2026, 11, 6),
        payments_ledger=0.0,
        case=sample_case,
    )
    assert eval1.state == PTPState.BROKEN
    assert eval1.is_habitual_breaker is False
    assert eval1.recommended_action == ActionType.REMINDER_SMS
    assert eval1.policy_action == "requeue_firmer_reminder"

    # --- 2nd Promise by same customer ---
    promise_date_2 = date(2026, 11, 12)
    ptp_tracker.register_promise(
        case_id=sample_case.case_id,
        customer_id=sample_case.customer_id,
        promise_date=promise_date_2,
        promised_amount=4500.0,
        case=sample_case,
    )

    # Nov 13: 2nd promise broken!
    eval2 = ptp_tracker.evaluate_promise(
        case_id=sample_case.case_id,
        current_date=date(2026, 11, 13),
        payments_ledger=0.0,
        case=sample_case,
    )
    assert eval2.state == PTPState.BROKEN
    assert eval2.is_habitual_breaker is True
    assert eval2.recommended_action == ActionType.HUMAN_ESCALATION
    assert eval2.policy_action == "escalate_human_debt_manager"


# ==============================================================================
# 4. DISPUTE RECONCILIATION & IMMEDIATE OUTREACH FREEZE TESTS
# ==============================================================================

def test_dispute_trigger_freezes_outreach_and_aborts_outbox(dispute_mgr, outbox, sample_case):
    """
    When customer disputes payment:
    1. Case transitions immediately to CaseState.DISPUTED.
    2. Outbox aborts any pending or scheduled messages for that case.
    3. Generates a structured human reconciliation task.
    """
    # Enqueue a scheduled WhatsApp and SMS for this case
    rec1 = outbox.enqueue("run_disp", sample_case.customer_id, sample_case.case_id, 1, "whatsapp", "whatsapp_nudge", {"text": "msg1"})
    rec2 = outbox.enqueue("run_disp", sample_case.customer_id, sample_case.case_id, 2, "sms", "reminder_sms", {"text": "msg2"})
    assert rec1.state == OutboxState.PENDING
    assert rec2.state == OutboxState.PENDING

    # Customer states: "Maine already pay kar diya hai"
    task = dispute_mgr.raise_dispute(
        case_id=sample_case.case_id,
        customer_id=sample_case.customer_id,
        dispute_type=DisputeType.ALREADY_PAID,
        evidence_notes="Customer provided UTR: UPI/334281983021",
        case=sample_case,
        outbox=outbox,
    )

    # 1. Case State is DISPUTED
    assert sample_case.state == CaseState.DISPUTED

    # 2. Outbox pending messages are ABORTED
    updated_rec1 = outbox.get_record(rec1.idempotency_key)
    updated_rec2 = outbox.get_record(rec2.idempotency_key)
    assert updated_rec1.state == OutboxState.ABORTED
    assert updated_rec2.state == OutboxState.ABORTED
    assert "Dispute raised" in updated_rec1.error_message

    # 3. Structured task generated for human reconciliation
    assert task is not None
    assert task.dispute_type == DisputeType.ALREADY_PAID
    assert task.status == DisputeStatus.PENDING_RECONCILIATION
    assert task.case_id == sample_case.case_id

    pending_tasks = dispute_mgr.get_pending_tasks()
    assert len(pending_tasks) == 1
    assert pending_tasks[0].task_id == task.task_id


def test_dispute_resolution_workflow(dispute_mgr, sample_case):
    """Verifies that resolving a dispute updates the task and case state."""
    task = dispute_mgr.raise_dispute(
        case_id=sample_case.case_id,
        customer_id=sample_case.customer_id,
        dispute_type=DisputeType.AMOUNT_DISPUTE,
        evidence_notes="Invoice includes incorrect GST calculation",
        case=sample_case,
    )

    # Human operator reviews and resolves it
    resolved_task = dispute_mgr.resolve_dispute(
        task_id=task.task_id,
        resolution_status=DisputeStatus.RESOLVED,
        resolution_notes="Credit note issued for GST variance. Paid.",
        case=sample_case,
    )

    assert resolved_task.status == DisputeStatus.RESOLVED
    assert sample_case.state == CaseState.RECOVERED
    assert len(dispute_mgr.get_pending_tasks()) == 0


# ==============================================================================
# 5. WHATSAPP FAILURE TO SMS FALLBACK TESTS
# ==============================================================================

def test_whatsapp_failure_automatic_fallback_to_sms(mock_whatsapp, mock_sms, mock_gateway):
    """
    Simulates WhatsApp delivery failure (network error or unroutable number).
    Verifies that OmnichannelDispatcher automatically falls back to SMS within the same transaction.
    """
    dispatcher = OmnichannelDispatcher(
        razorpay=mock_gateway,
        whatsapp=mock_whatsapp,
        sms=mock_sms,
    )

    # Simulate WhatsApp API network failure
    mock_whatsapp.simulate_network_failure = True

    key = "idemp_fb_001"
    payload = {
        "phone": "+919876543210",
        "message": "Payment reminder for Invoice #INV-1092",
    }

    resp = dispatcher.dispatch(
        idempotency_key=key,
        channel="whatsapp",
        payload=payload,
        fallback_enabled=True,
    )

    # Fallback to SMS must succeed
    assert resp.success is True
    assert resp.channel == "sms"
    assert resp.fallback_occurred is True
    assert resp.fallback_channel == "sms"
    assert resp.reference_id.startswith("sms_")
    assert any("WhatsApp attempt failed" in note for note in resp.audit_notes)
    assert any("Automatic fallback to SMS succeeded" in note for note in resp.audit_notes)

    # Provider call counts: WhatsApp was tried 1x, SMS was called 1x
    assert mock_whatsapp.call_count == 1
    assert mock_sms.call_count == 1


def test_whatsapp_invalid_number_fallback_in_outbox(outbox, mock_whatsapp, mock_sms):
    """Verifies that an outbox dispatch gracefully completes via SMS fallback when WhatsApp fails."""
    mock_whatsapp.simulate_invalid_number = True

    record = outbox.enqueue(
        run_id="run_fb",
        customer_id="cust_fb",
        case_id="case_fb",
        attempt_number=1,
        channel="whatsapp",
        action_type="whatsapp_nudge",
        payload={"phone": "+919876500000", "message": "Pay link"},
    )

    dispatched = outbox.dispatch_record(record.idempotency_key)
    assert dispatched.state == OutboxState.CONFIRMED
    assert dispatched.provider_ref.startswith("sms_")
    assert dispatched.metadata.get("fallback_occurred") is True


# ==============================================================================
# 6. SCHEDULER: SALARY TIMING, MANDATES & OMNICHANNEL LADDER
# ==============================================================================

def test_salary_cycle_timing_scheduling():
    """
    If failure cause is insufficient_funds and today is not salary week,
    schedules retry for 1st of upcoming month at 10:00 AM local time.
    """
    scheduler = WorkflowScheduler()

    # Case 1: Mid-month (Nov 15) outside salary week -> Target Dec 1, 10:00 AM
    mid_month_date = datetime(2026, 11, 15, 14, 30, 0)
    retry_time = calculate_salary_retry_time(mid_month_date, is_salary_week=False)
    assert retry_time.year == 2026
    assert retry_time.month == 12
    assert retry_time.day == 1
    assert retry_time.hour == 10
    assert retry_time.minute == 0

    # Case 2: December mid-month -> Target Jan 1 of next year, 10:00 AM
    dec_date = datetime(2026, 12, 18, 11, 0, 0)
    retry_time_dec = calculate_salary_retry_time(dec_date, is_salary_week=False)
    assert retry_time_dec.year == 2027
    assert retry_time_dec.month == 1
    assert retry_time_dec.day == 1
    assert retry_time_dec.hour == 10

    # Case 3: Already salary week (Nov 1) -> 24h retry
    salary_week_date = datetime(2026, 11, 1, 9, 0, 0)
    retry_time_sw = calculate_salary_retry_time(salary_week_date, is_salary_week=True)
    assert retry_time_sw.day == 2
    assert retry_time_sw.hour == 10


def test_mandate_retry_sequencer():
    """
    Mandate Retry Sequencer:
    Day 0: Failure occurs.
    Day +1: Pre-debit alert via WhatsApp.
    Day +2 (24 hrs later): Execute automated mandate retry.
    Day +4: Final retry before subscription pause.
    """
    scheduler = WorkflowScheduler()
    t0 = datetime(2026, 11, 2, 10, 0, 0)

    touches = scheduler.generate_mandate_sequence(
        case_id="case_mnd_01",
        customer_id="cust_mnd_01",
        failure_time=t0,
        amount=999.0,
    )

    assert len(touches) == 3

    # Touch 1 (Day +1): Pre-debit WhatsApp alert
    assert touches[0].touch_number == 1
    assert touches[0].channel == "whatsapp"
    assert touches[0].action_type == ActionType.WHATSAPP_NUDGE
    assert touches[0].scheduled_time == t0 + timedelta(days=1)
    assert touches[0].delay_hours == 24.0

    # Touch 2 (Day +2): Automated mandate retry
    assert touches[1].touch_number == 2
    assert touches[1].channel == "payment_gateway"
    assert touches[1].action_type == ActionType.MANDATE_RETRY_SEQUENCE
    assert touches[1].scheduled_time == t0 + timedelta(days=2)
    assert touches[1].delay_hours == 48.0

    # Touch 3 (Day +4): Final retry before pause
    assert touches[2].touch_number == 3
    assert touches[2].channel == "payment_gateway"
    assert touches[2].action_type == ActionType.MANDATE_RETRY_SEQUENCE
    assert touches[2].scheduled_time == t0 + timedelta(days=4)
    assert touches[2].delay_hours == 96.0


def test_omnichannel_escalation_ladder():
    """
    Omnichannel Escalation Ladder:
    - Touch 1: Friendly WhatsApp nudge with secure paylink.
    - Touch 2 (48 hrs later if unpaid): SMS reminder.
    - Touch 3 (96 hrs later): Interactive Hinglish voice call.
    - Touch 4 (7 days later): Escalated to human accounts manager.
    """
    scheduler = WorkflowScheduler()
    t0 = datetime(2026, 11, 2, 10, 0, 0)

    ladder = scheduler.generate_omnichannel_ladder("case_ladder", "cust_ladder", start_time=t0, amount=2500.0)
    assert len(ladder) == 4

    # Touch 1: WhatsApp
    assert ladder[0].channel == "whatsapp"
    assert ladder[0].action_type == ActionType.WHATSAPP_NUDGE
    assert ladder[0].delay_hours == 0.0

    # Touch 2: SMS at +48h
    assert ladder[1].channel == "sms"
    assert ladder[1].action_type == ActionType.REMINDER_SMS
    assert ladder[1].delay_hours == 48.0

    # Touch 3: Voice Call at +96h
    assert ladder[2].channel == "voice"
    assert ladder[2].action_type == ActionType.VOICE_CALL_HINGLISH
    assert ladder[2].delay_hours == 96.0

    # Touch 4: Human Escalation at +168h (7 days)
    assert ladder[3].channel == "human"
    assert ladder[3].action_type == ActionType.HUMAN_ESCALATION
    assert ladder[3].delay_hours == 168.0


# ==============================================================================
# 7. CROSS-CHANNEL DUPLICATE CONTACT SUPPRESSION (12H RULE)
# ==============================================================================

def test_cross_channel_duplicate_contact_suppression():
    """
    Inspects customer contact history across all channels (SMS, WhatsApp, Voice, Email).
    Rule: If any customer-facing contact occurred within the last 12 hours,
    suppress new outbound messages across all channels to prevent spamming.
    """
    scheduler = WorkflowScheduler(cooldown_hours=12.0)
    current_time = datetime(2026, 11, 2, 18, 0, 0)

    # 1. Customer contacted via WhatsApp 4 hours ago -> Must suppress
    contact_history = [
        {
            "customer_id": "cust_anti_spam",
            "channel": "whatsapp",
            "timestamp": datetime(2026, 11, 2, 14, 0, 0),  # 4h ago
        }
    ]
    suppressed, reason = scheduler.check_suppression("cust_anti_spam", current_time, contact_history)
    assert suppressed is True
    assert "whatsapp 4.0h ago" in reason

    # 2. Customer contacted via SMS 15 hours ago (> 12h cooldown) -> Do NOT suppress
    old_contact_history = [
        {
            "customer_id": "cust_anti_spam",
            "channel": "sms",
            "timestamp": datetime(2026, 11, 2, 2, 0, 0),  # 16h ago
        }
    ]
    suppressed, reason = scheduler.check_suppression("cust_anti_spam", current_time, old_contact_history)
    assert suppressed is False
    assert reason == ""

    # 3. Silent backend retry (payment_gateway) does NOT suppress customer outreach
    backend_history = [
        {
            "customer_id": "cust_anti_spam",
            "channel": "payment_gateway",
            "timestamp": datetime(2026, 11, 2, 16, 0, 0),  # 2h ago
        }
    ]
    suppressed, reason = scheduler.check_suppression("cust_anti_spam", current_time, backend_history)
    assert suppressed is False
