"""
Backstop Natural Hinglish Phrasing & Gemini Dialogue Generator.
Generates culturally respectful, code-mixed Hinglish voice responses strictly
constrained by the deterministic conversation state machine.
"""

import os
import sys
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Ensure UTF-8 output when supported by stream
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Deterministic high-fidelity Hinglish fallback templates matching specification
FALLBACK_TEMPLATES = {
    "GREETING": (
        "Namaste {name} ji! Main Backstop payment desk se bol raha hu. "
        "Aapka Rs. {amount} ka payment pending tha, usi ke baare mein baat karni thi. "
        "Kya yeh sahi samay hai baat karne ka?"
    ),
    "PAY_NOW": (
        "Bahut badhiya {name} ji! Main aapke WhatsApp number {phone} par direct Razorpay payment "
        "link bhej raha hu. Main line par hi ruka hu, please check karke pay kar dijiye."
    ),
    "PROMISE_LATER": (
        "Bilkul samajh sakta hu. Maine {date_phrase} note kar li hai, us din subah aapko "
        "reminder bhej denge."
    ),
    "SEND_LINK": (
        "Maine aapke WhatsApp number pe link bhej diya hai, please check kar lijiye."
    ),
    "ALREADY_PAID": (
        "Maaf kijiye! Main abhi aapka case hold pe daal raha hu aur hamari accounts team se verify "
        "karwata hu. Aapko dobara call nahi aayegi jab tak verify na ho jaye."
    ),
    "INVOICE_DISPUTE": (
        "Maine aapka dispute record kar liya hai. Hamari accounts team invoice check karegi "
        "aur tab tak aapka case hold par rahega aur koi call nahi aayegi."
    ),
    "ANGER_HOSTILITY": (
        "Main aapki pareshani poori tarah samajh sakta hu, aur asuvidha ke liye kshama chahta hu. "
        "Main abhi aapke account par 7 din ka contact hold laga raha hu aur yeh case senior support "
        "team ko escalate kar raha hu. Aapko pareshan nahi kiya jayega."
    ),
    "OPT_OUT": (
        "Aapki request note kar li gayi hai. Aapka number hamari calling list se turant hata diya gaya hai "
        "aur DND update ho gaya hai. Aapko aage se koi reminder call nahi aayegi. Shukriya."
    ),
    "CLARIFY": (
        "Maaf kijiye, main theek se samajh nahi paya. Kya aap abhi pay karna chahenge, "
        "ya WhatsApp par link bhej du, ya fir kisi aur tareekh ka reminder lagayein?"
    ),
}


def get_fallback_phrasing(
    intent: str,
    customer_name: str = "Customer",
    amount: float = 14500.0,
    phone_masked: str = "+91 98*** **210",
    date_phrase: str = "5 tareekh",
) -> str:
    """Return an exact, high-fidelity deterministic fallback template."""
    template = FALLBACK_TEMPLATES.get(intent, FALLBACK_TEMPLATES["CLARIFY"])
    formatted_amount = f"{amount:,.0f}" if isinstance(amount, (int, float)) else str(amount)
    return template.format(
        name=customer_name,
        amount=formatted_amount,
        phone=phone_masked,
        date_phrase=date_phrase,
    )


def generate_hinglish_response(
    intent: str,
    customer_name: str = "Customer",
    amount: float = 14500.0,
    phone_masked: str = "+91 98*** **210",
    date_phrase: str = "5 tareekh",
    customer_utterance: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generates natural Hinglish phrasing for a voice call turn.
    If Gemini API key is present, uses Gemini 3.6/3.8 Flash strictly conditioned
    on the state machine intent. Otherwise, gracefully uses verified fallback templates.
    """
    key = api_key or GEMINI_API_KEY
    fallback_text = get_fallback_phrasing(
        intent=intent,
        customer_name=customer_name,
        amount=amount,
        phone_masked=phone_masked,
        date_phrase=date_phrase,
    )

    if not key:
        return {
            "text": fallback_text,
            "source": "fallback_template",
            "intent": intent,
            "note": "Offline mode: High-fidelity deterministic fallback template used.",
        }

    # Attempt Gemini call
    prompt = f"""You are the voice of Backstop, an Indian revenue recovery AI assistant speaking on a phone call.
You must speak in natural, warm, respectful Hinglish (Hindi + English code-mixing in Latin script).

MANDATORY RULES:
1. The deterministic state machine has ALREADY decided the state and intent: {intent}.
2. You must NOT alter the facts, promise unauthorized discounts, or make false threats.
3. Keep it to 1 or 2 concise conversational spoken sentences (under 35 words).
4. Do not invent fees or argue. Treat the customer with respect.

Context:
- Customer Name: {customer_name}
- Outstanding Amount: INR {amount:,.0f}
- Customer Phone (masked): {phone_masked}
- Promised Date / Detail: {date_phrase}
- Customer just said: "{customer_utterance or '...'}"
- Intent to express: {intent}

Template reference to guide phrasing:
"{fallback_text}"

Output ONLY the exact spoken Hinglish sentence(s). Do not include quotes, markdown, or commentary."""

    try:
        # Try new google.genai SDK
        try:
            from google import genai
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt,
            )
            generated = response.text.strip().replace('"', '')
            if generated:
                return {
                    "text": generated,
                    "source": "gemini",
                    "model": "gemini-3.6-flash",
                    "intent": intent,
                }
        except ImportError:
            # Try older google.generativeai SDK
            import google.generativeai as gai
            gai.configure(api_key=key)
            model = gai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)
            generated = response.text.strip().replace('"', '')
            if generated:
                return {
                    "text": generated,
                    "source": "gemini",
                    "model": "gemini-1.5-flash",
                    "intent": intent,
                }
    except Exception as e:
        # If network error or quota issue, gracefully return the high-fidelity template
        return {
            "text": fallback_text,
            "source": "fallback_template",
            "intent": intent,
            "note": f"Gemini API call failed ({type(e).__name__}); fallback used safely.",
        }

    return {
        "text": fallback_text,
        "source": "fallback_template",
        "intent": intent,
    }


if __name__ == "__main__":
    print("=== Testing Hinglish Voice Phrasing Generator ===")
    scenarios = [
        ("GREETING", "Rohan Gupta", 14500.0, None),
        ("PROMISE_LATER", "Rohan Gupta", 14500.0, "Bhaiya abhi salary nahi aayi, 5 tareekh ko transfer karunga"),
        ("ALREADY_PAID", "Rohan Gupta", 14500.0, "Maine kal shaam ko hi UPI se pay kar diya tha, baar baar call kyu karte ho?"),
        ("SEND_LINK", "Rohan Gupta", 14500.0, "Mujhe WhatsApp pe link bhej do"),
        ("ANGER_HOSTILITY", "Rohan Gupta", 14500.0, "Bakwas band karo tameez nahi hai"),
        ("OPT_OUT", "Rohan Gupta", 14500.0, "Mera number list se hatao / DND karo"),
    ]

    for intent, name, amt, utterance in scenarios:
        res = generate_hinglish_response(
            intent=intent,
            customer_name=name,
            amount=amt,
            customer_utterance=utterance,
            date_phrase="5 tareekh",
        )
        print(f"\n[INTENT: {intent}] (Source: {res['source']})")
        print(f"Agent: \"{res['text']}\"")
