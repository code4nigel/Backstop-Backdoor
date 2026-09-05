"""
Message Drafter -- the ONLY place an LLM (Gemini) is used in this system.

Design choice (say this to judges): everything upstream -- diagnosis, EV
ranking, stopping rules -- is deterministic or a small trained classifier.
Gemini's job here is narrow: turn an already-decided intervention into a
natural, appropriately-toned customer message (including Hinglish). It
never decides WHAT to do, only HOW to phrase what's already decided.

If no GEMINI_API_KEY is configured, this falls back to a template message
so the rest of the system still works end-to-end without the API key.
"""
import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

_TEMPLATES = {
    "insufficient_funds": "Hi {name}, your payment of {amount} didn't go through. We'll retry automatically once your account usually has funds -- no action needed unless you'd like to update your payment method.",
    "expired_card": "Hi {name}, it looks like the card on file for your {amount} payment has expired. Please update your card details to avoid any interruption.",
    "bank_risk_block": "Hi {name}, your bank flagged the {amount} payment for extra review. This sometimes happens with new or high-value transactions -- please confirm the transaction with your bank or try an alternate payment method.",
    "3ds_friction": "Hi {name}, your {amount} payment needs one more verification step. Please complete the authentication on your bank's app to finish the payment.",
    "fraud_flag": "Hi {name}, we've paused your {amount} payment for a routine security check. Our team will reach out shortly to verify a few details.",
}


def _fallback_message(root_cause: str, name: str, amount: str) -> str:
    template = _TEMPLATES.get(root_cause, "Hi {name}, we noticed an issue with your {amount} payment. Please review your payment details.")
    return template.format(name=name, amount=amount)


def draft_message(root_cause: str, action: str, customer_name: str, amount_display: str,
                   language: str = "english") -> dict:
    if not GEMINI_API_KEY:
        return {
            "message": _fallback_message(root_cause, customer_name, amount_display),
            "source": "template_fallback",
            "note": "GEMINI_API_KEY not set -- using template fallback. Add it to .env to enable live drafting.",
        }

    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)

        tone_instruction = (
            "Write in natural Hinglish (Hindi-English code-mixed, Latin script), "
            "warm and conversational, like a helpful support agent texting a customer."
            if language == "hinglish"
            else "Write in clear, warm, professional English."
        )

        prompt = f"""You are drafting a short SMS/WhatsApp-style payment recovery message.

Context (already decided by the recovery system -- do not change these facts):
- Customer name: {customer_name}
- Amount: {amount_display}
- Reason payment failed: {root_cause}
- Chosen recovery action: {action}

{tone_instruction}

Rules:
- Under 45 words.
- Do not invent extra fees, deadlines, or threats.
- Do not use ALL CAPS or excessive exclamation marks.
- End with a clear, simple next step matching the action: {action}.

Output ONLY the message text, nothing else."""

        import time
        last_error = None
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=prompt,
                )
                return {"message": response.text.strip(), "source": "gemini"}
            except Exception as e:
                last_error = e
                if "503" in str(e) or "UNAVAILABLE" in str(e):
                    time.sleep(1.5 * (attempt + 1))  # backoff: 1.5s, 3s
                    continue
                break  # non-retryable error, stop immediately

        return {
            "message": _fallback_message(root_cause, customer_name, amount_display),
            "source": "template_fallback",
            "note": f"Gemini call failed after retries ({type(last_error).__name__}: {last_error}), used fallback.",
        }

    except Exception as e:
        return {
            "message": _fallback_message(root_cause, customer_name, amount_display),
            "source": "template_fallback",
            "note": f"Gemini call failed ({type(e).__name__}: {e}), used fallback.",
        }

if __name__ == "__main__":
    result = draft_message("expired_card", "reminder_sms", "Priya", "Rs. 20,000", language="hinglish")
    print(result)