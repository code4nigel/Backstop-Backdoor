"""
Isolated Batch Evaluator:
Executes scientific A/B benchmarking across Control Group, Naive Baseline, and Backstop Agent.
Runs in-memory with zero database pollution, outputting an auditable EvaluationReport.
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
import time

from engine.models import RecoveryCase, LeakType, ActionType, CaseState
from engine.policy import PolicyEngine
from engine.diagnosis import DiagnosisEngine
from engine.measurement import (
    simulate_recovery_outcome,
    compute_confidence_interval,
    StrategyMetrics,
)
from engine.config import CHANNEL_UNIT_COSTS, HUMAN_REVIEW_FEE, DEFAULT_RUN_ID


@dataclass
class EvaluationReport:
    run_id: str
    timestamp: float
    total_cases: int
    total_revenue_at_risk: float
    control_metrics: StrategyMetrics
    naive_metrics: StrategyMetrics
    agent_metrics: StrategyMetrics
    incremental_lift_vs_control_pct: float
    incremental_lift_vs_naive_pct: float
    leak_type_breakdown: Dict[str, Dict[str, Any]]
    suppressed_count: int
    suppression_reasons: Dict[str, int]
    systemic_cases_intercepted: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "total_cases": self.total_cases,
            "total_revenue_at_risk": round(self.total_revenue_at_risk, 2),
            "control": self.control_metrics.to_dict(),
            "naive": self.naive_metrics.to_dict(),
            "agent": self.agent_metrics.to_dict(),
            "lift_vs_control_pct": round(self.incremental_lift_vs_control_pct, 2),
            "lift_vs_naive_pct": round(self.incremental_lift_vs_naive_pct, 2),
            "leak_type_breakdown": self.leak_type_breakdown,
            "suppressed_count": self.suppressed_count,
            "suppression_reasons": self.suppression_reasons,
            "systemic_cases_intercepted": self.systemic_cases_intercepted,
        }


def evaluate_batch(
    cases: List[RecoveryCase],
    policy_engine: Optional[PolicyEngine] = None,
    diagnosis_engine: Optional[DiagnosisEngine] = None,
    run_id: str = DEFAULT_RUN_ID,
) -> EvaluationReport:
    policy = policy_engine or PolicyEngine()
    diag = diagnosis_engine or DiagnosisEngine()

    total_at_risk = sum(c.amount for c in cases)
    total_n = len(cases)

    # Strategy 0: Control Group (Organic only)
    ctrl_recovered_cases = 0
    ctrl_gross = 0.0
    for c in cases:
        rec, _ = simulate_recovery_outcome(c, ActionType.DO_NOTHING, f"{run_id}_ctrl")
        if rec:
            ctrl_recovered_cases += 1
            ctrl_gross += c.amount

    ctrl_net = ctrl_gross  # Zero costs for control
    ctrl_ci_low, ctrl_ci_high = compute_confidence_interval(ctrl_recovered_cases, total_n)
    ctrl_metrics = StrategyMetrics(
        name="Holdout Control Group (Organic)",
        total_cases=total_n,
        recovered_cases=ctrl_recovered_cases,
        recovery_rate=ctrl_recovered_cases / total_n if total_n else 0.0,
        gross_recovered=ctrl_gross,
        outreach_cost=0.0,
        discounts_given=0.0,
        human_costs=0.0,
        net_recovered=ctrl_net,
        roi=0.0,
        ci_low=ctrl_ci_low,
        ci_high=ctrl_ci_high,
    )

    # Strategy 1: Naive Baseline (Blind immediate retry for payments, generic SMS for carts/invoices)
    naive_recovered_cases = 0
    naive_gross = 0.0
    naive_channel_cost = 0.0
    for c in cases:
        if c.leak_type == LeakType.FAILED_PAYMENT:
            naive_action = ActionType.RETRY_IMMEDIATE
        elif c.leak_type == LeakType.ABANDONED_CHECKOUT:
            naive_action = ActionType.REMINDER_SMS
        elif c.leak_type == LeakType.SUBSCRIPTION_MANDATE:
            naive_action = ActionType.RETRY_IMMEDIATE
        else:
            naive_action = ActionType.REMINDER_SMS

        cost = CHANNEL_UNIT_COSTS.get(naive_action.value, 2.0)
        naive_channel_cost += cost
        rec, _ = simulate_recovery_outcome(c, naive_action, f"{run_id}_naive")
        if rec:
            naive_recovered_cases += 1
            naive_gross += c.amount

    naive_net = naive_gross - naive_channel_cost
    naive_roi = (naive_net - naive_channel_cost) / naive_channel_cost if naive_channel_cost else 0.0
    naive_ci_low, naive_ci_high = compute_confidence_interval(naive_recovered_cases, total_n)
    naive_metrics = StrategyMetrics(
        name="Naive Baseline (Blind Retry / SMS)",
        total_cases=total_n,
        recovered_cases=naive_recovered_cases,
        recovery_rate=naive_recovered_cases / total_n if total_n else 0.0,
        gross_recovered=naive_gross,
        outreach_cost=naive_channel_cost,
        discounts_given=0.0,
        human_costs=0.0,
        net_recovered=naive_net,
        roi=naive_roi,
        ci_low=naive_ci_low,
        ci_high=naive_ci_high,
    )

    # Strategy 2: Backstop Agent
    agent_recovered_cases = 0
    agent_gross = 0.0
    agent_channel_cost = 0.0
    agent_discounts = 0.0
    agent_human_cost = 0.0
    suppressed_count = 0
    suppression_reasons: Dict[str, int] = {}
    systemic_intercepted = 0

    leak_breakdown: Dict[str, Dict[str, Any]] = {
        lt.value: {"total_cases": 0, "at_risk": 0.0, "recovered_cases": 0, "net_recovered": 0.0}
        for lt in LeakType
    }

    for c in cases:
        lt_str = c.leak_type.value if isinstance(c.leak_type, LeakType) else c.leak_type
        leak_breakdown[lt_str]["total_cases"] += 1
        leak_breakdown[lt_str]["at_risk"] += c.amount

        # Control group check: If assigned to holdout, receive zero intervention
        if c.is_control_group:
            rec, _ = simulate_recovery_outcome(c, ActionType.DO_NOTHING, run_id)
            if rec:
                agent_recovered_cases += 1
                agent_gross += c.amount
                leak_breakdown[lt_str]["recovered_cases"] += 1
                leak_breakdown[lt_str]["net_recovered"] += c.amount
            continue

        # 1. Diagnose
        diagnosis = diag.diagnose_case(c)
        if diagnosis.is_systemic:
            systemic_intercepted += 1

        # 2. Policy & Compliance Decision
        decision = policy.evaluate(
            case=c,
            diagnosed_cause=diagnosis.predicted_cause,
            confidence=diagnosis.confidence,
        )

        if not decision.allowed:
            suppressed_count += 1
            reason = decision.blocked_reason or "Policy Suppressed"
            suppression_reasons[reason] = suppression_reasons.get(reason, 0) + 1
            # Organic cure check
            rec, _ = simulate_recovery_outcome(c, ActionType.DO_NOTHING, run_id)
            if rec:
                agent_recovered_cases += 1
                agent_gross += c.amount
                leak_breakdown[lt_str]["recovered_cases"] += 1
                leak_breakdown[lt_str]["net_recovered"] += c.amount
            continue

        # 3. Execute approved intervention
        chosen = decision.chosen_action
        cost = CHANNEL_UNIT_COSTS.get(chosen.value, 0.0)
        agent_channel_cost += cost

        if decision.requires_human_approval:
            agent_human_cost += HUMAN_REVIEW_FEE

        # Find expected discount if applied
        disc = 0.0
        for opt in decision.ranked_interventions:
            if opt.action == chosen:
                disc = opt.expected_discount
                break
        agent_discounts += disc

        rec, _ = simulate_recovery_outcome(c, chosen, run_id)
        if rec:
            agent_recovered_cases += 1
            agent_gross += c.amount
            net_gain = c.amount - cost - disc - (HUMAN_REVIEW_FEE if decision.requires_human_approval else 0.0)
            leak_breakdown[lt_str]["recovered_cases"] += 1
            leak_breakdown[lt_str]["net_recovered"] += net_gain

    total_agent_costs = agent_channel_cost + agent_discounts + agent_human_cost
    agent_net = agent_gross - total_agent_costs
    agent_roi = (agent_net - total_agent_costs) / total_agent_costs if total_agent_costs else 0.0
    agent_ci_low, agent_ci_high = compute_confidence_interval(agent_recovered_cases, total_n)

    agent_metrics = StrategyMetrics(
        name="Backstop AI Recovery Agent",
        total_cases=total_n,
        recovered_cases=agent_recovered_cases,
        recovery_rate=agent_recovered_cases / total_n if total_n else 0.0,
        gross_recovered=agent_gross,
        outreach_cost=agent_channel_cost,
        discounts_given=agent_discounts,
        human_costs=agent_human_cost,
        net_recovered=agent_net,
        roi=agent_roi,
        ci_low=agent_ci_low,
        ci_high=agent_ci_high,
    )

    # Scientific Lift Calculations
    lift_vs_control = ((agent_net - ctrl_net) / max(ctrl_net, 1.0)) * 100.0
    lift_vs_naive = ((agent_net - naive_net) / max(naive_net, 1.0)) * 100.0

    return EvaluationReport(
        run_id=run_id,
        timestamp=time.time(),
        total_cases=total_n,
        total_revenue_at_risk=total_at_risk,
        control_metrics=ctrl_metrics,
        naive_metrics=naive_metrics,
        agent_metrics=agent_metrics,
        incremental_lift_vs_control_pct=lift_vs_control,
        incremental_lift_vs_naive_pct=lift_vs_naive,
        leak_type_breakdown=leak_breakdown,
        suppressed_count=suppressed_count,
        suppression_reasons=suppression_reasons,
        systemic_cases_intercepted=systemic_intercepted,
    )
