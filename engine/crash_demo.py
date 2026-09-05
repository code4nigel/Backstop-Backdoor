"""
Live demo script: run this, let it process a few transactions, then hit
Ctrl+C mid-batch to simulate a crash. Run the exact same command again --
it picks up correctly, with zero duplicate actions.

Usage:
    python -m engine.crash_demo
(run it, Ctrl+C partway through, then run it again)
"""
import time
import pandas as pd
from engine.policy import PolicyEngine
from engine.ledger import RecoveryLedger

# This demo runs every invoice at a fixed attempt_number=1 for a single-pass
# batch. Because attempt_number never changes across restarts, any invoice
# already EXECUTED in the ledger produces the SAME idempotency_key on a
# re-run and gets correctly SKIPPED -- that skip is the thing to watch for.


def main():
    df = pd.read_csv("data/transactions.csv")
    df_failed = df[df["failed"]].copy()
    df_failed["is_salary_week"] = df_failed["is_salary_week"].astype(bool)

    ledger = RecoveryLedger()
    policy = PolicyEngine(ledger=ledger)

    print(f"Ledger summary BEFORE this run: {ledger.summary()}")
    print("Processing batch (Ctrl+C anytime to simulate a crash)...\n")

    for _, row in df_failed.iterrows():
        txn = row.to_dict()
        invoice_id = txn["txn_id"]
        state = {"attempts": 0, "opt_out": False}

        decision = policy.evaluate(txn, state)

        if decision.stopped and "already" in decision.stop_reason:
            tag = "SKIPPED (idempotent no-op)"
        elif decision.executed:
            tag = "EXECUTED"
        else:
            tag = "STOPPED"
        print(f"[{invoice_id}] action={decision.chosen_action:16s} state={tag:28s} EV=₹{decision.expected_value:,.0f}")

        time.sleep(0.15)

    print(f"\nLedger summary AFTER this run: {ledger.summary()}")


if __name__ == "__main__":
    main()