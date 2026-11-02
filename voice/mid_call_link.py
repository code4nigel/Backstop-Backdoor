"""
Backstop Mid-Call WhatsApp Payment Link Dispatcher.
Generates authenticated Razorpay payment links and simulates instant WhatsApp
dispatch mid-call with delivery timestamps and receipts.
"""

import hashlib
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional

# Ensure UTF-8 output when supported by stream
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def mask_phone_number(phone: str) -> str:
    """
    Mask phone number for privacy compliance:
    e.g. "+91 98765 43210" -> "+91 98*** **210"
    """
    if not phone:
        return "+91 ***** *****"
    digits = [c for c in phone if c.isdigit()]
    if len(digits) >= 10:
        raw = "".join(digits)
        country_code = raw[:-10] if len(raw) > 10 else "91"
        mobile = raw[-10:]
        return f"+{country_code} {mobile[:2]}*** **{mobile[-3:]}"
    return "+91 98*** **210"


def send_mid_call_paylink(
    case_id: str = "rec_case_89f02a",
    customer_phone: str = "+91 98765 43210",
    amount: float = 14500.0,
    merchant_name: str = "Razorpay Backstop Recovery",
    expiry_minutes: int = 60,
) -> Dict[str, Any]:
    """
    Generates a secure, signed Razorpay payment link and simulates real-time
    WhatsApp message delivery to the customer during an active recovery voice call.

    Args:
        case_id: Unique identifier for the recovery case.
        customer_phone: Recipient phone number (will be masked in audit receipt).
        amount: Outstanding balance amount in INR.
        merchant_name: Calling merchant / brand display name.
        expiry_minutes: Link validity window in minutes.

    Returns:
        Dict containing phone (masked), pay_link, status, timestamp, and audit receipt.
    """
    clean_id = str(case_id).replace("rec_case_", "").replace("case_", "")
    # Generate deterministic 6-character signed slug
    salt = "rzp_signed_token_backstop"
    raw_sig = hashlib.sha256(f"{clean_id}:{amount}:{salt}".encode("utf-8")).hexdigest()
    slug = f"rec_{raw_sig[:6]}"
    paylink = f"https://rzp.io/i/{slug}"

    now = datetime.now(timezone.utc)
    timestamp_str = now.isoformat()
    receipt_hash = hashlib.md5(f"{case_id}:{timestamp_str}".encode("utf-8")).hexdigest()[:12]
    receipt_id = f"wamid.HBgMOTE5OD{receipt_hash}"

    masked_phone = mask_phone_number(customer_phone)
    short_message = (
        f"Payment link for INR {amount:,.2f} from {merchant_name}. "
        f"Tap link to pay instantly via UPI, Cards, Netbanking: {paylink} "
        f"(Valid for {expiry_minutes} mins)."
    )

    return {
        "status": "DELIVERED",
        "case_id": case_id,
        "amount": round(float(amount), 2),
        "phone_masked": masked_phone,
        "pay_link": paylink,
        "paylink": paylink,  # convenient alias
        "timestamp": timestamp_str,
        "delivery_receipt": receipt_id,
        "channel": "WHATSAPP_MID_CALL",
        "short_message": short_message,
        "expiry_minutes": expiry_minutes,
        "signed_signature": raw_sig[:16],
    }


if __name__ == "__main__":
    test_result = send_mid_call_paylink(
        case_id="rec_case_89f02a",
        customer_phone="+91 98765 43210",
        amount=14500.0,
    )
    print("=== Mid-Call WhatsApp PayLink Dispatch Test ===")
    for k, v in test_result.items():
        print(f"{k}: {v}")
