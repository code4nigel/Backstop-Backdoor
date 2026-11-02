"""
Historical Dataset Generator for Backstop.
Generates 10,000+ historical training cases spanning all 4 revenue leak types:
  - failed_payment (~35%)
  - abandoned_checkout (~25%)
  - subscription_mandate (~20%)
  - b2b_invoice (~20%)

Contains latent ground truth root causes, observable symptoms, decline codes,
realistic feature correlations, and historical resolution outcomes for ML training & calibration.

Outputs:
  - data/historical_cases.csv
"""
from datetime import datetime, timedelta
import os
import random
import sys
from typing import Any, Dict, List

import numpy as np
import pandas as pd

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from engine.measurement import GROUND_TRUTH_RECOVERY_PROB, ACTION_UNIT_COST

BANKS = ["HDFC", "SBI", "ICICI", "Axis", "Kotak"]
GATEWAYS = ["Razorpay_Direct", "Route_B", "Route_C"]
CARD_NETWORKS = ["Visa", "Mastercard", "RuPay"]
UPI_APPS = ["GPay", "PhonePe", "Paytm", "CRED"]
PAYMENT_CHANNELS = ["UPI", "Credit Card", "Debit Card", "Netbanking"]

DROP_STEPS = ["cart", "shipping_info", "coupon_apply", "payment_selection", "otp_screen"]
MANDATE_TYPES = ["e_mandate", "upi_autopay", "card_standing_instruction"]
PLAN_INTERVALS = ["monthly", "quarterly", "annual"]
CLIENT_TIERS = ["SMB", "Mid-Market", "Enterprise"]

HISTORICAL_ACTIONS = [
    "retry_immediate",
    "retry_delayed",
    "reroute_gateway",
    "card_update_link",
    "mandate_retry_sequence",
    "reminder_sms",
    "whatsapp_nudge",
    "voice_call_hinglish",
    "discount_incentive",
    "human_escalation",
    "do_nothing",
]


def generate_historical_dataset(
    n_rows: int = 12000,
    start_date: str = "2026-01-01",
    seed: int = 42,
) -> pd.DataFrame:
    """
    Generates >= 10,000 historical rows with realistic signal correlations
    and resolution labels.
    """
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)

    base_start = datetime.strptime(start_date, "%Y-%m-%d")

    # Proportions:
    # failed_payment ~ 35%
    # abandoned_checkout ~ 25%
    # subscription_mandate ~ 20%
    # b2b_invoice ~ 20%
    n_failed = int(n_rows * 0.35)
    n_checkout = int(n_rows * 0.25)
    n_mandate = int(n_rows * 0.20)
    n_invoice = n_rows - (n_failed + n_checkout + n_mandate)

    rows: List[Dict[str, Any]] = []
    case_counter = 1

    # -----------------------------------------------------------------------
    # 1. Failed Payments
    # -----------------------------------------------------------------------
    for _ in range(n_failed):
        days_offset = rng.randint(0, 300)
        txn_date = base_start + timedelta(days=days_offset)
        day_of_month = txn_date.day
        is_salary_week = (day_of_month <= 5 or day_of_month >= 28)

        channel = rng.choice(PAYMENT_CHANNELS)
        bank = rng.choice(BANKS)
        gateway = rng.choice(GATEWAYS)
        prior_failures = int(np_rng.poisson(0.7))
        account_balance_proxy = round(float(np_rng.normal(0.0, 1.0)), 2)
        days_to_card_expiry = rng.randint(-15, 450)

        # Latent true cause generation with realistic priors
        if days_to_card_expiry <= 0 and "Card" in channel:
            true_cause = "expired_card"
        elif account_balance_proxy < -0.8 and not is_salary_week and rng.random() < 0.65:
            true_cause = "insufficient_funds"
        elif channel == "UPI" and rng.random() < 0.25:
            true_cause = "upi_timeout"
        elif prior_failures >= 3 and rng.random() < 0.35:
            true_cause = "fraud_flag"
        elif bank in ("SBI", "Axis") and rng.random() < 0.28:
            true_cause = "bank_risk_block"
        elif "Card" in channel and rng.random() < 0.30:
            true_cause = "3ds_friction"
        else:
            true_cause = rng.choice(["insufficient_funds", "bank_risk_block", "3ds_friction"])

        decline_code = {
            "insufficient_funds": "INSUFFICIENT_FUNDS",
            "expired_card": "EXPIRED_CARD",
            "bank_risk_block": "BANK_RISK_BLOCK",
            "3ds_friction": "AUTHENTICATION_FAILED_3DS",
            "upi_timeout": "UPI_GATEWAY_TIMEOUT",
            "fraud_flag": "FRAUD_SUSPECT_BLOCK",
        }.get(true_cause, "PAYMENT_DECLINED")

        amount = float(np.round(np_rng.gamma(shape=3.2, scale=3200), 2))
        amount = max(250.0, min(amount, 95000.0))

        # Historical action taken
        action = rng.choice(HISTORICAL_ACTIONS)
        base_p = GROUND_TRUTH_RECOVERY_PROB.get(true_cause, {}).get(action, 0.10)
        if is_salary_week and true_cause == "insufficient_funds" and action == "retry_delayed":
            base_p = min(0.92, base_p + 0.12)
        recovered = rng.random() < base_p

        rows.append({
            "case_id": f"HIST_PAY_{case_counter:06d}",
            "customer_id": f"cust_{rng.randint(10000, 99999)}",
            "leak_type": "failed_payment",
            "amount": amount,
            "days_at_risk": rng.randint(0, 5),
            "created_at": txn_date.isoformat(),
            "channel": channel,
            "bank": bank,
            "gateway": gateway,
            "card_network": rng.choice(CARD_NETWORKS) if "Card" in channel else "NA",
            "upi_app": rng.choice(UPI_APPS) if channel == "UPI" else "NA",
            "prior_failures": prior_failures,
            "is_salary_week": is_salary_week,
            "account_balance_proxy": account_balance_proxy,
            "days_to_card_expiry": days_to_card_expiry if "Card" in channel else None,
            "cart_value": None,
            "cart_items_count": None,
            "drop_step": None,
            "cart_age_hours": None,
            "mandate_type": None,
            "plan_interval": None,
            "consecutive_failed_cycles": None,
            "invoice_number": None,
            "po_number": None,
            "client_tier": None,
            "days_overdue": None,
            "credit_limit": None,
            "decline_code": decline_code,
            "true_root_cause": true_cause,
            "is_systemic": False,
            "action_taken": action,
            "recovered": recovered,
            "cost": ACTION_UNIT_COST.get(action, 2.0),
        })
        case_counter += 1

    # -----------------------------------------------------------------------
    # 2. Abandoned Checkouts
    # -----------------------------------------------------------------------
    for _ in range(n_checkout):
        days_offset = rng.randint(0, 300)
        txn_date = base_start + timedelta(days=days_offset)
        
        cart_value = round(float(np_rng.uniform(600.0, 22000.0)), 2)
        cart_items_count = int(np_rng.integers(1, 8))
        cart_age_hours = round(float(np_rng.uniform(0.5, 72.0)), 1)
        device = rng.choice(["mobile_app", "mobile_web", "desktop"])

        if cart_value > 8000.0 and rng.random() < 0.45:
            true_cause = "shipping_shock"
            drop_step = "shipping_info"
        elif rng.random() < 0.25:
            true_cause = "coupon_invalid"
            drop_step = "coupon_apply"
        elif rng.random() < 0.30:
            true_cause = "otp_delay"
            drop_step = "otp_screen"
        elif rng.random() < 0.20:
            true_cause = "payment_page_crash"
            drop_step = "payment_selection"
        else:
            true_cause = "casual_browsing"
            drop_step = "cart"

        action = rng.choice(HISTORICAL_ACTIONS)
        base_p = GROUND_TRUTH_RECOVERY_PROB.get(true_cause, {}).get(action, 0.05)
        recovered = rng.random() < base_p

        rows.append({
            "case_id": f"HIST_CART_{case_counter:06d}",
            "customer_id": f"cust_{rng.randint(10000, 99999)}",
            "leak_type": "abandoned_checkout",
            "amount": cart_value,
            "days_at_risk": int(cart_age_hours // 24),
            "created_at": txn_date.isoformat(),
            "channel": "Web/App",
            "bank": None,
            "gateway": None,
            "card_network": None,
            "upi_app": None,
            "prior_failures": 0,
            "is_salary_week": False,
            "account_balance_proxy": None,
            "days_to_card_expiry": None,
            "cart_value": cart_value,
            "cart_items_count": cart_items_count,
            "drop_step": drop_step,
            "cart_age_hours": cart_age_hours,
            "mandate_type": None,
            "plan_interval": None,
            "consecutive_failed_cycles": None,
            "invoice_number": None,
            "po_number": None,
            "client_tier": None,
            "days_overdue": None,
            "credit_limit": None,
            "decline_code": f"CHECKOUT_DROP_{drop_step.upper()}",
            "true_root_cause": true_cause,
            "is_systemic": False,
            "action_taken": action,
            "recovered": recovered,
            "cost": ACTION_UNIT_COST.get(action, 2.0),
        })
        case_counter += 1

    # -----------------------------------------------------------------------
    # 3. Subscription Mandates
    # -----------------------------------------------------------------------
    for _ in range(n_mandate):
        days_offset = rng.randint(0, 300)
        txn_date = base_start + timedelta(days=days_offset)
        
        mandate_type = rng.choice(MANDATE_TYPES)
        plan_interval = rng.choice(PLAN_INTERVALS)
        consecutive_fails = rng.choices([1, 2, 3], weights=[0.68, 0.24, 0.08])[0]

        amount = 999.0 if plan_interval == "monthly" else (2999.0 if plan_interval == "quarterly" else 11999.0)
        amount = round(amount * float(np_rng.uniform(0.85, 1.25)), 2)

        if "card" in mandate_type and rng.random() < 0.40:
            true_cause = "card_expired_before_cycle"
            decline_code = "CARD_EXPIRED"
        elif consecutive_fails >= 2 and rng.random() < 0.50:
            true_cause = "mandate_limit_exceeded"
            decline_code = "LIMIT_EXCEEDED"
        elif rng.random() < 0.35:
            true_cause = "issuer_auth_decline"
            decline_code = "ISSUER_AUTH_FAILED"
        else:
            true_cause = "pre_debit_notification_missed"
            decline_code = "PRE_DEBIT_NOTIF_FAILED"

        action = rng.choice(HISTORICAL_ACTIONS)
        base_p = GROUND_TRUTH_RECOVERY_PROB.get(true_cause, {}).get(action, 0.05)
        recovered = rng.random() < base_p

        rows.append({
            "case_id": f"HIST_MAN_{case_counter:06d}",
            "customer_id": f"cust_{rng.randint(10000, 99999)}",
            "leak_type": "subscription_mandate",
            "amount": amount,
            "days_at_risk": consecutive_fails * 5,
            "created_at": txn_date.isoformat(),
            "channel": mandate_type,
            "bank": rng.choice(BANKS),
            "gateway": "Razorpay_Autopay",
            "card_network": None,
            "upi_app": None,
            "prior_failures": consecutive_fails - 1,
            "is_salary_week": False,
            "account_balance_proxy": None,
            "days_to_card_expiry": None,
            "cart_value": None,
            "cart_items_count": None,
            "drop_step": None,
            "cart_age_hours": None,
            "mandate_type": mandate_type,
            "plan_interval": plan_interval,
            "consecutive_failed_cycles": consecutive_fails,
            "invoice_number": None,
            "po_number": None,
            "client_tier": None,
            "days_overdue": None,
            "credit_limit": None,
            "decline_code": decline_code,
            "true_root_cause": true_cause,
            "is_systemic": False,
            "action_taken": action,
            "recovered": recovered,
            "cost": ACTION_UNIT_COST.get(action, 2.0),
        })
        case_counter += 1

    # -----------------------------------------------------------------------
    # 4. B2B Invoices
    # -----------------------------------------------------------------------
    for _ in range(n_invoice):
        days_offset = rng.randint(0, 300)
        txn_date = base_start + timedelta(days=days_offset)
        client_tier = rng.choices(CLIENT_TIERS, weights=[0.50, 0.35, 0.15])[0]
        days_overdue = rng.randint(5, 45)

        if client_tier == "SMB":
            amount = round(float(np_rng.uniform(20000.0, 75000.0)), 2)
            credit_limit = 150000.0
        elif client_tier == "Mid-Market":
            amount = round(float(np_rng.uniform(80000.0, 250000.0)), 2)
            credit_limit = 500000.0
        else:
            amount = round(float(np_rng.uniform(250000.0, 600000.0)), 2)
            credit_limit = 1500000.0

        if days_overdue > 35 and rng.random() < 0.40:
            true_cause = "invoice_dispute"
        elif days_overdue < 15 and rng.random() < 0.55:
            true_cause = "forgot_payment"
        elif rng.random() < 0.35:
            true_cause = "cash_flow_timing"
        elif rng.random() < 0.25:
            true_cause = "po_number_mismatch"
        else:
            true_cause = "wrong_accounts_payable_contact"

        action = rng.choice(HISTORICAL_ACTIONS)
        base_p = GROUND_TRUTH_RECOVERY_PROB.get(true_cause, {}).get(action, 0.05)
        recovered = rng.random() < base_p

        rows.append({
            "case_id": f"HIST_INV_{case_counter:06d}",
            "customer_id": f"org_{rng.randint(1000, 9999)}",
            "leak_type": "b2b_invoice",
            "amount": amount,
            "days_at_risk": days_overdue,
            "created_at": txn_date.isoformat(),
            "channel": "Bank_Transfer_NEFT_RTGS",
            "bank": None,
            "gateway": None,
            "card_network": None,
            "upi_app": None,
            "prior_failures": 0,
            "is_salary_week": False,
            "account_balance_proxy": None,
            "days_to_card_expiry": None,
            "cart_value": None,
            "cart_items_count": None,
            "drop_step": None,
            "cart_age_hours": None,
            "mandate_type": None,
            "plan_interval": None,
            "consecutive_failed_cycles": None,
            "invoice_number": f"INV-2026-{rng.randint(10000, 99999)}",
            "po_number": f"PO-{rng.randint(100000, 999999)}",
            "client_tier": client_tier,
            "days_overdue": days_overdue,
            "credit_limit": credit_limit,
            "decline_code": f"OVERDUE_NET30_{true_cause.upper()}",
            "true_root_cause": true_cause,
            "is_systemic": False,
            "action_taken": action,
            "recovered": recovered,
            "cost": ACTION_UNIT_COST.get(action, 2.0),
        })
        case_counter += 1

    df = pd.DataFrame(rows)
    return df


def save_historical_dataset(df: pd.DataFrame, target_path: str = "data/historical_cases.csv") -> str:
    """Saves DataFrame to target CSV path."""
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    df.to_csv(target_path, index=False)
    return target_path


if __name__ == "__main__":
    n = 12000
    print(f"Generating {n} historical rows for ML model training...")
    df = generate_historical_dataset(n_rows=n, seed=42)
    saved_path = save_historical_dataset(df)
    print(f"Successfully generated {len(df)} rows across {df['leak_type'].nunique()} leak types:")
    for leak, count in df["leak_type"].value_counts().items():
        print(f"  - {leak}: {count} rows ({count/len(df)*100:.1f}%)")
    print(f"Overall Recovery Rate: {df['recovered'].mean()*100:.2f}%")
    print(f"Saved: {saved_path}")
