"""
Comprehensive Verification Suite for Backstop Foundation, Contracts, Safety & Reliability.

Covers 100% of functional requirements:
- Canonical domain model instantiation and serialization across all 4 LeakType cases
- CaseState lifecycle validation, forced transitions, and illegal transition guards
- Deterministic PII masking utilities (phone, email, name, card, GSTIN, dict, free-form text, audit scanner)
- Strict RunID ledger isolation, preventing cross-run contamination
- Durable idempotency state machine behavior (PENDING -> EXECUTED -> SKIPPED)
- On-disk WAL mode ledger and zero-overhead in-memory ledger mode with no disk footprint
- Central configuration, programmatic overrides, and quiet hours calculation across normal and inverted windows
"""

from __future__ import annotations

import os
import time
import pytest

from engine.config import Config, load_config
from engine.ledger import InflightRecoveryList, RecoveryLedger, make_idempotency_key
from engine.models import (
    ActionType,
    AuditEvent,
    CaseState,
    DiagnosisResult,
    EvaluatedIntervention,
    LeakType,
    PolicyDecision,
    RecoveryCase,
    validate_transition,
)
from engine.pii import (
    audit_scan_pii,
    mask_card,
    mask_dict,
    mask_email,
    mask_gstin,
    mask_name,
    mask_phone,
    mask_text,
)


# =====================================================================
# 1. Domain Models & Canonical Contracts
# =====================================================================

class TestCanonicalModels:
    """Verifies dataclass creation, serialization, and state machine transitions."""

    @pytest.mark.parametrize(
        "leak_type,metadata",
        [
            (
                LeakType.FAILED_PAYMENT,
                {"decline_code": "insufficient_funds", "bank": "HDFC", "gateway": "Razorpay", "prior_failures": 1},
            ),
            (
                LeakType.ABANDONED_CHECKOUT,
                {"drop_step": "otp", "cart_items": 3, "cart_age_hours": 2.5},
            ),
            (
                LeakType.SUBSCRIPTION_MANDATE,
                {"mandate_id": "man_1001", "plan_id": "pro_monthly", "renewal_attempt": 2, "mandate_type": "upi_autopay"},
            ),
            (
                LeakType.B2B_INVOICE,
                {"invoice_id": "INV-2026-089", "po_number": "PO-998", "due_date": "2026-10-15", "client_tier": "tier_1"},
            ),
        ],
    )
    def test_all_four_leak_types_creation_and_roundtrip(self, leak_type: LeakType, metadata: dict) -> None:
        """Verify creation, dict conversion, and deserialization of all 4 LeakType cases."""
        case = RecoveryCase(
            case_id=f"case_{leak_type.value}_001",
            customer_id="cust_9988",
            customer_name="Rohan Gupta",
            phone_masked="+91 98••• ••210",
            email_masked="r•••••a@example.com",
            leak_type=leak_type,
            amount=14999.0,
            days_at_risk=4,
            created_at=1700000000.0,
            metadata=metadata,
            true_root_cause="insufficient_funds",
            is_salary_week=True,
            state=CaseState.DETECTED,
            assigned_run_id="bench_run_01",
            is_control_group=False,
        )

        assert case.leak_type == leak_type
        assert case.amount == 14999.0
        assert case.txn_id == case.case_id
        assert case.invoice_id == case.case_id
        assert case.is_terminal() is False

        # Serialization round-trip
        data_dict = case.to_dict()
        assert data_dict["leak_type"] == leak_type.value
        assert data_dict["case_id"] == case.case_id
        assert data_dict["metadata"] == metadata

        rebuilt = RecoveryCase.from_dict(data_dict)
        assert rebuilt.case_id == case.case_id
        assert rebuilt.leak_type == leak_type
        assert rebuilt.metadata == metadata
        assert rebuilt.state == CaseState.DETECTED

    def test_case_state_transitions(self) -> None:
        """Verify legal and illegal state machine transitions, including force override."""
        case = RecoveryCase(
            case_id="case_transition_test",
            customer_id="cust_1",
            customer_name="Test",
            phone_masked="",
            email_masked="",
            leak_type=LeakType.FAILED_PAYMENT,
            amount=500.0,
            days_at_risk=1,
        )
        assert case.state == CaseState.DETECTED

        # Legal: DETECTED -> DIAGNOSED
        case.transition_to(CaseState.DIAGNOSED)
        assert case.state == CaseState.DIAGNOSED

        # Legal: DIAGNOSED -> READY
        case.transition_to(CaseState.READY)
        assert case.state == CaseState.READY

        # Legal: READY -> EXECUTING
        case.transition_to(CaseState.EXECUTING)
        assert case.state == CaseState.EXECUTING

        # Legal: EXECUTING -> RECOVERED
        case.transition_to(CaseState.RECOVERED)
        assert case.state == CaseState.RECOVERED

        # Legal: RECOVERED -> CLOSED
        case.transition_to(CaseState.CLOSED)
        assert case.state == CaseState.CLOSED
        assert case.is_terminal() is True

        # Illegal: CLOSED cannot transition anywhere
        with pytest.raises(ValueError):
            case.transition_to(CaseState.READY)

        # Force override allows manual admin intervention
        case.transition_to(CaseState.READY, force=True)
        assert case.state == CaseState.READY

    def test_validate_transition_utility(self) -> None:
        """Test validate_transition helper directly."""
        assert validate_transition(CaseState.DETECTED, CaseState.DIAGNOSED) is True
        assert validate_transition(CaseState.DETECTED, CaseState.POLICY_BLOCKED) is True
        assert validate_transition(CaseState.DETECTED, CaseState.RECOVERED) is False
        assert validate_transition("DETECTED", "DIAGNOSED") is True
        assert validate_transition("INVALID", "DIAGNOSED") is False
        assert validate_transition(CaseState.READY, CaseState.READY) is True

    def test_diagnosis_result_roundtrip(self) -> None:
        """Verify DiagnosisResult dataclass serialization."""
        diag = DiagnosisResult(
            case_id="case_001",
            predicted_cause="issuer_down",
            confidence=0.89,
            source="systemic_detector",
            evidence=["HDFC success rate down 32% across 500 txns"],
            is_systemic=True,
            probabilities={"issuer_down": 0.89, "insufficient_funds": 0.11},
        )
        d = diag.to_dict()
        assert d["confidence"] == 0.89
        assert d["is_systemic"] is True
        rebuilt = DiagnosisResult.from_dict(d)
        assert rebuilt.case_id == diag.case_id
        assert rebuilt.predicted_cause == diag.predicted_cause
        assert rebuilt.probabilities == {"issuer_down": 0.89, "insufficient_funds": 0.11}

    def test_evaluated_intervention_roundtrip(self) -> None:
        """Verify EvaluatedIntervention dataclass serialization."""
        interv = EvaluatedIntervention(
            action=ActionType.REROUTE_GATEWAY,
            p_recovery=0.78,
            expected_value=7800.0,
            channel_cost=0.0,
            expected_discount=0.0,
            risk_penalty=0.0,
            human_cost=0.0,
            net_recovery=7800.0,
            rationale="Gateway degraded; rerouting to backup route with 92% uptime",
        )
        assert interv.name == "reroute_gateway"
        d = interv.to_dict()
        assert d["action"] == "reroute_gateway"
        rebuilt = EvaluatedIntervention.from_dict(d)
        assert rebuilt.action == ActionType.REROUTE_GATEWAY
        assert rebuilt.net_recovery == 7800.0

    def test_policy_decision_roundtrip(self) -> None:
        """Verify PolicyDecision dataclass serialization."""
        interv = EvaluatedIntervention(
            action=ActionType.WHATSAPP_NUDGE,
            p_recovery=0.65,
            expected_value=1300.0,
            channel_cost=0.5,
            expected_discount=0.0,
            risk_penalty=0.0,
            human_cost=0.0,
            net_recovery=1299.5,
            rationale="Optimal balance of low friction and recovery EV",
        )
        dec = PolicyDecision(
            case_id="case_002",
            allowed=True,
            chosen_action=ActionType.WHATSAPP_NUDGE,
            blocked_reason=None,
            applied_rules=["quiet_hours_checked_allowed", "opt_out_verified"],
            requires_human_approval=False,
            human_review_reason=None,
            ranked_interventions=[interv],
        )
        d = dec.to_dict()
        assert d["chosen_action"] == "whatsapp_nudge"
        rebuilt = PolicyDecision.from_dict(d)
        assert rebuilt.case_id == dec.case_id
        assert rebuilt.chosen_action == ActionType.WHATSAPP_NUDGE
        assert len(rebuilt.ranked_interventions) == 1

    def test_audit_event_roundtrip(self) -> None:
        """Verify AuditEvent dataclass serialization."""
        ev = AuditEvent(
            event_id="ev_101",
            case_id="case_003",
            run_id="run_test",
            from_state="READY",
            to_state="EXECUTING",
            rule_or_action="whatsapp_nudge",
            details={"channel": "whatsapp", "cost": 0.5},
            timestamp=1700001000.0,
        )
        d = ev.to_dict()
        rebuilt = AuditEvent.from_dict(d)
        assert rebuilt.event_id == "ev_101"
        assert rebuilt.details["channel"] == "whatsapp"


# =====================================================================
# 2. PII Masking Utilities & Zero-Leak Guarantees
# =====================================================================

class TestPIIMasking:
    """Verifies that no raw personal data survives deterministic masking."""

    def test_mask_phone_variants(self) -> None:
        """Verify phone masking on standard Indian and international formats."""
        raw_phone = "+91 98765 43210"
        masked = mask_phone(raw_phone)
        assert masked == "+91 98••• ••210"
        assert "765" not in masked
        assert "43" not in masked

        raw_plain = "9876543210"
        masked_plain = mask_phone(raw_plain)
        assert masked_plain == "98•••••210"
        assert "76543" not in masked_plain

        # Intermediate length: e.g. 6 digits
        assert mask_phone("123456") == "12••56"

        # Edge cases
        assert mask_phone("") == ""
        assert mask_phone(None) == ""
        assert mask_phone("12") == "••"

    def test_mask_email_variants(self) -> None:
        """Verify email masking with exact privacy guarantees."""
        raw_email = "ananya.sharma@example.com"
        masked = mask_email(raw_email)
        assert masked == "a•••••a@example.com"
        assert "sharma" not in masked
        assert "ananya" not in masked

        # Short username edge cases
        assert mask_email("ab@domain.com") == "a•@domain.com"
        assert mask_email("a@domain.com") == "•@domain.com"
        assert mask_email("@domain.com") == "•••@domain.com"
        assert mask_email("no_at_sign") == "N•••••••••"
        assert mask_email("") == ""
        assert mask_email(None) == ""

    def test_mask_name_variants(self) -> None:
        """Verify customer name masking."""
        raw_name = "Rohan Gupta"
        masked = mask_name(raw_name)
        assert masked == "R•••• G••••"
        assert "ohan" not in masked
        assert "upta" not in masked

        assert mask_name("Ananya") == "A•••••"
        assert mask_name("A") == "A"
        assert mask_name("  ") == ""
        assert mask_name("") == ""
        assert mask_name(None) == ""

    def test_mask_card_variants(self) -> None:
        """Verify card masking reveals only last 4 digits."""
        raw_card = "4111 2222 3333 4242"
        masked = mask_card(raw_card)
        assert masked == "•••• •••• •••• 4242"
        assert "4111" not in masked
        assert "2222" not in masked
        assert "3333" not in masked

        # Just last 4 passed
        assert mask_card("4242") == "•••• •••• •••• 4242"
        assert mask_card("42") == "•••• •••• •••• ••42"
        assert mask_card("") == "•••• •••• •••• ••••"
        assert mask_card(None) == "•••• •••• •••• ••••"

    def test_mask_gstin(self) -> None:
        """Verify Indian GSTIN masking."""
        raw_gstin = "27AAPFU0939F1ZV"
        masked = mask_gstin(raw_gstin)
        assert masked == "27••••••••••1ZV"
        assert "AAPFU" not in masked
        assert "0939F" not in masked

        assert mask_gstin("27AAPFU1") == "27•••FU1"
        assert mask_gstin("1234") == "••••"
        assert mask_gstin("") == ""
        assert mask_gstin(None) == ""

    def test_mask_dict_deep_sanitization(self) -> None:
        """Verify recursive dictionary masking on complex nested payloads."""
        raw_payload = {
            "case_id": "case_101",
            "customer_name": "Rohan Gupta",
            "phone": "+91 98765 43210",
            "email": "ananya.sharma@example.com",
            "card_number": "4111222233334242",
            "gstin": "27AAPFU0939F1ZV",
            "contacts": [
                {
                    "name": "Priya Patel",
                    "mobile": "9812345678",
                    "email": "priya.patel@razorpay.com",
                }
            ],
            "metadata": {
                "amount": 25000,
                "notes": "Sent SMS to +91 98765 43210",
            },
        }

        sanitized = mask_dict(raw_payload)

        # Original payload must not be mutated
        assert raw_payload["customer_name"] == "Rohan Gupta"

        # Check masked fields
        assert sanitized["customer_name"] == "R•••• G••••"
        assert sanitized["phone"] == "+91 98••• ••210"
        assert sanitized["email"] == "a•••••a@example.com"
        assert sanitized["card_number"] == "•••• •••• •••• 4242"
        assert sanitized["gstin"] == "27••••••••••1ZV"

        # Check nested list
        assert sanitized["contacts"][0]["name"] == "P•••• P••••"
        assert sanitized["contacts"][0]["email"] == "p•••••l@razorpay.com"

        # Check inline text within notes
        assert "98765 43210" not in sanitized["metadata"]["notes"]

    def test_mask_text_embedded_pii(self) -> None:
        """Verify embedded PII scrubbing from free-form strings and log messages."""
        log_line = "Error contacting customer Rohan at ananya.sharma@example.com on +91 98765 43210 using card 4111-2222-3333-4242"
        scrubbed = mask_text(log_line)
        assert "ananya.sharma@example.com" not in scrubbed
        assert "98765 43210" not in scrubbed
        assert "4111-2222-3333-4242" not in scrubbed
        assert "a•••••a@example.com" in scrubbed
        assert "4242" in scrubbed

        assert mask_text("") == ""
        assert mask_text(None) == ""

    def test_audit_scan_pii(self) -> None:
        """Verify audit scanner flags unmasked data and approves sanitized data."""
        dirty = "Customer phone is 9876543210 and email is test@domain.com with card 4111222233334242"
        violations = audit_scan_pii(dirty)
        assert len(violations) == 3

        clean = mask_text(dirty)
        clean_violations = audit_scan_pii(clean)
        assert len(clean_violations) == 0


# =====================================================================
# 3. Idempotent Ledger & Strict RunID Partitioning
# =====================================================================

class TestRecoveryLedger:
    """Verifies crash-safe state machine, run isolation, and in-memory operations."""

    @pytest.fixture
    def mem_ledger(self) -> RecoveryLedger:
        """Create a clean in-memory ledger for isolated testing."""
        ledger = RecoveryLedger(db_path=":memory:", default_run_id="test_run")
        yield ledger
        ledger.close()

    def test_idempotency_key_generation(self) -> None:
        """Verify deterministic 24-character SHA256 key generation."""
        key1 = make_idempotency_key("cust_01", "inv_01", 1, "retry_delayed")
        key2 = make_idempotency_key("cust_01", "inv_01", 1, "retry_delayed")
        key3 = make_idempotency_key("cust_01", "inv_01", 2, "retry_delayed")

        assert len(key1) == 24
        assert key1 == key2
        assert key1 != key3

        # Backwards compatibility alias
        key_alias = make_idempotency_key(customer_id="cust_01", invoice_id="inv_01", attempt_number=1, action="retry_delayed")
        assert key_alias == key1

    def test_ledger_idempotency_within_same_run(self, mem_ledger: RecoveryLedger) -> None:
        """Verify that within the same run_id, a second call returns should_execute=False."""
        run_id = "batch_run_alpha"

        # 1. First execution
        key, should_exec, state = mem_ledger.begin_action(
            run_id=run_id,
            customer_id="cust_100",
            case_id="case_100",
            attempt_number=1,
            action="whatsapp_nudge",
            root_cause="insufficient_funds",
            expected_value=2500.0,
        )
        assert should_exec is True
        assert state == "PENDING"

        # 2. Complete the action
        completed = mem_ledger.complete_action(
            run_id=run_id,
            key=key,
            success=True,
            result_detail={"message_id": "wa_msg_99", "delivered": True},
        )
        assert completed is True

        # 3. Re-execution with exact same parameters (simulating crash or re-run)
        key2, should_exec2, state2 = mem_ledger.begin_action(
            run_id=run_id,
            customer_id="cust_100",
            case_id="case_100",
            attempt_number=1,
            action="whatsapp_nudge",
            root_cause="insufficient_funds",
            expected_value=2500.0,
        )
        assert key2 == key
        assert should_exec2 is False
        assert state2 == "EXECUTED"

        # 4. Check that audit history captured the skip
        history = mem_ledger.get_case_history(run_id, "case_100")
        assert len(history) >= 2
        states = [h["to_state"] for h in history]
        assert "PENDING" in states
        assert "EXECUTED" in states
        assert "SKIPPED" in states

    def test_cross_run_isolation(self, mem_ledger: RecoveryLedger) -> None:
        """Verify that running with run_A followed by run_B with identical case executes cleanly without interference."""
        case_id = "shared_case_888"
        customer_id = "cust_888"
        attempt = 1
        action = "retry_immediate"

        # Run A: execute to completion
        run_a = "run_A"
        key_a, should_exec_a, _ = mem_ledger.begin_action(
            run_id=run_a,
            customer_id=customer_id,
            case_id=case_id,
            attempt_number=attempt,
            action=action,
            root_cause="bank_down",
            expected_value=5000.0,
        )
        assert should_exec_a is True
        mem_ledger.complete_action(run_a, key_a, success=True)

        # Run B: run with identical case parameters
        run_b = "run_B"
        key_b, should_exec_b, state_b = mem_ledger.begin_action(
            run_id=run_b,
            customer_id=customer_id,
            case_id=case_id,
            attempt_number=attempt,
            action=action,
            root_cause="bank_down",
            expected_value=5000.0,
        )

        # CRITICAL ASSERTION: run_B MUST be allowed to execute (not contaminated by run_A)
        assert key_b == key_a
        assert should_exec_b is True
        assert state_b == "PENDING"

        # Summaries must remain cleanly isolated
        summary_a = mem_ledger.summary(run_id=run_a)
        assert summary_a["EXECUTED"]["count"] == 1

        summary_b = mem_ledger.summary(run_id=run_b)
        assert summary_b["PENDING"]["count"] == 1

        summary_all = mem_ledger.summary()
        assert summary_all["EXECUTED"]["count"] == 1
        assert summary_all["PENDING"]["count"] == 1

    def test_purge_run(self, mem_ledger: RecoveryLedger) -> None:
        """Verify that purging a test run cleanly wipes its data without affecting other runs."""
        key_x, _, _ = mem_ledger.begin_action("run_X", "c1", "case1", 1, "sms", "cause", 100.0)
        key_y, _, _ = mem_ledger.begin_action("run_Y", "c2", "case2", 1, "sms", "cause", 200.0)

        # Purge run_X
        purged = mem_ledger.purge_run("run_X")
        assert purged["actions_purged"] >= 1
        assert purged["runs_purged"] == 1

        # Assert run_X is gone
        assert mem_ledger.get_action("run_X", key_x) is None
        assert mem_ledger.get_run("run_X") is None

        # Assert run_Y remains completely intact
        assert mem_ledger.get_action("run_Y", key_y) is not None
        assert mem_ledger.get_run("run_Y") is not None

    def test_in_memory_mode_creates_no_disk_files(self) -> None:
        """Verify that db_path=':memory:' leaves no disk artifacts."""
        ledger = RecoveryLedger(db_path=":memory:", default_run_id="mem_test")
        key, should_exec, _ = ledger.begin_action("mem_test", "c", "inv", 1, "action", "cause", 10.0)
        assert should_exec is True
        ledger.complete_action("mem_test", key, True)

        assert not os.path.exists(":memory:")
        ledger.close()

    def test_on_disk_ledger_persistence(self, tmp_path) -> None:
        """Verify on-disk SQLite ledger with WAL mode handles reopen and data persistence."""
        db_file = str(tmp_path / "test_ledger.db")
        ledger = RecoveryLedger(db_path=db_file, default_run_id="disk_run")
        key, should_exec, _ = ledger.begin_action("disk_run", "c1", "inv1", 1, "action", "cause", 100.0)
        assert should_exec is True
        ledger.complete_action("disk_run", key, True)

        # List runs & check history
        runs = ledger.list_runs()
        assert len(runs) >= 1
        hist = ledger.history(run_id="disk_run", limit=10)
        assert len(hist) >= 1

        # Reopen with new instance pointing to same file
        reopened = RecoveryLedger(db_path=db_file, default_run_id="disk_run")
        act = reopened.get_action("disk_run", key)
        assert act is not None
        assert act["state"] == "EXECUTED"

    def test_crash_inflight_recovery(self, mem_ledger: RecoveryLedger) -> None:
        """Verify that uncompleted PENDING actions can be retrieved after an abrupt stop."""
        run_id = "crash_test_run"
        k1, _, _ = mem_ledger.begin_action(run_id, "c1", "case1", 1, "act", "cause", 100.0)
        k2, _, _ = mem_ledger.begin_action(run_id, "c2", "case2", 1, "act", "cause", 200.0)
        k3, _, _ = mem_ledger.begin_action(run_id, "c3", "case3", 1, "act", "cause", 300.0)

        mem_ledger.complete_action(run_id, k1, success=True)
        # k2 and k3 remain PENDING

        inflight = mem_ledger.recover_inflight_actions(run_id)
        assert len(inflight) == 2
        assert int(inflight) == 2
        assert inflight.count == 2
        pending_keys = {item["idempotency_key"] for item in inflight}
        assert k2 in pending_keys
        assert k3 in pending_keys
        assert k1 not in pending_keys

    def test_legacy_call_compatibility(self, mem_ledger: RecoveryLedger) -> None:
        """Verify that legacy 7-parameter begin_action and 3-parameter complete_action work seamlessly."""
        key, should_exec, state = mem_ledger.begin_action(
            "cust_legacy", "inv_legacy", 1, "retry_delayed", "insufficient_funds", 10998.0
        )
        assert len(key) == 24
        assert should_exec is True
        assert state == "PENDING"

        # Complete with legacy signature
        mem_ledger.complete_action(key, success=True, result_detail={"recovered": True})

        # Check action was updated
        act = mem_ledger.get_action(key)
        assert act is not None
        assert act["state"] == "EXECUTED"


# =====================================================================
# 4. Central Configuration & Policy Levers
# =====================================================================

class TestCentralConfig:
    """Verifies configuration loading, threshold defaults, and quiet hour boundaries."""

    def test_default_config_values(self) -> None:
        """Verify default thresholds match specification."""
        cfg = Config()
        assert cfg.SQLITE_DB_PATH == "models/recovery_ledger.db"
        assert cfg.DEFAULT_RUN_ID == "default_run"
        assert cfg.MAX_ATTEMPTS == 3
        assert cfg.CONFIDENCE_HUMAN_FLOOR == 0.35
        assert cfg.MARGIN_DISCOUNT_CAP_PCT == 0.10
        assert cfg.QUIET_HOURS_START == 19  # 7:00 PM
        assert cfg.QUIET_HOURS_END == 8     # 8:00 AM
        assert cfg.HOLDOUT_RATIO == 0.15    # 15% holdout
        assert isinstance(cfg.CHANNEL_UNIT_COSTS, dict)

    def test_is_quiet_hours_calculation(self) -> None:
        """Verify quiet hours check across daytime and nighttime hours."""
        cfg = Config(QUIET_HOURS_START=19, QUIET_HOURS_END=8)

        # Night hours: 19:00 - 08:00 (quiet hours active)
        assert cfg.is_quiet_hours(19) is True  # 7 PM
        assert cfg.is_quiet_hours(21) is True  # 9 PM
        assert cfg.is_quiet_hours(23) is True  # 11 PM
        assert cfg.is_quiet_hours(0) is True   # Midnight
        assert cfg.is_quiet_hours(5) is True   # 5 AM
        assert cfg.is_quiet_hours(7) is True   # 7 AM

        # Business hours: 08:00 - 18:59 (quiet hours inactive)
        assert cfg.is_quiet_hours(8) is False   # 8 AM sharp
        assert cfg.is_quiet_hours(10) is False  # 10 AM
        assert cfg.is_quiet_hours(14) is False  # 2 PM
        assert cfg.is_quiet_hours(18) is False  # 6 PM

        # Inverted / Daytime quiet window: e.g. 13:00 to 15:00
        day_quiet_cfg = Config(QUIET_HOURS_START=13, QUIET_HOURS_END=15)
        assert day_quiet_cfg.is_quiet_hours(14) is True
        assert day_quiet_cfg.is_quiet_hours(10) is False

        # Current system hour check
        assert isinstance(cfg.is_quiet_hours(), bool)

    def test_load_config_with_overrides(self, tmp_path) -> None:
        """Verify load_config accepts programmatic overrides and optional env file."""
        cfg = load_config(MAX_ATTEMPTS=5, MARGIN_DISCOUNT_CAP_PCT=0.15)
        assert cfg.MAX_ATTEMPTS == 5
        assert cfg.MARGIN_DISCOUNT_CAP_PCT == 0.15
        assert isinstance(cfg.to_dict(), dict)

        # Test loading from explicit .env path
        fake_env = tmp_path / ".env.test"
        fake_env.write_text("MAX_ATTEMPTS=7\n")
        cfg_env = load_config(env_path=str(fake_env))
        assert cfg_env.MAX_ATTEMPTS == 7
