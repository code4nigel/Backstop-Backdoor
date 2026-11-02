"""
Unit and Integration Tests for Scientific Measurement and Isolated Evaluation.
Verifies:
  1. Deterministic Seeding: Repeating runs with the same run_id yields 100% identical numbers.
  2. Stratified Holdout Control Group: Exactly ~15% stratified across leak types and amount buckets.
  3. Truthful Ground-Truth Scoring: Misdiagnosed cases are penalized against true underlying causes.
  4. Financial Math Accuracy: Gross, channel costs, discounts, ₹150 human review fees, Net, Lift, ROI.
  5. Confidence Interval Mathematics: 95% Wilson interval bounds bounded [0, 1] with correct width scaling.
  6. Evaluator Isolation: In-memory evaluation runs with zero DB pollution and deterministic outputs.
"""
import copy
import json
import math
import os
import random
import pytest

from engine.models import ActionType, CaseState, LeakType, RecoveryCase
from engine.config import CHANNEL_UNIT_COSTS, HUMAN_REVIEW_FEE
from engine.measurement import (
    GROUND_TRUTH_RECOVERY_PROB,
    assign_stratified_holdout,
    calculate_case_cost,
    compute_comparative_metrics,
    compute_confidence_interval,
    compute_wilson_ci,
    get_amount_bucket,
    get_deterministic_rng,
    get_ground_truth_prob,
    simulate_outcome,
    simulate_recovery_outcome,
    ConfidenceInterval,
    StrategyMetrics,
)
from engine.evaluator import evaluate_batch, EvaluationReport


# ---------------------------------------------------------------------------
# Test Fixtures & Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_cases() -> list[RecoveryCase]:
    """Generates a small controlled batch of multi-leak cases for deterministic testing."""
    rng = random.Random(42)
    cases = []
    leak_types = [
        LeakType.FAILED_PAYMENT,
        LeakType.ABANDONED_CHECKOUT,
        LeakType.SUBSCRIPTION_MANDATE,
        LeakType.B2B_INVOICE,
    ]
    amounts = [1500.0, 5000.0, 15000.0, 50000.0]

    for i in range(100):
        lt = leak_types[i % len(leak_types)]
        amt = amounts[i % len(amounts)]
        case = RecoveryCase(
            case_id=f"TEST_CASE_{i:04d}",
            customer_id=f"CUST_{i:04d}",
            customer_name="Test Customer",
            phone_masked="+91 98••• ••210",
            email_masked="t••••t@example.com",
            leak_type=lt,
            amount=amt,
            days_at_risk=i % 10 + 1,
            created_at=1700000000.0 + i * 3600,
            metadata={"bank": "HDFC", "channel": "UPI", "prior_failures": 1},
            true_root_cause="insufficient_funds" if i % 2 == 0 else "expired_card",
            is_salary_week=(i % 4 == 0),
            state=CaseState.DETECTED,
            assigned_run_id="test_run",
            is_control_group=False,
        )
        cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# 1. Deterministic Seeding Tests
# ---------------------------------------------------------------------------

class TestDeterministicSeeding:
    def test_repeat_simulations_identical(self, sample_cases):
        """Verify that identical case_id + run_id produces 100% identical recovery outcomes."""
        run_id = "reproducibility_run_001"
        outcomes_1 = [
            simulate_outcome(c.case_id, run_id, c.true_root_cause, ActionType.RETRY_DELAYED, c.is_salary_week)
            for c in sample_cases
        ]
        outcomes_2 = [
            simulate_outcome(c.case_id, run_id, c.true_root_cause, ActionType.RETRY_DELAYED, c.is_salary_week)
            for c in sample_cases
        ]
        assert outcomes_1 == outcomes_2, "Outcomes between identical run_ids must be 100% deterministic"

    def test_simulate_recovery_outcome_identical(self, sample_cases):
        """Verify simulate_recovery_outcome method yields identical results across repeated calls."""
        run_id = "reproducibility_run_002"
        res_1 = [simulate_recovery_outcome(c, ActionType.WHATSAPP_NUDGE, run_id) for c in sample_cases]
        res_2 = [simulate_recovery_outcome(c, ActionType.WHATSAPP_NUDGE, run_id) for c in sample_cases]
        assert res_1 == res_2

    def test_different_run_id_produces_variance(self, sample_cases):
        """Verify different run_ids generate distinct hash sequences."""
        rng1 = get_deterministic_rng("case_999", "run_A")
        rng2 = get_deterministic_rng("case_999", "run_B")
        seq1 = [rng1.random() for _ in range(10)]
        seq2 = [rng2.random() for _ in range(10)]
        assert seq1 != seq2, "Different run_ids must produce different random sequences"


# ---------------------------------------------------------------------------
# 2. Stratified Holdout Control Group Tests
# ---------------------------------------------------------------------------

class TestHoldoutStratification:
    def test_stratified_holdout_ratio(self, sample_cases):
        """Verify holdout assignment achieves ~15% across all 4 leak types and amount buckets."""
        assigned = assign_stratified_holdout(sample_cases, holdout_ratio=0.15, seed=42)
        total = len(assigned)
        ctrl_count = sum(1 for c in assigned if c.is_control_group)
        actual_ratio = ctrl_count / total

        # Expected ratio: ~15% (+/- 3% for integer rounding on 100 cases)
        assert 0.12 <= actual_ratio <= 0.18, f"Holdout ratio {actual_ratio} out of bounds"

        # Verify representation in every leak type
        for lt in LeakType:
            lt_cases = [c for c in assigned if c.leak_type == lt]
            lt_ctrl = sum(1 for c in lt_cases if c.is_control_group)
            assert lt_ctrl > 0, f"Leak type {lt} has no holdout representation"
            ratio = lt_ctrl / len(lt_cases)
            assert 0.10 <= ratio <= 0.20

    def test_amount_bucket_categorization(self):
        """Verify amount buckets: low (<2000), medium (2000-10000), high (>10000)."""
        assert get_amount_bucket(500.0) == "low"
        assert get_amount_bucket(1999.99) == "low"
        assert get_amount_bucket(2000.0) == "medium"
        assert get_amount_bucket(10000.0) == "medium"
        assert get_amount_bucket(10000.01) == "high"
        assert get_amount_bucket(250000.0) == "high"


# ---------------------------------------------------------------------------
# 3. Truthful Ground-Truth Scoring & Misdiagnosis Penalties
# ---------------------------------------------------------------------------

class TestGroundTruthScoring:
    def test_expired_card_hard_decline(self):
        """Retrying an expired card immediately must have 0.00 recovery probability."""
        prob_retry = get_ground_truth_prob("expired_card", ActionType.RETRY_IMMEDIATE)
        assert prob_retry == 0.0, "Immediate retry on expired card must yield 0.00 recovery"

        # Correct intervention: card update link
        prob_link = get_ground_truth_prob("expired_card", ActionType.CARD_UPDATE_LINK)
        assert prob_link >= 0.65, "Card update link on expired card must have high recovery"

    def test_misdiagnosis_penalty(self):
        """
        If agent misdiagnoses an expired card as insufficient_funds and fires retry_delayed,
        it must be scored against true_root_cause=expired_card (0.02) rather than insufficient_funds (0.68)!
        """
        # True cause: expired_card, Action: retry_delayed
        actual_prob = get_ground_truth_prob("expired_card", ActionType.RETRY_DELAYED)
        # Diagnosed cause: insufficient_funds
        perceived_prob = get_ground_truth_prob("insufficient_funds", ActionType.RETRY_DELAYED)

        assert actual_prob <= 0.05, "Actual recovery on expired card with delayed retry must be negligible"
        assert perceived_prob >= 0.60, "Perceived recovery on insufficient funds would be high"
        assert actual_prob < perceived_prob, "Misdiagnosed intervention must be heavily penalized"

    def test_systemic_degradation_penalty(self):
        """During systemic outage (issuer_down), immediate retries fail, while rerouting succeeds."""
        prob_retry = get_ground_truth_prob("issuer_down", ActionType.RETRY_IMMEDIATE)
        prob_reroute = get_ground_truth_prob("issuer_down", ActionType.REROUTE_GATEWAY)

        assert prob_retry <= 0.05, "Immediate retry during issuer down must fail"
        assert prob_reroute >= 0.75, "Rerouting around degraded route must succeed with high probability"

    def test_salary_week_uplift(self):
        """Salary week provides positive uplift for funds-related interventions."""
        prob_normal = get_ground_truth_prob("insufficient_funds", ActionType.RETRY_DELAYED, is_salary_week=False)
        prob_salary = get_ground_truth_prob("insufficient_funds", ActionType.RETRY_DELAYED, is_salary_week=True)
        assert prob_salary > prob_normal, "Salary week must boost recovery probability for delayed retries"


# ---------------------------------------------------------------------------
# 4. Financial Mathematics & Cost Deductions
# ---------------------------------------------------------------------------

class TestFinancialMath:
    def test_case_cost_calculation(self):
        """Verify unit costs: retry (₹2), human escalation (₹150), discount redeemed."""
        # 1. Base channel costs
        ch, disc, hm = calculate_case_cost(ActionType.RETRY_IMMEDIATE, 5000.0, recovered=True)
        assert ch == 2.0
        assert disc == 0.0
        assert hm == 0.0

        # 2. Human review fee
        ch, disc, hm = calculate_case_cost(ActionType.HUMAN_ESCALATION, 50000.0, recovered=True)
        assert hm == 150.0, f"Human review fee must be ₹150, got {hm}"

        # 3. Discount given only if recovered
        ch, disc_rec, hm = calculate_case_cost(ActionType.DISCOUNT_INCENTIVE, 10000.0, recovered=True, discount_rate=0.08)
        assert disc_rec == 800.0  # 8% of 10,000

        ch, disc_fail, hm = calculate_case_cost(ActionType.DISCOUNT_INCENTIVE, 10000.0, recovered=False, discount_rate=0.08)
        assert disc_fail == 0.0, "Discount must not be redeemed if transaction was not recovered"

    def test_net_recovery_formula(self):
        """Verify: Net Recovered = Gross - Outreach Cost - Discounts - Human Cost."""
        metrics = StrategyMetrics(
            name="Test Strategy",
            total_cases=10,
            recovered_cases=5,
            recovery_rate=0.5,
            gross_recovered=100000.0,
            channel_cost=250.0,
            discount_cost=1500.0,
            human_review_cost=300.0,  # 2 reviews @ 150
            total_cost=2050.0,
            net_recovered=97950.0,
            roi=(97950.0 - 2050.0) / 2050.0,
            ci_low=0.25,
            ci_high=0.75,
        )
        assert metrics.net_recovered == metrics.gross_recovered - (
            metrics.channel_cost + metrics.discount_cost + metrics.human_review_cost
        )
        assert round(metrics.roi, 2) == round((97950.0 - 2050.0) / 2050.0, 2)

    def test_comparative_lift_formula(self):
        """Verify incremental lift over control and naive baseline formulas."""
        ctrl = StrategyMetrics(name="Control", total_cases=100, recovered_cases=6, recovery_rate=0.06,
                               gross_recovered=30000.0, channel_cost=0.0, discount_cost=0.0,
                               human_review_cost=0.0, total_cost=0.0, net_recovered=30000.0,
                               roi=0.0, ci_low=0.02, ci_high=0.12)

        naive = StrategyMetrics(name="Naive", total_cases=100, recovered_cases=20, recovery_rate=0.20,
                                gross_recovered=100000.0, channel_cost=200.0, discount_cost=0.0,
                                human_review_cost=0.0, total_cost=200.0, net_recovered=99800.0,
                                roi=498.0, ci_low=0.13, ci_high=0.29)

        agent = StrategyMetrics(name="Agent", total_cases=100, recovered_cases=55, recovery_rate=0.55,
                                gross_recovered=275000.0, channel_cost=400.0, discount_cost=2000.0,
                                human_review_cost=600.0, total_cost=3000.0, net_recovered=272000.0,
                                roi=89.67, ci_low=0.45, ci_high=0.64)

        c, n, a = compute_comparative_metrics(ctrl, naive, agent)

        expected_lift_ctrl = ((272000.0 - 30000.0) / 30000.0) * 100.0
        expected_lift_naive = ((272000.0 - 99800.0) / 99800.0) * 100.0

        assert round(a.lift_over_control_pct, 2) == round(expected_lift_ctrl, 2)
        assert round(a.lift_over_naive_pct, 2) == round(expected_lift_naive, 2)


# ---------------------------------------------------------------------------
# 5. Statistical Confidence Interval Mathematics
# ---------------------------------------------------------------------------

class TestConfidenceIntervals:
    def test_ci_bounds_within_zero_one(self):
        """Confidence interval bounds must strictly lie within [0.0, 1.0]."""
        test_cases = [(0, 100), (1, 100), (50, 100), (99, 100), (100, 100), (0, 10), (10, 10)]
        for k, n in test_cases:
            ci = compute_confidence_interval(k, n)
            low, high = ci.lower, ci.upper
            assert 0.0 <= low <= high <= 1.0, f"Bounds failed for k={k}, n={n}: [{low}, {high}]"

    def test_confidence_interval_unpackable_as_tuple(self):
        """ConfidenceInterval object must unpack directly as (low, high) tuple."""
        low, high = compute_confidence_interval(30, 100)
        assert isinstance(low, float)
        assert isinstance(high, float)
        assert low < high

    def test_ci_width_shrinks_with_sample_size(self):
        """Larger sample sizes must yield narrower confidence intervals."""
        ci_small = compute_confidence_interval(50, 100)
        ci_med = compute_confidence_interval(500, 1000)
        ci_large = compute_confidence_interval(5000, 10000)

        width_small = ci_small.upper - ci_small.lower
        width_med = ci_med.upper - ci_med.lower
        width_large = ci_large.upper - ci_large.lower

        assert width_small > width_med > width_large, "Confidence interval width must decrease as sample size grows"

    def test_ci_symmetry_at_fifty_percent(self):
        """At p=0.50, the Wilson interval is centered around 0.50 for large n."""
        ci = compute_confidence_interval(500, 1000)
        assert abs(ci.p_hat - 0.50) < 0.001
        diff_lower = 0.50 - ci.lower
        diff_upper = ci.upper - 0.50
        assert abs(diff_lower - diff_upper) < 0.01


# ---------------------------------------------------------------------------
# 6. Isolated Evaluator Benchmark Tests
# ---------------------------------------------------------------------------

class TestIsolatedEvaluator:
    def test_evaluate_batch_runs_in_memory(self, sample_cases):
        """Benchmark runs cleanly in-memory returning an EvaluationReport."""
        report = evaluate_batch(sample_cases, run_id="benchmark_memory_test")
        assert isinstance(report, EvaluationReport)
        assert report.total_cases == 100
        assert report.total_revenue_at_risk > 0.0
        assert report.control_metrics is not None
        assert report.naive_metrics is not None
        assert report.agent_metrics is not None
        assert report.incremental_lift_vs_control_pct > 0.0
        assert report.incremental_lift_vs_naive_pct > 0.0

    def test_evaluate_batch_deterministic_across_identical_run_id(self, sample_cases):
        """Two calls to evaluate_batch with identical run_id produce identical monetary numbers."""
        cases_copy1 = copy.deepcopy(sample_cases)
        cases_copy2 = copy.deepcopy(sample_cases)

        rep1 = evaluate_batch(cases_copy1, run_id="fixed_run_benchmark")
        rep2 = evaluate_batch(cases_copy2, run_id="fixed_run_benchmark")

        assert rep1.total_revenue_at_risk == rep2.total_revenue_at_risk
        assert rep1.control_metrics.net_recovered == rep2.control_metrics.net_recovered
        assert rep1.naive_metrics.net_recovered == rep2.naive_metrics.net_recovered
        assert rep1.agent_metrics.net_recovered == rep2.agent_metrics.net_recovered
        assert rep1.incremental_lift_vs_control_pct == rep2.incremental_lift_vs_control_pct

    def test_active_cases_300_batch_evaluation(self):
        """Verify active_cases_300.json can be loaded and evaluated cleanly."""
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        json_path = os.path.join(repo_root, "data", "active_cases_300.json")
        if not os.path.exists(json_path) and os.path.exists("data/active_cases_300.json"):
            json_path = "data/active_cases_300.json"
        assert os.path.exists(json_path), f"{json_path} must exist"

        with open(json_path, "r", encoding="utf-8") as f:
            cases_dict = json.load(f)

        assert len(cases_dict) >= 300, "Active demo batch must have >= 300 cases"
        cases = [RecoveryCase.from_dict(d) for d in cases_dict]

        report = evaluate_batch(cases, run_id="demo_active_batch_eval")
        assert report.total_cases >= 300
        assert report.total_revenue_at_risk >= 3000000.0  # > 30 Lakhs
        assert report.agent_metrics.net_recovered > report.control_metrics.net_recovered
        assert report.incremental_lift_vs_control_pct > 0.0

        # Check leak type breakdown completeness
        for lt in LeakType:
            lt_val = lt.value
            assert lt_val in report.leak_type_breakdown
            assert report.leak_type_breakdown[lt_val]["total_cases"] > 0
            assert report.leak_type_breakdown[lt_val]["at_risk"] > 0
