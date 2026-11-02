"""
Backstop Natural Hinglish Phrasing & Live Multi-Turn Gemini LLM Dialogue Engine.
Supports:
1. Live Google Gemini API (gemini-2.5-flash / gemini-2.0-flash) via direct REST API (zero SDK install required) or google-genai SDK.
2. Runtime API key configuration from the Voice Studio UI, environment variables, or .env file.
3. Multi-turn conversational Hinglish fallback engine supporting Hindi (Devanagari), Hinglish, and English.
"""

import os
import sys
import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, List

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Global runtime API key (can be set via .env, environment variable, or UI input)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip() or None


def set_runtime_gemini_key(api_key: Optional[str]) -> bool:
    """Sets the Gemini API key at runtime and persists to .env if possible."""
    global GEMINI_API_KEY
    clean = (api_key or "").strip()
    GEMINI_API_KEY = clean if clean else None
    if clean:
        os.environ["GEMINI_API_KEY"] = clean
        try:
            env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(f"GEMINI_API_KEY={clean}\n")
        except Exception:
            pass
    return bool(GEMINI_API_KEY)


def get_runtime_gemini_key() -> Optional[str]:
    return GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", "").strip() or None


# Deterministic high-fidelity Hinglish fallback templates matching specification & unit tests
FALLBACK_TEMPLATES = {
    "GREETING": (
        "Namaste {name} ji! Main Backstop payment desk se bol raha hu. "
        "Aapka Rs. {amount} ka payment pending tha, usi ke baare mein baat karni thi. "
        "Kya yeh sahi samay hai baat karne ka?"
    ),
    "ASK_DETAILS": (
        "Ji {name} ji, yeh aapke Rs. {amount} ke pending transaction ke baare mein hai jo "
        "technical decline ya overdue hone ki wajah se hold par hai. Agar aap chahein toh main "
        "abhi WhatsApp par direct payment link bhej sakta hu, ya aap koi convenient date bata sakte hain."
    ),
    "NEGOTIATE_DISCOUNT": (
        "Main samajh sakta hu {name} ji. Hamare policy guardrail ke andar main aapko "
        "10% ka one-time instant waiver de sakta hu. Kya main updated discounted link aapke "
        "WhatsApp number {phone} par bhej du?"
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
    "GOODBYE": (
        "Bahut shukriya {name} ji! Aapka sara record update ho gaya hai. Aapka din shubh ho, Namaste!"
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


def _call_gemini_rest(api_key: str, prompt: str, json_mode: bool = False) -> Optional[str]:
    """
    Calls the official Google Gemini REST API directly using stdlib urllib.
    Tries gemini-2.5-flash first, then gemini-2.0-flash, then gemini-1.5-flash.
    """
    models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    for model_name in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        gen_config: Dict[str, Any] = {
            "temperature": 0.35,
            "maxOutputTokens": 350,
        }
        if json_mode:
            gen_config["responseMimeType"] = "application/json"

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_config,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                candidates = body.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts and "text" in parts[0]:
                        return parts[0]["text"].strip()
        except urllib.error.HTTPError as he:
            # If model not found (404), try next model; if 400/401/403 invalid key, break
            if he.code in (401, 403):
                break
            continue
        except Exception:
            continue
    return None


def analyze_and_respond_with_gemini(
    customer_utterance: str,
    customer_name: str,
    amount: float,
    phone_masked: str,
    leak_type: str = "b2b_invoice",
    predicted_cause: str = "cash_flow_timing",
    conversation_history: Optional[List[Dict[str, str]]] = None,
    api_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Uses live Gemini LLM to both classify the customer's utterance across multi-turn context
    AND generate a natural Hinglish spoken response while strictly enforcing RBI & policy rules.
    Returns None if no API key is configured or if the network call fails.
    """
    key = api_key if api_key is not None else get_runtime_gemini_key()
    if not key:
        return None

    history_lines = []
    for turn in (conversation_history or [])[-8:]:
        role = turn.get("role", "Agent")
        txt = turn.get("text", "")
        history_lines.append(f"{role}: {txt}")
    history_block = "\n".join(history_lines) if history_lines else "Agent: Namaste! Main Backstop payment desk se bol raha hu."

    prompt = f"""You are 'Aarav', a polite, empathetic, RBI-compliant Senior Recovery Specialist at Backstop (Razorpay Payment Operations Desk) on a live phone call with an Indian customer.

CASE CONTEXT:
- Customer Name: {customer_name}
- Pending Amount: Rs. {amount:,.0f}
- Payment Category: {leak_type} (Diagnosed Cause: {predicted_cause})
- Customer Masked Phone: {phone_masked}
- Policy Guardrails:
  1. Never threaten, harass, or use aggressive language.
  2. Maximum permitted discount/waiver is 10% (only offer if customer asks for a discount or hesitates due to price/charges).
  3. If customer says they already paid or disputes the invoice, IMMEDIATELY apologize, freeze outreach, and log a reconciliation task.
  4. If customer is angry/hostile or asks for DND/opt-out, IMMEDIATELY apologize, stop outreach, and end the call.
  5. Keep the conversation natural and multi-turn! Do NOT end the call if the customer is just greeting, saying "haan boliye", asking what the payment is for, asking for details, negotiating, or asking you to send a link.

CONVERSATION HISTORY SO FAR:
{history_block}

CUSTOMER JUST SAID:
"{customer_utterance}"
(Note: The customer may speak in Hindi Devanagari script, Hinglish, or English. Understand all three seamlessly.)

YOUR TASK:
Analyze the customer's message in context and respond in natural, warm, conversational Hinglish written in Latin script (so the voice synthesizer speaks it clearly, e.g., "Haan {customer_name} ji, yeh aapke Rs. {amount:,.0f} ke pending invoice ke baare mein hai..."). Keep your spoken reply concise (1 to 2 sentences, under 35 words).

Choose the single best `detected_intent` from this exact list:
- "ASK_DETAILS": Customer says "haan boliye", "kaun bol raha hai", "kis cheez ka payment", "what is this for", "yes tell me", or asks a general question.
- "NEGOTIATE_DISCOUNT": Customer asks for a discount, waiver, lower amount, or complains about shipping/extra charges.
- "PROMISE_LATER": Customer asks for more time, mentions salary delay, or promises to pay on a future date (e.g., "5 tareekh", "kal", "parso", "next week", "abhi paise nahi hain").
- "SEND_LINK": Customer asks you to send the payment link on WhatsApp/SMS ("link bhej do", "WhatsApp kar do").
- "PAY_NOW": Customer agrees to pay right now ("haan abhi kar deta hu", "GPay kar raha hu").
- "ALREADY_PAID": Customer claims they already made the payment ("maine pay kar diya tha", "paise kat gaye").
- "INVOICE_DISPUTE": Customer says the bill/invoice is wrong or service/goods had an issue ("invoice galat hai").
- "ANGER_HOSTILITY": Customer is angry, uses harsh language, or tells you to stop bothering them ("bakwas band karo", "harass mat karo").
- "OPT_OUT": Customer explicitly asks for DND or removing their number ("DND karo", "list se number hatao").
- "GOODBYE": Customer says thank you, ok bye, or confirms the call is finished ("theek hai shukriya", "ok bye").

Return ONLY a valid JSON object matching this exact schema:
{{
  "detected_intent": "ASK_DETAILS",
  "confidence": 0.96,
  "promised_date": "2026-11-05",
  "date_phrase": "5 tareekh",
  "agent_response": "Spoken Hinglish reply in Latin script",
  "should_end_call": false
}}"""

    raw_output = _call_gemini_rest(key, prompt, json_mode=True)
    if not raw_output:
        return None

    try:
        # Clean markdown fences if present
        cleaned = raw_output.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict) and "agent_response" in parsed and "detected_intent" in parsed:
            return parsed
    except Exception:
        pass
    return None


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
    If Gemini API key is present, uses Gemini Flash strictly conditioned
    on the state machine intent. Otherwise, gracefully uses verified fallback templates.
    """
    key = api_key if api_key is not None else get_runtime_gemini_key()
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

    prompt = f"""You are the voice of Backstop, an Indian revenue recovery AI assistant speaking on a phone call.
You must speak in natural, warm, respectful Hinglish (Hindi + English code-mixing in Latin script).

MANDATORY RULES:
1. The deterministic state machine has ALREADY decided the state and intent: {intent}.
2. You must NOT alter the facts, promise unauthorized discounts above 10%, or make false threats.
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

Output ONLY the exact spoken Hinglish sentence(s) in Latin script. Do not include quotes, markdown, or commentary."""

    generated = _call_gemini_rest(key, prompt, json_mode=False)
    if generated:
        clean_gen = generated.strip().strip('"').strip("'")
        if clean_gen:
            return {
                "text": clean_gen,
                "source": "gemini",
                "model": "gemini-2.5-flash",
                "intent": intent,
            }

    return {
        "text": fallback_text,
        "source": "fallback_template",
        "intent": intent,
        "note": "Gemini API unreachable or key invalid; fallback template used safely.",
    }
