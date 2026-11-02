"""
Demo Batch Generator:
Synthesizes 300 active multi-leak recovery cases (~₹54.2 Lakhs at risk)
spanning Failed Payments, Abandoned Checkouts, Subscription Mandates, and B2B Invoices.
Embeds systemic route degradations, 15% stratified holdout control group,
and strict PII masking.
"""
import os
import sys
import json
import random
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from typing import List, Dict, Any
from engine.models import RecoveryCase, LeakType, CaseState
from engine.pii import mask_phone, mask_email, mask_name


INDIAN_FIRST_NAMES = [
    "Aarav", "Ananya", "Rohan", "Priya", "Vikram", "Sneha", "Aditya", "Pooja", "Rahul", "Neha",
    "Arjun", "Kavya", "Varun", "Isha", "Karan", "Riya", "Siddharth", "Tanvi", "Nikhil", "Divya",
    "Manish", "Meera", "Abhishek", "Deepika", "Kunal", "Simran", "Rajesh", "Sunita", "Amit", "Swati"
]

INDIAN_LAST_NAMES = [
    "Sharma", "Verma", "Gupta", "Mehta", "Patel", "Reddy", "Nair", "Iyer", "Chopra", "Singh",
    "Kumar", "Joshi", "Bhatia", "Malhotra", "Saxena", "Deshmukh", "Kulkarni", "Aggarwal", "Mishra", "Shah"
]

BANKS = ["HDFC", "SBI", "ICICI", "Axis", "Kotak"]
UPI_APPS = ["GPay", "PhonePe", "Paytm", "CRED"]
CARD_NETWORKS = ["Visa", "Mastercard", "RuPay"]


def generate_batch(seed: int = 42, count: int = 300) -> List[RecoveryCase]:
    rng = random.Random(seed)
    cases: List[RecoveryCase] = []
    now = time.time()

    # Proportions: 105 payments, 75 carts, 60 mandates, 60 invoices = 300
    n_payments = 105
    n_carts = 75
    n_mandates = 60
    n_invoices = 60

    case_idx = 1

    # 1. Failed Payments
    for i in range(n_payments):
        case_id = f"CASE_PAY_{case_idx:04d}"
        cust_id = f"CUST_{rng.randint(1000, 9999)}"
        raw_name = f"{rng.choice(INDIAN_FIRST_NAMES)} {rng.choice(INDIAN_LAST_NAMES)}"
        raw_phone = f"+91 {rng.randint(60000, 99999)} {rng.randint(10000, 99999)}"
        raw_email = f"{raw_name.lower().replace(' ', '.')}@example.com"
        amount = round(rng.uniform(2000, 15000), 2)
        days_at_risk = rng.randint(1, 5)

        bank = rng.choice(BANKS)
        gateway = "Razorpay_Direct"
        upi_app = rng.choice(UPI_APPS)
        is_salary_week = rng.choice([True, False, False])

        # Inject systemic incident for HDFC route in cases 20 to 45
        if 20 <= i <= 45:
            bank = "HDFC"
            true_cause = "systemic_route_degraded"
            decline_code = "ISSUER_DOWN"
            is_systemic = True
        else:
            is_systemic = False
            cause_choice = rng.random()
            if cause_choice < 0.35:
                true_cause = "insufficient_funds"
                decline_code = "INSUFFICIENT_FUNDS"
            elif cause_choice < 0.60:
                true_cause = "expired_card"
                decline_code = "EXPIRED_CARD"
            elif cause_choice < 0.80:
                true_cause = "upi_timeout"
                decline_code = "UPI_TIMEOUT"
            else:
                true_cause = "3ds_friction"
                decline_code = "3DS_TIMEOUT"

        meta = {
            "bank": bank,
            "gateway": gateway,
            "upi_app": upi_app,
            "card_network": rng.choice(CARD_NETWORKS),
            "decline_code": decline_code,
            "prior_failures": rng.randint(0, 2),
            "is_systemic": is_systemic,
            "days_to_card_expiry": -15 if true_cause == "expired_card" else rng.randint(30, 400),
            "customer_touches_7d": 0,
        }

        cases.append(RecoveryCase(
            case_id=case_id,
            customer_id=cust_id,
            customer_name=mask_name(raw_name),
            phone_masked=mask_phone(raw_phone),
            email_masked=mask_email(raw_email),
            leak_type=LeakType.FAILED_PAYMENT,
            amount=amount,
            days_at_risk=days_at_risk,
            created_at=now - (days_at_risk * 86400),
            metadata=meta,
            true_root_cause=true_cause,
            is_salary_week=is_salary_week,
            state=CaseState.DETECTED,
            assigned_run_id="demo_run",
            is_control_group=False,
        ))
        case_idx += 1

    # 2. Abandoned Checkouts
    for i in range(n_carts):
        case_id = f"CASE_CART_{case_idx:04d}"
        cust_id = f"CUST_{rng.randint(1000, 9999)}"
        raw_name = f"{rng.choice(INDIAN_FIRST_NAMES)} {rng.choice(INDIAN_LAST_NAMES)}"
        raw_phone = f"+91 {rng.randint(60000, 99999)} {rng.randint(10000, 99999)}"
        raw_email = f"{raw_name.lower().replace(' ', '.')}@example.com"
        amount = round(rng.uniform(1000, 10000), 2)
        days_at_risk = rng.randint(1, 3)

        cause_r = rng.random()
        if cause_r < 0.40:
            true_cause = "shipping_shock"
            drop_step = "shipping_info"
        elif cause_r < 0.70:
            true_cause = "otp_delay"
            drop_step = "otp_screen"
        elif cause_r < 0.88:
            true_cause = "coupon_invalid"
            drop_step = "coupon_apply"
        else:
            true_cause = "payment_page_crash"
            drop_step = "payment_selection"

        meta = {
            "drop_step": drop_step,
            "cart_items_count": rng.randint(1, 4),
            "cart_age_hours": rng.randint(2, 48),
            "customer_touches_7d": 0,
        }

        cases.append(RecoveryCase(
            case_id=case_id,
            customer_id=cust_id,
            customer_name=mask_name(raw_name),
            phone_masked=mask_phone(raw_phone),
            email_masked=mask_email(raw_email),
            leak_type=LeakType.ABANDONED_CHECKOUT,
            amount=amount,
            days_at_risk=days_at_risk,
            created_at=now - (days_at_risk * 86400),
            metadata=meta,
            true_root_cause=true_cause,
            is_salary_week=False,
            state=CaseState.DETECTED,
            assigned_run_id="demo_run",
            is_control_group=False,
        ))
        case_idx += 1

    # 3. Subscription Mandates
    for i in range(n_mandates):
        case_id = f"CASE_SUB_{case_idx:04d}"
        cust_id = f"CUST_{rng.randint(1000, 9999)}"
        raw_name = f"{rng.choice(INDIAN_FIRST_NAMES)} {rng.choice(INDIAN_LAST_NAMES)}"
        raw_phone = f"+91 {rng.randint(60000, 99999)} {rng.randint(10000, 99999)}"
        raw_email = f"{raw_name.lower().replace(' ', '.')}@example.com"
        amount = round(rng.uniform(2500, 15000), 2)
        days_at_risk = rng.randint(1, 6)

        m_cause = rng.random()
        if m_cause < 0.45:
            true_cause = "pre_debit_notification_missed"
            meta_pre_debit = False
            mandate_type = "e_mandate"
        elif m_cause < 0.75:
            true_cause = "mandate_limit_exceeded"
            amount = max(amount, 15500.0)  # > 15,000 threshold
            meta_pre_debit = True
            mandate_type = "upi_autopay"
        else:
            true_cause = "card_expired_before_cycle"
            meta_pre_debit = True
            mandate_type = "card_standing_instruction"

        meta = {
            "mandate_id": f"mandate_{rng.randint(100000, 999999)}",
            "mandate_type": mandate_type,
            "pre_debit_sent": meta_pre_debit,
            "days_to_card_expiry": -5 if true_cause == "card_expired_before_cycle" else 180,
            "customer_touches_7d": 0,
        }

        cases.append(RecoveryCase(
            case_id=case_id,
            customer_id=cust_id,
            customer_name=mask_name(raw_name),
            phone_masked=mask_phone(raw_phone),
            email_masked=mask_email(raw_email),
            leak_type=LeakType.SUBSCRIPTION_MANDATE,
            amount=amount,
            days_at_risk=days_at_risk,
            created_at=now - (days_at_risk * 86400),
            metadata=meta,
            true_root_cause=true_cause,
            is_salary_week=False,
            state=CaseState.DETECTED,
            assigned_run_id="demo_run",
            is_control_group=False,
        ))
        case_idx += 1

    # 4. B2B Invoices
    for i in range(n_invoices):
        case_id = f"CASE_INV_{case_idx:04d}"
        cust_id = f"CORP_{rng.randint(100, 999)}"
        company_name = f"{rng.choice(INDIAN_LAST_NAMES)} Enterprises Pvt Ltd"
        raw_phone = f"+91 {rng.randint(60000, 99999)} {rng.randint(10000, 99999)}"
        raw_email = f"accounts@{company_name.lower().split()[0]}.co.in"
        amount = round(rng.uniform(30000, 85000), 2)
        days_at_risk = rng.randint(5, 35)

        inv_r = rng.random()
        if inv_r < 0.35:
            true_cause = "po_number_mismatch"
            po_mismatch = True
            dispute_flag = False
        elif inv_r < 0.65:
            true_cause = "cash_flow_timing"
            po_mismatch = False
            dispute_flag = False
        elif inv_r < 0.85:
            true_cause = "forgot_payment"
            po_mismatch = False
            dispute_flag = False
        else:
            true_cause = "invoice_dispute"
            po_mismatch = False
            dispute_flag = True

        meta = {
            "invoice_number": f"INV-2026-{rng.randint(10000, 99999)}",
            "po_number_mismatch": po_mismatch,
            "dispute_flag": dispute_flag,
            "client_tier": rng.choice(["SMB", "Mid-Market", "Enterprise"]),
            "customer_touches_7d": 0,
        }

        cases.append(RecoveryCase(
            case_id=case_id,
            customer_id=cust_id,
            customer_name=mask_name(company_name),
            phone_masked=mask_phone(raw_phone),
            email_masked=mask_email(raw_email),
            leak_type=LeakType.B2B_INVOICE,
            amount=amount,
            days_at_risk=days_at_risk,
            created_at=now - (days_at_risk * 86400),
            metadata=meta,
            true_root_cause=true_cause,
            is_salary_week=rng.choice([True, False]),
            state=CaseState.DETECTED,
            assigned_run_id="demo_run",
            is_control_group=False,
        ))
        case_idx += 1

    # 5. Stratified Holdout Control Group (15% per leak type)
    for lt in LeakType:
        lt_cases = [c for c in cases if c.leak_type == lt]
        n_ctrl = int(len(lt_cases) * 0.15)
        # Sort by amount to ensure stratified spread
        lt_cases.sort(key=lambda c: c.amount)
        # Sample evenly across amount spectrum
        step = len(lt_cases) / n_ctrl if n_ctrl else 1
        for j in range(n_ctrl):
            target_idx = int(j * step)
            lt_cases[min(target_idx, len(lt_cases) - 1)].is_control_group = True

    return cases


def main():
    cases = generate_batch(seed=42, count=300)
    data_list = [c.to_dict() for c in cases]
    total_val = sum(c.amount for c in cases)
    print(f"Generated {len(cases)} cases across 4 leak types. Total at risk: Rs. {total_val:,.2f}")

    out_path = "data/active_cases_300.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data_list, f, indent=2)
    print(f"Saved active batch to {out_path}")

    # Also save flattened CSV
    import pandas as pd
    flat_rows = []
    for c in cases:
        r = {
            "case_id": c.case_id,
            "customer_id": c.customer_id,
            "customer_name": c.customer_name,
            "phone_masked": c.phone_masked,
            "email_masked": c.email_masked,
            "leak_type": c.leak_type.value if isinstance(c.leak_type, LeakType) else str(c.leak_type),
            "amount": c.amount,
            "days_at_risk": c.days_at_risk,
            "created_at": c.created_at,
            "true_root_cause": c.true_root_cause,
            "is_salary_week": c.is_salary_week,
            "state": c.state.value if isinstance(c.state, CaseState) else str(c.state),
            "assigned_run_id": c.assigned_run_id,
            "is_control_group": c.is_control_group,
        }
        if c.metadata:
            for k, v in c.metadata.items():
                r[f"meta_{k}"] = v
        flat_rows.append(r)
    csv_path = "data/active_cases_300.csv"
    pd.DataFrame(flat_rows).to_csv(csv_path, index=False)
    print(f"Saved active batch CSV to {csv_path}")


if __name__ == "__main__":
    main()
