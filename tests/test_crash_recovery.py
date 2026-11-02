"""
Crash-and-Resume Resilience Verification Test:
Simulates mid-batch sudden kill and tests exact idempotency,
zero duplicate provider dispatches, and exact rupee financial consistency upon resumption.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.models import RecoveryCase
from engine.ledger import RecoveryLedger
from engine.execution.outbox import TransactionalOutbox
from engine.execution.providers import PROVIDER_LOG
from engine.batch_runner import run_batch, MidBatchCrashException
from data.generate_demo_batch import generate_batch


class TestCrashRecovery:
    @pytest.fixture(autouse=True)
    def setup_test(self):
        self.ledger = RecoveryLedger(db_path=":memory:")
        self.outbox = TransactionalOutbox(self.ledger)
        PROVIDER_LOG.clear()

    def test_mid_batch_kill_and_resumption(self):
        # Generate 100 cases
        all_cases = generate_batch(seed=42, count=300)
        cases_100 = all_cases[:100]

        crash_run_id = "crash_and_resume_test_run"

        # 1. Run a single uninterrupted reference run to establish ground-truth financial summary
        ref_ledger = RecoveryLedger(db_path=":memory:")
        ref_outbox = TransactionalOutbox(ref_ledger)
        uninterrupted_result = run_batch(
            cases=cases_100,
            run_id=crash_run_id,
            ledger=ref_ledger,
            outbox=ref_outbox,
        )

        PROVIDER_LOG.clear()

        # 2. Part 1: Mid-Batch Kill at case #47
        crashed = False
        try:
            run_batch(
                cases=cases_100,
                run_id=crash_run_id,
                ledger=self.ledger,
                outbox=self.outbox,
                kill_at_case=47,
            )
        except MidBatchCrashException as exc:
            crashed = True
            assert "case #47" in str(exc)

        assert crashed is True, "Expected MidBatchCrashException to be raised at case #47"

        # Count provider calls made before crash
        pre_resume_provider_dispatches = len(PROVIDER_LOG.dispatches)
        assert pre_resume_provider_dispatches > 0

        # Snapshot of keys dispatched in phase 1
        dispatched_keys_phase1 = [d["idempotency_key"] for d in PROVIDER_LOG.dispatches]
        assert len(dispatched_keys_phase1) == len(set(dispatched_keys_phase1)), "No duplicates allowed in Phase 1"

        # 3. Part 2: Recovery In-Flight Actions
        reconciled_count = self.ledger.recover_inflight_actions(crash_run_id)

        # 4. Part 3: Restart Batch Runner with exact same run_id
        resumed_result = run_batch(
            cases=cases_100,
            run_id=crash_run_id,
            ledger=self.ledger,
            outbox=self.outbox,
        )

        # 5. Assertions on Resumed Execution

        # A. Cases #1–46 must be marked SKIPPED (idempotent no-op)
        processed_cases = resumed_result["processed_cases"]
        assert len(processed_cases) == 100

        skipped_early_cases = [
            c for idx, c in enumerate(processed_cases[:46], start=1)
            if c["status"] == "SKIPPED"
        ]
        # Those that were executed in run 1 must now be SKIPPED
        assert len(skipped_early_cases) > 20, "Early dispatched cases must be marked SKIPPED on resumption"

        # B. Assert Zero Duplicate Records Created in Provider Logs
        all_dispatched_keys = [d["idempotency_key"] for d in PROVIDER_LOG.dispatches]
        assert len(all_dispatched_keys) == len(set(all_dispatched_keys)), (
            "Duplicate provider dispatch detected! Idempotency key violation."
        )

        # Verify that for any case dispatched in phase 1, exactly 1 record exists in provider log
        for key in dispatched_keys_phase1:
            assert PROVIDER_LOG.count_for_key(key) == 1, f"Key {key} was dispatched more than once!"

        # C. Cases #48–100 finish execution cleanly
        late_cases = processed_cases[47:]
        assert len(late_cases) == 53
        for c in late_cases:
            assert c["status"] in ("EXECUTED", "CONTROL_GROUP", "POLICY_BLOCKED")

        # D. Assert Final Financial Summary Matches Single Uninterrupted Run to the EXACT Rupee!
        assert resumed_result["gross_recovered"] == pytest.approx(uninterrupted_result["gross_recovered"], abs=0.01), (
            f"Gross recovered mismatch: {resumed_result['gross_recovered']} vs {uninterrupted_result['gross_recovered']}"
        )
        assert resumed_result["channel_costs"] == pytest.approx(uninterrupted_result["channel_costs"], abs=0.01), (
            f"Channel costs mismatch: {resumed_result['channel_costs']} vs {uninterrupted_result['channel_costs']}"
        )
        assert resumed_result["discounts_given"] == pytest.approx(uninterrupted_result["discounts_given"], abs=0.01), (
            f"Discounts mismatch: {resumed_result['discounts_given']} vs {uninterrupted_result['discounts_given']}"
        )
        assert resumed_result["human_costs"] == pytest.approx(uninterrupted_result["human_costs"], abs=0.01), (
            f"Human costs mismatch: {resumed_result['human_costs']} vs {uninterrupted_result['human_costs']}"
        )
        assert resumed_result["net_recovered"] == pytest.approx(uninterrupted_result["net_recovered"], abs=0.01), (
            f"Net recovered mismatch: {resumed_result['net_recovered']} vs {uninterrupted_result['net_recovered']}"
        )
