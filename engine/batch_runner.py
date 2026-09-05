"""
Batch runner: runs the full agent over failed transactions and compares
recovered revenue against two baselines:
  - do_nothing: no recovery action taken
  - naive_retry: always immediately retry, regardless of root cause

This produces the money slide: $ recovered and % lift over baseline.
"""
import random
import pandas as pd
from engine.policy import PolicyEngine
from engine.recovery_value import RECOVERY_PROB_TABLE


def run_batch(df_failed: pd.DataFrame, seed=7):
    random.seed(seed)
    policy = PolicyEngine()
    invoice_state = {}

    results = []
    for _, row in df_failed.iterrows():
        txn = row.to_dict()
        invoice_id = txn["txn_id"]
        state = invoice_state.setdefault(invoice_id, {"attempts": 0, "opt_out": False})

        decision = policy.evaluate(txn, state)
        state["attempts"] = decision.attempt_number

        results.append({
            "txn_id": invoice_id,
            "amount": txn["amount"],
            "root_cause": decision.root_cause,
            "chosen_action": decision.chosen_action,
            "expected_value": decision.expected_value,
            "recovered_agent": getattr(decision, "recovered", False),
            "revenue_recovered_agent": getattr(decision, "revenue_recovered", 0),
        })

    out = pd.DataFrame(results)

    def naive_outcome(row):
        p = RECOVERY_PROB_TABLE.get(row["root_cause"], {}).get("retry_immediate", 0.1)
        return random.random() < p

    out["recovered_naive"] = out.apply(naive_outcome, axis=1)
    out["revenue_recovered_naive"] = out.apply(
        lambda r: r["amount"] if r["recovered_naive"] else 0, axis=1
    )

    summary = {
        "total_at_risk_revenue": round(out["amount"].sum(), 2),
        "recovered_do_nothing": 0.0,
        "recovered_naive_retry": round(out["revenue_recovered_naive"].sum(), 2),
        "recovered_agent": round(out["revenue_recovered_agent"].sum(), 2),
    }
    summary["lift_over_naive_pct"] = round(
        100 * (summary["recovered_agent"] - summary["recovered_naive_retry"])
        / max(summary["recovered_naive_retry"], 1), 1
    )
    return out, summary


if __name__ == "__main__":
    df = pd.read_csv("data/transactions.csv")
    df_failed = df[df["failed"]].copy()
    df_failed["is_salary_week"] = df_failed["is_salary_week"].astype(bool)
    out, summary = run_batch(df_failed)
    print(summary)
    out.to_csv("data/batch_results.csv", index=False)
    print(f"\nSaved {len(out)} decisions to batch_results.csv")