"""
High-Speed Deterministic PII Masking Utilities for Backstop.

Enforces zero-data-leak guarantees across phone numbers, emails, customer names,
card numbers, and GSTIN identifiers in logs, API responses, and database entries.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Union

# Security Audit Regexes
RE_RAW_INDIAN_PHONE = re.compile(r"(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}\b")
RE_STRICT_10_DIGIT_PHONE = re.compile(r"^[6-9]\d{9}$")
RE_RAW_EMAIL = re.compile(r"(?<![•\w\\])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,7}\b")
RE_CREDIT_CARD_PAN = re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b|\b\d{15,16}\b")
RE_GSTIN = re.compile(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}\b")


def mask_phone(phone: Optional[str]) -> str:
    """Mask a phone number preserving prefix and last 3 digits.

    Example:
        "+91 98765 43210" -> "+91 98••• ••210"
        "9876543210"      -> "98•••••210"

    Args:
        phone: Raw phone number string

    Returns:
        Deterministic masked phone string
    """
    if not phone:
        return ""

    s = str(phone).strip()
    if not s:
        return ""

    # Locate positions of all digits in the string
    digit_indices = [i for i, ch in enumerate(s) if ch.isdigit()]
    total_digits = len(digit_indices)

    if total_digits < 4:
        return "•" * len(s)

    chars = list(s)

    if total_digits >= 10:
        # Keep last 3 digits clear.
        # Mask the 5 digits immediately preceding the last 3 digits.
        # Keep all digits before that (country code + first 2 mobile digits).
        mask_start = total_digits - 8
        mask_end = total_digits - 3
        for idx in range(mask_start, mask_end):
            chars[digit_indices[idx]] = "•"
    else:
        # Intermediate length: keep first 2 and last 2, mask between
        mask_start = 2
        mask_end = total_digits - 2
        for idx in range(mask_start, mask_end):
            chars[digit_indices[idx]] = "•"

    return "".join(chars)


def mask_email(email: Optional[str]) -> str:
    """Mask an email address preserving domain and edge username characters.

    Example:
        "ananya.sharma@example.com" -> "a•••••a@example.com"
        "raj@domain.in"            -> "r•••••j@domain.in"
        "ab@domain.com"            -> "a•@domain.com"

    Args:
        email: Raw email address

    Returns:
        Deterministic masked email string
    """
    if not email:
        return ""

    s = str(email).strip()
    if "@" not in s:
        return mask_name(s)

    user_part, domain = s.split("@", 1)
    if not user_part:
        return f"•••@{domain}"

    if len(user_part) == 1:
        return f"•@{domain}"
    elif len(user_part) == 2:
        return f"{user_part[0]}•@{domain}"
    else:
        return f"{user_part[0]}•••••{user_part[-1]}@{domain}"


def mask_name(name: Optional[str]) -> str:
    """Mask a customer or contact name preserving initials.

    Example:
        "Rohan Gupta" -> "R•••• G••••"
        "Ananya"      -> "A•••••"

    Args:
        name: Raw customer name string

    Returns:
        Deterministic masked name string
    """
    if not name:
        return ""

    tokens = re.split(r"(\s+)", str(name).strip())
    masked_tokens = []
    for t in tokens:
        if t.isspace():
            masked_tokens.append(t)
        elif len(t) == 1:
            masked_tokens.append(t.upper())
        elif len(t) > 1:
            masked_tokens.append(t[0].upper() + "•" * (len(t) - 1))
        else:
            masked_tokens.append(t)

    return "".join(masked_tokens)


def mask_card(card: Optional[str]) -> str:
    """Mask a credit/debit card number, displaying only the last 4 digits.

    Example:
        "4242"                 -> "•••• •••• •••• 4242"
        "4111 2222 3333 4242" -> "•••• •••• •••• 4242"

    Args:
        card: Card PAN or last 4 digits

    Returns:
        Formatted masked card string
    """
    if not card:
        return "•••• •••• •••• ••••"

    digits = "".join(c for c in str(card) if c.isdigit())
    if len(digits) >= 4:
        last4 = digits[-4:]
    else:
        last4 = digits.rjust(4, "•")

    return f"•••• •••• •••• {last4}"


def mask_gstin(gstin: Optional[str]) -> str:
    """Mask a 15-character Indian Goods and Services Tax Identification Number (GSTIN).

    Preserves 2-digit State Code and last 3 entity digits, masking the central 10-digit PAN.

    Example:
        "27AAPFU0939F1ZV" -> "27••••••••••1ZV"

    Args:
        gstin: Raw GSTIN string

    Returns:
        Masked GSTIN string
    """
    if not gstin:
        return ""

    s = "".join(c for c in str(gstin).strip().upper() if c.isalnum())
    if len(s) == 15:
        return f"{s[:2]}••••••••••{s[-3:]}"
    elif len(s) > 5:
        return f"{s[:2]}{'•' * (len(s) - 5)}{s[-3:]}"
    else:
        return "•" * len(s)


def mask_text(text: Optional[str]) -> str:
    """Scan and mask any embedded PII (phone, email, card, GSTIN) inside free text.

    Args:
        text: Free-form text or log string

    Returns:
        Sanitized text with all identifiable PII masked
    """
    if not text:
        return ""

    s = str(text)
    s = RE_CREDIT_CARD_PAN.sub(lambda m: mask_card(m.group(0)), s)
    s = RE_GSTIN.sub(lambda m: mask_gstin(m.group(0)), s)
    s = RE_RAW_EMAIL.sub(lambda m: mask_email(m.group(0)), s)
    s = RE_RAW_INDIAN_PHONE.sub(lambda m: mask_phone(m.group(0)), s)
    return s


def mask_dict(data: Any) -> Any:
    """Recursively scan dictionary keys and values, replacing PII with masked representations.

    Sanitizes keys matching phone, email, name, card, customer_name, pan, gstin.
    Does not mutate the input dictionary.

    Args:
        data: Arbitrary dict, list, or primitive

    Returns:
        Deeply sanitized copy of the input
    """
    if isinstance(data, dict):
        result: Dict[str, Any] = {}
        for k, v in data.items():
            k_lower = str(k).lower()

            if isinstance(v, (dict, list)):
                result[k] = mask_dict(v)
            elif isinstance(v, str):
                if any(term in k_lower for term in ("phone", "mobile", "contact", "tel")):
                    result[k] = mask_phone(v)
                elif any(term in k_lower for term in ("email", "mail")):
                    result[k] = mask_email(v)
                elif any(term in k_lower for term in ("customer_name", "user_name", "cardholder", "client_name", "full_name")) or k_lower == "name":
                    result[k] = mask_name(v)
                elif any(term in k_lower for term in ("card", "card_number", "pan_number", "credit_card")):
                    result[k] = mask_card(v)
                elif "gstin" in k_lower:
                    result[k] = mask_gstin(v)
                else:
                    result[k] = mask_text(v)
            else:
                result[k] = v
        return result

    elif isinstance(data, list):
        return [mask_dict(item) for item in data]

    return data


def audit_scan_pii(text: str) -> List[str]:
    """Scan text for PII exposure violations.

    Returns:
        List of human-readable violation descriptions
    """
    violations: List[str] = []
    if not isinstance(text, str):
        text = str(text)

    if RE_RAW_INDIAN_PHONE.search(text):
        violations.append("Raw 10-digit Indian mobile number exposed")
    if RE_RAW_EMAIL.search(text):
        violations.append("Raw email address exposed")
    if RE_CREDIT_CARD_PAN.search(text):
        violations.append("Raw credit card PAN exposed")
    return violations
