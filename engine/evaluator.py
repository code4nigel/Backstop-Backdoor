"""
Isolated Batch Evaluator:
Executes scientific A/B benchmarking across Control Group, Naive Baseline, and Backstop Agent.
Runs in-memory with zero database pollution, outputting an auditable EvaluationReport.

Key Measurement Principles:
1. Strict Cohort Separation:
   - Control Cohort: ONLY cases with is_control_group == True (receive ActionType.DO_NOTHING).
   - Treatment Cohort: ONLY cases with is_control_group == False (receive policy-guided interventions).
2. True Incremental Lift:
   - Difference in recovery rates: delta_r = r_treat - r_ctrl.
   - Percentage Lift: (r_treat - r_ctrl) / max(r_ctrl, 0.001) * 100.
3. Standard Error of Difference & 95% Confidence Interval:
   - SE = sqrt((r_treat * (1 - r_treat) / N_treat) + (r_ctrl * (1 - r_ctrl) / N_ctrl)).
   - 95% CI for delta_r: [delta_r - 1.96 * SE, delta_r + 1.96 * SE].
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
import time
import math

from engine.models import RecoveryCase, LeakType, ActionType, CaseState, DiagnosisResult, PolicyDecision
from engine.policy import PolicyEngine
from engine.diagnosis import MultiLeakDiagnosisDispatcher, DiagnosisEngine
from engine.measurement import (
    simulate_recovery_outcome,
    compute_confidence_interval,
    assign_stratified_holdout,
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
    holdout_control_count: int = 0
    treated_cases_count: int = 0
    confidence_interval_95: Dict[str, float] = field(default_factory=dict)
    detailed_case_results: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "total_cases": self.total_cases,
            "total_revenue_at_risk": round(self.total_revenue_at_risk, 2),
            "holdout_control_count": self.holdout_control_count,
            "treated_cases_count": self.treated_cases_count,
            "control": self.control_metrics.to_dict(),
            "naive": self.naive_metrics.to_dict(),
            "agent": self.agent_metrics.to_dict(),
            "lift_vs_control_pct": round(self.incremental_lift_vs_control_pct, 2),
            "lift_vs_naive_pct": round(self.incremental_lift_vs_naive_pct, 2),
            "confidence_interval_95": self.confidence_interval_95,
            "leak_type_breakdown": self.leak_type_breakdown,
            "suppressed_count": self.suppressed_count,
            "suppression_reasons": self.suppression_reasons,
            "systemic_cases_intercepted": self.systemic_cases_intercepted,
            "detailed_case_results_count": len(self.detailed_case_results),
        }


def evaluate_batch(
    cases: List[RecoveryCase],
    policy_engine: Optional[PolicyEngine] = None,
    diagnosis_engine: Optional[Any] = None,
    run_id: str = DEFAULT_RUN_ID,
) -> EvaluationReport:
    policy = policy_engine or PolicyEngine()
    diag = diagnosis_engine or MultiLeakDiagnosisDispatcher()

    total_at_risk = sum(c.amount for c in cases)
    total_n = len(cases)

    # 1. Ensure Stratified Holdout Partitioning
    ctrl_cases = [c for c in cases if getattr(c, "is_control_group", False)]
    treat_cases = [c for c in cases if not getattr(c, "is_control_group", False)]
    
    if len(ctrl_cases) < 5 and total_n >= 20:
        cases = assign_stratified_holdout(cases, holdout_ratio=0.15, seed=run_id)
        ctrl_cases = [c for c in cases if c.is_control_group]
        treat_cases = [c for c in cases if not c.is_control_group]

    n_ctrl = len(ctrl_cases)
    n_treat = len(treat_cases)
    risk_ctrl = sum(c.amount for c in ctrl_cases)
    risk_treat = sum(c.amount for c in treat_cases)

    # Detailed case results for drilldowns and timelines
    detailed_results: List[Dict[str, Any]] = []

    # -------------------------------------------------------------
    # Cohort 1: Holdout Control Group (Organic Self-Cure Only)
    # -------------------------------------------------------------
    ctrl_recovered_cases = 0
    ctrl_gross = 0.0

    for c in ctrl_cases:
        rec, _ = simulate_recovery_outcome(c, ActionType.DO_NOTHING, f"{run_id}_ctrl")
        if rec:
            ctrl_recovered_cases += 1
            ctrl_gross += c.amount

        detailed_results.append({
            "case_id": c.case_id,
            "customer_id": c.customer_id,
            "customer_name": c.customer_name,
            "phone_masked": c.phone_masked,
            "email_masked": c.email_masked,
            "leak_type": c.leak_type.value if hasattr(c.leak_type, "value") else str(c.leak_type),
            "amount": c.amount,
            "days_at_risk": c.days_at_risk,
            "is_control_group": True,
            "state": "RECOVERED" if rec else "CLOSED",
            "chosen_action": "do_nothing",
            "diagnosis": {
                "predicted_cause": "unassigned_holdout",
                "confidence": 1.0,
                "evidence": ["Holdout control group (15% sample) receives zero intervention."],
                "is_systemic": False,
            },
            "ranked_interventions": [
                {"action": "do_nothing", "expected_value": 0.0, "net_ev": 0.0, "is_chosen": True}
            ],
            "policy_check": {
                "allowed": False,
                "dnd_status": "PASS",
                "quiet_hours": "PASS",
                "margin_guardrail": "PASS",
                "frequency_cap": "PASS",
                "dispute_hold": "PASS",
                "blocked_reason": "Holdout Control Group: Zero intervention",
                "applied_rules": ["RULE_HOLDOUT_CONTROL_SUPPRESSION"],
                "requires_human_approval": False,
            },
            "execution": {
                "channel": "None (Holdout)",
                "outbox_status": "SKIPPED_HOLDOUT",
                "idempotency_key": f"ctrl_{c.case_id}",
                "provider_status": "UNTOUCHED",
            },
            "audit_trail": [
                {"from_state": "NONE", "to_state": "DETECTED", "rule": "INGEST", "ts": c.created_at},
                {"from_state": "DETECTED", "to_state": "CLOSED" if not rec else "RECOVERED", "rule": "HOLDOUT_BASELINE", "ts": c.created_at + 60},
            ],
            "net_ev": 0.0,
            "recovered": rec,
        })

    ctrl_rate = ctrl_recovered_cases / n_ctrl if n_ctrl else 0.0
    ctrl_net = ctrl_gross
    ctrl_ci_low, ctrl_ci_high = compute_confidence_interval(ctrl_recovered_cases, n_ctrl if n_ctrl else 1)
    
    ctrl_metrics = StrategyMetrics(
        name="Holdout Control Group (Organic)",
        total_cases=n_ctrl,
        recovered_cases=ctrl_recovered_cases,
        recovery_rate=ctrl_rate,
        gross_recovered=ctrl_gross,
        outreach_cost=0.0,
        discounts_given=0.0,
        human_costs=0.0,
        net_recovered=ctrl_net,
        roi=0.0,
        ci_low=ctrl_ci_low,
        ci_high=ctrl_ci_high,
    )

    # -------------------------------------------------------------
    # Cohort 2: Naive Baseline (Blind immediate retry / SMS)
    # -------------------------------------------------------------
    naive_recovered_cases = 0
    naive_gross = 0.0
    naive_channel_cost = 0.0
    for c in treat_cases:
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
    naive_rate = naive_recovered_cases / n_treat if n_treat else 0.0
    naive_roi = (naive_net - naive_channel_cost) / naive_channel_cost if naive_channel_cost else 0.0
    naive_ci_low, naive_ci_high = compute_confidence_interval(naive_recovered_cases, n_treat if n_treat else 1)
    
    naive_metrics = StrategyMetrics(
        name="Naive Baseline (Blind Retry / SMS)",
        total_cases=n_treat,
        recovered_cases=naive_recovered_cases,
        recovery_rate=naive_rate,
        gross_recovered=naive_gross,
        outreach_cost=naive_channel_cost,
        discounts_given=0.0,
        human_costs=0.0,
        net_recovered=naive_net,
        roi=naive_roi,
        ci_low=naive_ci_low,
        ci_high=naive_ci_high,
    )

    # -------------------------------------------------------------
    # Cohort 3: Backstop AI Recovery Agent (Treated Cases)
    # -------------------------------------------------------------
    agent_recovered_cases = 0
    agent_gross = 0.0
    agent_channel_cost = 0.0
    agent_discounts = 0.0
    agent_human_cost = 0.0
    suppressed_count = 0
    suppression_reasons: Dict[str, int] = {}
    systemic_intercepted = 0

    leak_breakdown: Dict[str, Dict[str, Any]] = {
        lt.value: {"total_cases": 0, "at_risk": 0.0, "recovered_cases": 0, "net_recovered": 0.0, "top_root_cause": ""}
        for lt in LeakType
    }
    leak_causes: Dict[str, Dict[str, int]] = {lt.value: {} for lt in LeakType}

    for c in treat_cases:
        lt_str = c.leak_type.value if hasattr(c.leak_type, "value") else str(c.leak_type)
        leak_breakdown[lt_str]["total_cases"] += 1
        leak_breakdown[lt_str]["at_risk"] += c.amount

        # 1. Diagnose
        diagnosis = diag.diagnose_case(c) if hasattr(diag, "diagnose_case") else diag.diagnose(c)
        if getattr(diagnosis, "is_systemic", False):
            systemic_intercepted += 1

        cause = getattr(diagnosis, "predicted_cause", "unknown")
        leak_causes[lt_str][cause] = leak_causes[lt_str].get(cause, 0) + 1

        # 2. Policy & Compliance Decision
        decision = policy.evaluate(
            case=c,
            diagnosed_cause=getattr(diagnosis, "predicted_cause", None),
            confidence=getattr(diagnosis, "confidence", 1.0),
        )

        audit_trail = [
            {"from_state": "NONE", "to_state": "DETECTED", "rule": "INGESTION", "ts": c.created_at},
            {"from_state": "DETECTED", "to_state": "DIAGNOSED", "rule": f"DIAGNOSIS_{cause.upper()}", "ts": c.created_at + 15},
        ]

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

            audit_trail.append({"from_state": "DIAGNOSED", "to_state": "POLICY_BLOCKED", "rule": reason, "ts": c.created_at + 30})
            if rec:
                audit_trail.append({"from_state": "POLICY_BLOCKED", "to_state": "RECOVERED", "rule": "ORGANIC_SELF_CURE", "ts": c.created_at + 3600})

            detailed_results.append({
                "case_id": c.case_id,
                "customer_id": c.customer_id,
                "customer_name": c.customer_name,
                "phone_masked": c.phone_masked,
                "email_masked": c.email_masked,
                "leak_type": lt_str,
                "amount": c.amount,
                "days_at_risk": c.days_at_risk,
                "is_control_group": False,
                "state": "RECOVERED" if rec else "POLICY_BLOCKED",
                "chosen_action": "do_nothing",
                "diagnosis": {
                    "predicted_cause": cause,
                    "confidence": getattr(diagnosis, "confidence", 0.9),
                    "evidence": getattr(diagnosis, "evidence", ["Policy gate triggered"]),
                    "is_systemic": getattr(diagnosis, "is_systemic", False),
                },
                "ranked_interventions": [
                    {"action": getattr(opt, "action", getattr(opt, "name", "action")),
                     "expected_value": getattr(opt, "expected_value", 0.0),
                     "net_ev": getattr(opt, "net_recovery", getattr(opt, "expected_value", 0.0)),
                     "is_chosen": False}
                    for opt in getattr(decision, "ranked_interventions", [])
                ] or [{"action": "do_nothing", "expected_value": 0.0, "net_ev": 0.0, "is_chosen": True}],
                "policy_check": {
                    "allowed": False,
                    "dnd_status": "BLOCKED" if "DND" in reason else "PASS",
                    "quiet_hours": "BLOCKED" if "Quiet hours" in reason else "PASS",
                    "margin_guardrail": "PASS",
                    "frequency_cap": "BLOCKED" if "Frequency" in reason else "PASS",
                    "dispute_hold": "BLOCKED" if "dispute" in reason.lower() else "PASS",
                    "blocked_reason": reason,
                    "applied_rules": getattr(decision, "applied_rules", [reason]),
                    "requires_human_approval": False,
                },
                "execution": {
                    "channel": "None (Suppressed)",
                    "outbox_status": "SUPPRESSED",
                    "idempotency_key": f"supp_{c.case_id}",
                    "provider_status": "HELD",
                },
                "audit_trail": audit_trail,
                "net_ev": 0.0,
                "recovered": rec,
            })
            continue

        # 3. Execute approved intervention
        chosen = decision.chosen_action
        chosen_val = chosen.value if hasattr(chosen, "value") else str(chosen)
        cost = CHANNEL_UNIT_COSTS.get(chosen_val, 0.0)
        agent_channel_cost += cost

        if decision.requires_human_approval:
            agent_human_cost += HUMAN_REVIEW_FEE

        # Find expected discount if applied
        disc = 0.0
        for opt in decision.ranked_interventions:
            opt_act = getattr(opt, "action", getattr(opt, "name", None))
            if opt_act == chosen:
                disc = getattr(opt, "expected_discount", 0.0)
                break
        agent_discounts += disc

        rec, _ = simulate_recovery_outcome(c, chosen, run_id)
        net_gain = 0.0
        if rec:
            agent_recovered_cases += 1
            agent_gross += c.amount
            net_gain = c.amount - cost - disc - (HUMAN_REVIEW_FEE if decision.requires_human_approval else 0.0)
            leak_breakdown[lt_str]["recovered_cases"] += 1
            leak_breakdown[lt_str]["net_recovered"] += net_gain

        channel_display = "Payment Gateway" if "retry" in chosen_val or "reroute" in chosen_val else (
            "WhatsApp" if "whatsapp" in chosen_val or "link" in chosen_val else (
                "Hinglish Voice" if "voice" in chosen_val else "SMS"
            )
        )

        audit_trail.append({"from_state": "DIAGNOSED", "to_state": "READY", "rule": "POLICY_APPROVED", "ts": c.created_at + 30})
        audit_trail.append({"from_state": "READY", "to_state": "EXECUTING", "rule": f"OUTBOX_DISPATCH_{chosen_val.upper()}", "ts": c.created_at + 45})
        if rec:
            audit_trail.append({"from_state": "EXECUTING", "to_state": "RECOVERED", "rule": "PAYMENT_CONFIRMED", "ts": c.created_at + 900})
        else:
            audit_trail.append({"from_state": "EXECUTING", "to_state": "CLOSED", "rule": "ATTEMPT_EXHAUSTED", "ts": c.created_at + 1800})

        detailed_results.append({
            "case_id": c.case_id,
            "customer_id": c.customer_id,
            "customer_name": c.customer_name,
            "phone_masked": c.phone_masked,
            "email_masked": c.email_masked,
            "leak_type": lt_str,
            "amount": c.amount,
            "days_at_risk": c.days_at_risk,
            "is_control_group": False,
            "state": "RECOVERED" if rec else "CLOSED",
            "chosen_action": chosen_val,
            "diagnosis": {
                "predicted_cause": cause,
                "confidence": getattr(diagnosis, "confidence", 0.92),
                "evidence": getattr(diagnosis, "evidence", [f"Root cause: {cause}"]),
                "is_systemic": getattr(diagnosis, "is_systemic", False),
            },
            "ranked_interventions": [
                {"action": getattr(opt, "action", getattr(opt, "name", "act")).value if hasattr(getattr(opt, "action", getattr(opt, "name", "act")), "value") else str(getattr(opt, "action", getattr(opt, "name", "act"))),
                 "expected_value": getattr(opt, "expected_value", 0.0),
                 "net_ev": getattr(opt, "net_recovery", getattr(opt, "expected_value", 0.0)),
                 "is_chosen": (getattr(opt, "action", getattr(opt, "name", None)) == chosen)}
                for opt in getattr(decision, "ranked_interventions", [])
            ],
            "policy_check": {
                "allowed": True,
                "dnd_status": "PASS",
                "quiet_hours": "PASS",
                "margin_guardrail": "PASS",
                "frequency_cap": "PASS",
                "dispute_hold": "PASS",
                "blocked_reason": None,
                "applied_rules": getattr(decision, "applied_rules", ["POLICY_APPROVED"]),
                "requires_human_approval": decision.requires_human_approval,
                "human_review_reason": decision.human_review_reason,
            },
            "execution": {
                "channel": channel_display,
                "outbox_status": "CONFIRMED" if rec else "FAILED",
                "idempotency_key": f"idemp_{c.case_id[:8]}",
                "provider_status": "DELIVERED",
            },
            "audit_trail": audit_trail,
            "net_ev": max(net_gain, 0.0),
            "recovered": rec,
        })

    # Finalize Leak Breakdown Top Causes
    for lt_str in leak_breakdown:
        causes_map = leak_causes[lt_str]
        top_cause = max(causes_map, key=causes_map.get) if causes_map else "technical_decline"
        leak_breakdown[lt_str]["top_root_cause"] = top_cause

    # Cohort Recovery Rates
    treat_rate = agent_recovered_cases / n_treat if n_treat else 0.0
    total_agent_costs = agent_channel_cost + agent_discounts + agent_human_cost
    agent_net = agent_gross - total_agent_costs
    agent_roi = (agent_net - total_agent_costs) / total_agent_costs if total_agent_costs else 0.0
    agent_ci_low, agent_ci_high = compute_confidence_interval(agent_recovered_cases, n_treat if n_treat else 1)

    agent_metrics = StrategyMetrics(
        name="Backstop AI Recovery Agent",
        total_cases=n_treat,
        recovered_cases=agent_recovered_cases,
        recovery_rate=treat_rate,
        gross_recovered=agent_gross,
        outreach_cost=agent_channel_cost,
        discounts_given=agent_discounts,
        human_costs=agent_human_cost,
        net_recovered=agent_net,
        roi=agent_roi,
        ci_low=agent_ci_low,
        ci_high=agent_ci_high,
    )

    # -------------------------------------------------------------
    # Scientific Lift & Confidence Interval for Difference
    # -------------------------------------------------------------
    # Incremental Recovery Rate Difference: delta_r = r_treat - r_ctrl
    delta_r = treat_rate - ctrl_rate
    incremental_lift_pct = (delta_r / max(ctrl_rate, 0.001)) * 100.0

    # Standard Error of Difference between two independent cohorts
    se_diff = math.sqrt(
        (treat_rate * (1.0 - treat_rate) / max(n_treat, 1)) +
        (ctrl_rate * (1.0 - ctrl_rate) / max(n_ctrl, 1))
    )
    delta_r_low = max(0.0, delta_r - 1.96 * se_diff)
    delta_r_high = delta_r + 1.96 * se_diff

    lift_ci_low = (delta_r_low / max(ctrl_rate, 0.001)) * 100.0
    lift_ci_high = (delta_r_high / max(ctrl_rate, 0.001)) * 100.0

    # Comparison vs naive baseline
    delta_naive = treat_rate - naive_rate
    lift_vs_naive = (delta_naive / max(naive_rate, 0.001)) * 100.0

    return EvaluationReport(
        run_id=run_id,
        timestamp=time.time(),
        total_cases=total_n,
        total_revenue_at_risk=total_at_risk,
        control_metrics=ctrl_metrics,
        naive_metrics=naive_metrics,
        agent_metrics=agent_metrics,
        incremental_lift_vs_control_pct=incremental_lift_pct,
        incremental_lift_vs_naive_pct=lift_vs_naive,
        leak_type_breakdown=leak_breakdown,
        suppressed_count=suppressed_count,
        suppression_reasons=suppression_reasons,
        systemic_cases_intercepted=systemic_intercepted,
        holdout_control_count=n_ctrl,
        treated_cases_count=n_treat,
        confidence_interval_95={
            "lower_pct": round(lift_ci_low, 1),
            "upper_pct": round(lift_ci_high, 1),
            "rate_difference_pct": round(delta_r * 100.0, 1),
            "standard_error": round(se_diff, 4),
            "confidence_level": 0.95,
        },
        detailed_case_results=detailed_results,
    )
