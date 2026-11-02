"""
Batch Runner with Crash-Resume Guarantees:
Executes recovery cases through the Transactional Outbox and Recovery Ledger.
Provides idempotent resumption and verifiable financial consistency.
"""
from typing import List, Dict, Any, Optional, Tuple
import time

from engine.models import RecoveryCase, ActionType, CaseState, LeakType
from engine.ledger import RecoveryLedger
from engine.execution.outbox import TransactionalOutbox
from engine.execution.providers import PROVIDER_LOG
from engine.systemic import SystemicDetector
from engine.diagnosis import DiagnosisEngine
from engine.policy import PolicyEngine
from engine.measurement import simulate_recovery_outcome
from engine.config import CHANNEL_UNIT_COSTS, HUMAN_REVIEW_FEE, DEFAULT_RUN_ID


class MidBatchCrashException(Exception):
    """Simulated crash during mid-batch execution."""
    pass


def run_batch(
    cases: List[RecoveryCase],
    run_id: str = DEFAULT_RUN_ID,
    ledger: Optional[RecoveryLedger] = None,
    outbox: Optional[TransactionalOutbox] = None,
    policy_engine: Optional[PolicyEngine] = None,
    diagnosis_engine: Optional[DiagnosisEngine] = None,
    kill_at_case: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Executes a batch of cases with idempotency and crash recovery support.
    If kill_at_case is passed, abruptly terminates with MidBatchCrashException at that index.
    """
    ledger = ledger or RecoveryLedger()
    outbox = outbox or TransactionalOutbox(ledger)
    policy = policy_engine or PolicyEngine()
    diag = diagnosis_engine or DiagnosisEngine()

    ledger.create_run(run_id, description=f"Batch execution for {len(cases)} cases")

    processed_cases = []
    skipped_count = 0
    executed_count = 0
    gross_recovered = 0.0
    channel_costs = 0.0
    discounts_given = 0.0
    human_costs = 0.0

    for idx, case in enumerate(cases, start=1):
        # 1. Simulate Mid-Batch Kill if requested
        if kill_at_case is not None and idx == kill_at_case:
            # Leave this case in flight before raising crash
            raise MidBatchCrashException(f"Abrupt process termination at case #{idx} ({case.case_id})")

        # 2. Control Group (Organic only)
        if case.is_control_group:
            rec, _ = simulate_recovery_outcome(case, ActionType.DO_NOTHING, run_id)
            if rec:
                gross_recovered += case.amount
            processed_cases.append({
                "case_id": case.case_id,
                "status": "CONTROL_GROUP",
                "recovered": rec,
                "amount": case.amount,
            })
            continue

        # 3. Multi-Leak Diagnosis
        diagnosis = diag.diagnose_case(case)

        # 4. Deterministic Policy Gate
        decision = policy.evaluate(case, diagnosis.predicted_cause, diagnosis.confidence)
        if not decision.allowed:
            rec, _ = simulate_recovery_outcome(case, ActionType.DO_NOTHING, run_id)
            if rec:
                gross_recovered += case.amount
            processed_cases.append({
                "case_id": case.case_id,
                "status": "POLICY_BLOCKED",
                "reason": decision.blocked_reason,
                "recovered": rec,
                "amount": case.amount,
            })
            continue

        chosen_action = decision.chosen_action
        attempt_number = case.metadata.get("prior_failures", 0) + 1

        # 5. Idempotent Begin in Ledger
        key, should_execute, existing_state = ledger.begin_action(
            run_id=run_id,
            customer_id=case.customer_id,
            case_id=case.case_id,
            attempt_number=attempt_number,
            action=chosen_action.value,
            root_cause=diagnosis.predicted_cause,
            expected_value=case.amount,
            detail={"diagnosed_cause": diagnosis.predicted_cause},
        )

        if not should_execute:
            # Idempotent Skip!
            skipped_count += 1
            # Account for costs and revenue of the previously executed action
            cost = CHANNEL_UNIT_COSTS.get(chosen_action.value, 0.0)
            channel_costs += cost
            if decision.requires_human_approval:
                human_costs += HUMAN_REVIEW_FEE

            disc = 0.0
            if chosen_action == ActionType.DISCOUNT_INCENTIVE:
                disc = min(case.amount * 0.08, 500.0)
                discounts_given += disc

            rec, _ = simulate_recovery_outcome(case, chosen_action, run_id)
            if rec:
                gross_recovered += case.amount

            processed_cases.append({
                "case_id": case.case_id,
                "status": "SKIPPED",
                "action": chosen_action.value,
                "existing_state": existing_state,
                "recovered": rec,
                "amount": case.amount,
            })
            continue

        # 6. Dispatch via Outbox
        outbox_key, outbox_state, result = outbox.dispatch_action(
            run_id=run_id,
            case=case,
            action=chosen_action,
            attempt_number=attempt_number,
        )

        if outbox_state == "SKIPPED":
            skipped_count += 1
            cost = CHANNEL_UNIT_COSTS.get(chosen_action.value, 0.0)
            channel_costs += cost
            if decision.requires_human_approval:
                human_costs += HUMAN_REVIEW_FEE

            disc = 0.0
            if chosen_action == ActionType.DISCOUNT_INCENTIVE:
                disc = min(case.amount * 0.08, 500.0)
                discounts_given += disc

            rec, _ = simulate_recovery_outcome(case, chosen_action, run_id)
            if rec:
                gross_recovered += case.amount

            processed_cases.append({
                "case_id": case.case_id,
                "status": "SKIPPED",
                "action": chosen_action.value,
                "recovered": rec,
                "amount": case.amount,
            })
            continue

        # 7. Complete Action & Outcome Scoring
        executed_count += 1
        ledger.complete_action(run_id, key, success=True, result_detail=result)
        ledger.log_audit_event(
            run_id=run_id,
            case_id=case.case_id,
            from_state="READY",
            to_state="EXECUTING",
            rule_or_action=chosen_action.value,
            details=result,
        )

        cost = CHANNEL_UNIT_COSTS.get(chosen_action.value, 0.0)
        channel_costs += cost
        if decision.requires_human_approval:
            human_costs += HUMAN_REVIEW_FEE

        # Expected discount if discount_incentive was chosen
        disc = 0.0
        if chosen_action == ActionType.DISCOUNT_INCENTIVE:
            disc = min(case.amount * 0.08, 500.0)
            discounts_given += disc

        rec, _ = simulate_recovery_outcome(case, chosen_action, run_id)
        if rec:
            gross_recovered += case.amount

        processed_cases.append({
            "case_id": case.case_id,
            "status": "EXECUTED",
            "action": chosen_action.value,
            "recovered": rec,
            "amount": case.amount,
        })

    net_recovered = gross_recovered - channel_costs - discounts_given - human_costs

    return {
        "run_id": run_id,
        "total_cases": len(cases),
        "executed_count": executed_count,
        "skipped_count": skipped_count,
        "gross_recovered": round(gross_recovered, 2),
        "channel_costs": round(channel_costs, 2),
        "discounts_given": round(discounts_given, 2),
        "human_costs": round(human_costs, 2),
        "net_recovered": round(net_recovered, 2),
        "processed_cases": processed_cases,
    }
