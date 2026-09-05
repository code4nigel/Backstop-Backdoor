"""
Synthetic transaction data generator for the Revenue Recovery engine.

IMPORTANT (say this to judges, don't hide it):
This dataset is generated from a controlled, documented ground-truth process,
not scraped or real. We inject KNOWN failure mechanisms below so that we can
train a model, then BACKTEST whether it recovers the known ground truth
(calibration), rather than claiming to have discovered real-world patterns
from a few thousand synthetic rows.

Ground-truth mechanisms injected (documented on purpose):
  - card_expiring: cards expiring within 30 days have elevated decline prob
  - bank_risk_tier: some issuing banks have higher soft-decline / 3DS friction rates
  - repeat_failure_history: customers with >=2 prior failures decline more (issuer fatigue)
  - insufficient_funds: correlated with day-of-month (salary cycles) and low account_balance_proxy
  - fraud_flag: rare, independent random risk flag with high decline correlation
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

RNG = np.random.default_rng(42)

DECLINE_CODES = [
    "insufficient_funds",
    "expired_card",
    "bank_risk_block",
    "3ds_friction",
    "fraud_flag",
]

BANK_RISK_TIERS = ["low", "medium", "high"]


def generate_transactions(n=6000, start_date="2026-01-01"):
    start = datetime.fromisoformat(start_date)
    rows = []

    for i in range(n):
        customer_id = f"cust_{RNG.integers(0, n // 3)}"  # repeat customers
        txn_date = start + timedelta(days=int(RNG.integers(0, 180)))
        day_of_month = txn_date.day

        amount = float(np.round(RNG.gamma(shape=3.0, scale=3500), 2))  # INR-ish
        bank_risk_tier = RNG.choice(BANK_RISK_TIERS, p=[0.6, 0.3, 0.1])
        days_to_card_expiry = int(RNG.integers(-10, 400))  # negative = already expired
        prior_failures = int(RNG.poisson(0.6))
        account_balance_proxy = float(RNG.normal(0, 1))  # standardized proxy signal
        is_salary_week = day_of_month <= 5

        # ---- ground-truth latent failure probability construction ----
        p_insufficient = 0.05
        if is_salary_week:
            p_insufficient -= 0.03
        else:
            p_insufficient += 0.04
        p_insufficient += max(0, -account_balance_proxy) * 0.06
        p_insufficient += prior_failures * 0.02

        p_expired = 0.03
        if 0 <= days_to_card_expiry <= 30:
            p_expired += 0.35
        elif days_to_card_expiry < 0:
            p_expired += 0.55

        p_bank_risk = {"low": 0.02, "medium": 0.08, "high": 0.20}[bank_risk_tier]
        p_bank_risk += prior_failures * 0.015

        p_3ds = 0.04 + (0.03 if bank_risk_tier == "high" else 0.0)

        p_fraud = 0.01 + (0.02 if prior_failures >= 3 else 0.0)

        cause_probs = np.array([p_insufficient, p_expired, p_bank_risk, p_3ds, p_fraud])
        cause_probs = cause_probs / cause_probs.sum()

        raw_fail_signal = (p_insufficient + p_expired + p_bank_risk + p_3ds + p_fraud) / 5
        p_fail = float(np.clip(raw_fail_signal * 1.3, 0.03, 0.65))
        failed = RNG.random() < p_fail

        decline_code = None
        if failed:
            decline_code = RNG.choice(DECLINE_CODES, p=cause_probs)

        rows.append(
            dict(
                txn_id=f"txn_{i}",
                customer_id=customer_id,
                txn_date=txn_date.date().isoformat(),
                amount=amount,
                bank_risk_tier=bank_risk_tier,
                days_to_card_expiry=days_to_card_expiry,
                prior_failures=prior_failures,
                account_balance_proxy=round(account_balance_proxy, 3),
                is_salary_week=is_salary_week,
                failed=failed,
                decline_code=decline_code,
            )
        )

    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = generate_transactions(6000)
    df.to_csv("data/transactions.csv", index=False)
    print(df["failed"].mean(), "overall failure rate")
    print(df[df.failed]["decline_code"].value_counts(normalize=True))
    print(f"Saved {len(df)} rows to transactions.csv")