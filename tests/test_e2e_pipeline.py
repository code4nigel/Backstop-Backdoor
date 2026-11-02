"""
Comprehensive End-to-End Pipeline Integration Test:
Validates the complete lifecycle:
1. Ingests 300-case demo batch.
2. Runs Systemic Degradation Detector and verifies flagged degraded routes.
3. Multi-leak diagnosis suite confidence scores and evidence generation.
4. Cost-aware Net EV and policy compliance gates (DND, quiet hours, touch caps).
5. Transactional outbox action dispatch.
6. Outcome simulation against hidden ground-truth causes.
7. Verifies 15% Holdout Control Group receives ZERO outreach and organic recovery only.
8. Statistical evaluation report validation (positive incremental lift, ROI, Wilson confidence intervals).
"""
import os
import sys
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.models import RecoveryCase, LeakType, ActionType, CaseState
from engine.ledger import RecoveryLedger
from engine.systemic import SystemicDetector
from engine.diagnosis import DiagnosisEngine
from engine.policy import PolicyEngine
from engine.execution.outbox import TransactionalOutbox
from engine.execution.providers import PROVIDER_LOG
from engine.measurement import simulate_recovery_outcome, compute_confidence_interval
from engine.evaluator import evaluate_batch
from data.generate_demo_batch import generate_batch


class TestEndToEndPipeline:
    @pytest.fixture(autouse=True)
    def setup_pipeline(self):
        self.ledger = RecoveryLedger(db_path=":memory:")
        self.outbox = TransactionalOutbox(self.ledger)
        self.systemic = SystemicDetector(degradation_threshold=0.15)
        self.systemic.force_incident("HDFC:Razorpay_Direct", current_rate=0.32, reason="HDFC UPI degraded")
        self.diagnosis = DiagnosisEngine(systemic_detector=self.systemic)
        self.policy = PolicyEngine()
        PROVIDER_LOG.clear()

    def test_complete_e2e_lifecycle(self):
        # 1. Ingest 300-case demo batch
        cases = generate_batch(seed=42, count=300)
        assert len(cases) == 300, f"Expected 300 cases, got {len(cases)}"
        
        # Verify 4 leak types are represented
        leak_types = {c.leak_type for c in cases}
        assert LeakType.FAILED_PAYMENT in leak_types
        assert LeakType.ABANDONED_CHECKOUT in leak_types
        assert LeakType.SUBSCRIPTION_MANDATE in leak_types
        assert LeakType.B2B_INVOICE in leak_types

        # 2. Confirm Systemic Route Degradation is flagged
        hdfc_degraded_count = 0
        for c in cases:
            if c.metadata.get("bank") == "HDFC":
                is_deg, inc = self.systemic.is_degraded("HDFC", "Razorpay_Direct")
                if is_deg:
                    hdfc_degraded_count += 1
        assert hdfc_degraded_count > 0, "Expected systemic degradation to flag HDFC routes"

        # 3. Multi-Leak Diagnosis Suite & Confidence Scores
        for c in cases:
            diag_res = self.diagnosis.diagnose_case(c)
            assert diag_res.predicted_cause != ""
            assert 0.0 <= diag_res.confidence <= 1.0
            assert len(diag_res.evidence) > 0
            if c.metadata.get("is_systemic"):
                assert diag_res.is_systemic is True
                assert diag_res.predicted_cause == "systemic_route_degraded"

        # 4. Cost-Aware Net EV & Policy Compliance Gates
        decisions = []
        for c in cases:
            diag_res = self.diagnosis.diagnose_case(c)
            dec = self.policy.evaluate(c, diag_res.predicted_cause, diag_res.confidence)
            decisions.append((c, diag_res, dec))

            if dec.allowed:
                assert dec.chosen_action != ActionType.DO_NOTHING
                assert dec.blocked_reason is None
            else:
                assert dec.blocked_reason is not None

        # 5. Dispatch Actions through Transactional Outbox
        run_id = "e2e_integration_test_run"
        dispatched_count = 0
        for c, diag_res, dec in decisions:
            if c.is_control_group or not dec.allowed:
                continue
            key, state, res = self.outbox.dispatch_action(run_id, c, dec.chosen_action)
            assert state in ("CONFIRMED", "SKIPPED")
            dispatched_count += 1

        assert dispatched_count > 50, "Expected significant portion of approved actions to be dispatched"

        # 6 & 7. Verify Holdout Control Group (15%) Received ZERO Outreach
        control_cases = [c for c in cases if c.is_control_group]
        assert 35 <= len(control_cases) <= 55, f"Expected ~15% control cases (around 45), got {len(control_cases)}"

        for ctrl_case in control_cases:
            # Control cases must NEVER have records in provider dispatch logs!
            assert PROVIDER_LOG.count_for_case(ctrl_case.case_id) == 0, (
                f"Control case {ctrl_case.case_id} received outreach in violation of holdout contract!"
            )
            # Organic recovery simulation
            rec, p = simulate_recovery_outcome(ctrl_case, ActionType.DO_NOTHING, run_id)
            assert 0.00 <= p <= 0.15, f"Expected organic self-cure probability, got {p}"

        # 8. Assert Final Evaluation Report Statistics
        report = evaluate_batch(cases, self.policy, self.diagnosis, run_id=run_id)
        
        # Verify Report Structure and Metrics
        assert report.total_cases == 300
        assert report.total_revenue_at_risk > 4000000.0, "Expected > ₹40L at risk"
        assert report.agent_metrics.gross_recovered > report.control_metrics.gross_recovered
        assert report.agent_metrics.net_recovered > report.control_metrics.net_recovered
        
        # Verify Statistically Sound Positive Lift
        assert report.incremental_lift_vs_control_pct > 20.0, (
            f"Expected strong lift over control, got {report.incremental_lift_vs_control_pct}%"
        )
        assert report.incremental_lift_vs_naive_pct > 0.0, "Expected positive lift over naive strategy"
        assert report.agent_metrics.roi > 5.0, f"Expected ROI > 5x, got {report.agent_metrics.roi}x"

        # Verify Confidence Intervals (95%)
        ci_low, ci_high = report.agent_metrics.ci_low, report.agent_metrics.ci_high
        assert 0.0 < ci_low < report.agent_metrics.recovery_rate < ci_high < 1.0
        assert (ci_high - ci_low) < 0.20, "Expected well-bounded confidence interval for n=300"
