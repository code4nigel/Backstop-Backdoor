"""
Backstop Hinglish Voice Dialogue State Machine & Multi-Turn Conversational Intent Engine.
Supports both:
1. Live Google Gemini LLM multi-turn intent & Hinglish response generation (when GEMINI_API_KEY is set).
2. Rich multi-turn deterministic Hinglish/Hindi (Devanagari + Latin) + English conversational state machine
   that never gets stuck on single-turn replies or "Maaf kijiye" when the customer asks natural questions.
"""

import os
import sys
import re
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, Any, Optional, Tuple, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voice.mid_call_link import send_mid_call_paylink, mask_phone_number
from voice.prompts import (
    generate_hinglish_response,
    get_fallback_phrasing,
    analyze_and_respond_with_gemini,
    get_runtime_gemini_key,
)

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


class VoiceIntent(str, Enum):
    GREETING = "GREETING"
    ASK_DETAILS = "ASK_DETAILS"
    NEGOTIATE_DISCOUNT = "NEGOTIATE_DISCOUNT"
    PAY_NOW = "PAY_NOW"
    PROMISE_LATER = "PROMISE_LATER"
    SEND_LINK = "SEND_LINK"
    ALREADY_PAID = "ALREADY_PAID"
    INVOICE_DISPUTE = "INVOICE_DISPUTE"
    ANGER_HOSTILITY = "ANGER_HOSTILITY"
    OPT_OUT = "OPT_OUT"
    GOODBYE = "GOODBYE"
    CLARIFY = "CLARIFY"
    UNKNOWN = "UNKNOWN"


class CallState(str, Enum):
    OUTBOUND_CONNECT = "OUTBOUND_CONNECT"
    GREETING_STAGED = "GREETING_STAGED"
    IN_CONVERSATION = "IN_CONVERSATION"
    INTENT_CLASSIFICATION = "INTENT_CLASSIFICATION"
    PAY_NOW_PROCESSING = "PAY_NOW_PROCESSING"
    PROMISED_CAPTURED = "PROMISED_CAPTURED"
    LINK_DISPATCHED = "LINK_DISPATCHED"
    DISPUTE_FROZEN = "DISPUTE_FROZEN"
    INVOICE_DISPUTE_FROZEN = "INVOICE_DISPUTE_FROZEN"
    HOSTILITY_DEESCALATED = "HOSTILITY_DEESCALATED"
    OPT_OUT_PROCESSED = "OPT_OUT_PROCESSED"
    CALL_ENDED = "CALL_ENDED"


class CaseState(str, Enum):
    READY = "READY"
    EXECUTING = "EXECUTING"
    PROMISED = "PROMISED"
    DISPUTED = "DISPUTED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    RECOVERED = "RECOVERED"
    CLOSED = "CLOSED"


@dataclass
class PromiseToPayRecord:
    ptp_id: str
    case_id: str
    customer_id: str
    promised_date: str  # YYYY-MM-DD
    promised_amount: float
    status: str = "PENDING"
    created_at: float = field(default_factory=time.time)
    reminder_scheduled_for: str = ""
    outreach_frozen: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DisputeReconTask:
    task_id: str
    case_id: str
    customer_id: str
    dispute_type: str
    evidence_notes: str
    status: str = "AWAITING_HUMAN_RECONCILIATION"
    created_at: float = field(default_factory=time.time)
    freeze_outreach: bool = True
    assigned_team: str = "Accounts & Billing Reconciliation"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SimulationTurnResult:
    call_state: CallState
    case_state: CaseState
    detected_intent: VoiceIntent
    confidence: float
    fired_policy_rule: str
    agent_response: str
    extracted_entities: Dict[str, Any]
    dispatched_link: Optional[Dict[str, Any]] = None
    ptp_record: Optional[PromiseToPayRecord] = None
    dispute_task: Optional[DisputeReconTask] = None
    call_ended: bool = False
    customer_utterance: str = ""
    llm_source: str = "deterministic_engine"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "call_state": self.call_state.value,
            "case_state": self.case_state.value,
            "detected_intent": self.detected_intent.value,
            "confidence": self.confidence,
            "fired_policy_rule": self.fired_policy_rule,
            "agent_response": self.agent_response,
            "extracted_entities": self.extracted_entities,
            "dispatched_link": self.dispatched_link,
            "ptp_record": self.ptp_record.to_dict() if self.ptp_record else None,
            "dispute_task": self.dispute_task.to_dict() if self.dispute_task else None,
            "call_ended": self.call_ended,
            "customer_utterance": self.customer_utterance,
            "llm_source": self.llm_source,
        }


# Devanagari to Latin Hinglish normalization table so Chrome hi-IN microphone transcripts match seamlessly
DEVANAGARI_MAP = {
    "हाँ": "haan", "हां": "haan", "जी": "ji", "बोलो": "bolo", "बोलिए": "boliye",
    "बताइए": "bataiye", "बताओ": "batao", "क्या": "kya", "किस": "kis", "चीज़": "cheez",
    "चीज": "cheez", "पेमेंट": "payment", "बिल": "bill", "इनवॉइस": "invoice",
    "सैलरी": "salary", "तारीख": "tareekh", "को": "ko", "दूंगा": "dunga", "दूँगा": "dunga",
    "करूंगा": "karunga", "करूँगा": "karunga", "ट्रांसफर": "transfer", "कल": "kal",
    "परसों": "parso", "पैसे": "paise", "नहीं": "nahi", "अभी": "abhi", "देता": "deta",
    "हूँ": "hu", "हूं": "hu", "लिंक": "link", "भेज": "bhej", "भेजो": "bhejo",
    "दो": "do", "व्हाट्सएप": "whatsapp", "वाट्सएप": "whatsapp", "यूपीआई": "upi",
    "किया": "kiya", "दिया": "diya", "था": "tha", "चुका": "chuka", "कट": "kat",
    "गए": "gaye", "गलत": "galat", "डिस्काउंट": "discount", "कम": "kam",
    "बकवास": "bakwas", "बंद": "band", "तमीज़": "tameez", "तमीज": "tameez",
    "मत": "mat", "हटाओ": "hatao", "नंबर": "number", "डीएनडी": "dnd",
    "धन्यवाद": "dhanyawad", "शुक्रिया": "shukriya", "ठीक": "theek", "बाय": "bye",
    "समय": "samay", "बात": "baat", "करने": "karne", "सही": "sahi",
}


def normalize_hinglish_text(text: str) -> str:
    """Normalizes Devanagari Hindi words into Latin Hinglish equivalents for deterministic parsing."""
    out = (text or "").strip().lower()
    for dev, lat in DEVANAGARI_MAP.items():
        out = out.replace(dev, f" {lat} ")
    out = re.sub(r"\s+", " ", out).strip()
    return out


CustomerObjection = [
    {
        "id": "ask_details",
        "label": "Ask payment details (Multi-turn)",
        "phrase": "Haan boliye, yeh kis cheez ka payment pending dikha raha hai?",
    },
    {
        "id": "ptp_salary",
        "label": "Salary delay promise (5th)",
        "phrase": "Bhaiya abhi salary nahi aayi, 5 tareekh ko transfer karunga",
    },
    {
        "id": "already_paid",
        "label": "Already paid yesterday via UPI",
        "phrase": "Maine kal shaam ko hi UPI se pay kar diya tha, baar baar kyu call kar rahe ho?",
    },
    {
        "id": "pay_now",
        "label": "Agree to pay immediately",
        "phrase": "Haan, abhi kar deta hu GPay se",
    },
    {
        "id": "send_link",
        "label": "Ask for WhatsApp link",
        "phrase": "Mujhe WhatsApp pe link bhej do",
    },
    {
        "id": "invoice_dispute",
        "label": "Invoice discount dispute",
        "phrase": "Invoice galat hai, discount nahi laga tha",
    },
    {
        "id": "hostility",
        "label": "Hostile / aggressive complaint",
        "phrase": "Bakwas band karo, tameez nahi hai kya phone karne ki!",
    },
    {
        "id": "opt_out",
        "label": "DND / Opt-out request",
        "phrase": "Mera number list se hatao, DND register karo",
    },
]


class HinglishVoiceSimulator:
    """
    Deterministic + Gemini LLM Assisted Hinglish Recovery Engine.
    Policy rules and state transitions are enforced deterministically while allowing
    natural multi-turn dialogue in the live Voice Studio.
    """

    def __init__(
        self,
        case_id: str = "rec_case_89f02a",
        customer_id: str = "cust_in_9041",
        customer_name: str = "Rohan Gupta",
        phone: str = "+91 98765 43210",
        amount: float = 14500.0,
        leak_type: str = "b2b_invoice",
        predicted_cause: str = "cash_flow_timing",
        reference_date: Optional[datetime] = None,
    ):
        self.case_id = case_id
        self.customer_id = customer_id
        self.customer_name = customer_name
        self.phone = phone
        self.phone_masked = mask_phone_number(phone)
        self.amount = amount
        self.leak_type = leak_type
        self.predicted_cause = predicted_cause
        self.reference_date = reference_date or datetime(2026, 11, 2, 11, 0, 0, tzinfo=timezone.utc)

        self.call_state = CallState.OUTBOUND_CONNECT
        self.case_state = CaseState.READY
        self.history: List[SimulationTurnResult] = []
        self.dialogue_turns: List[Dict[str, str]] = []

        self.registered_ptp: Optional[PromiseToPayRecord] = None
        self.dispute_task: Optional[DisputeReconTask] = None
        self.last_dispatched_link: Optional[Dict[str, Any]] = None
        self.dnd_opt_out: bool = False
        self.contact_hold_days: int = 0

    def start_call(self) -> SimulationTurnResult:
        """Initiates outbound call and stages greeting."""
        self.call_state = CallState.GREETING_STAGED
        self.case_state = CaseState.READY
        self.dialogue_turns = []

        res = generate_hinglish_response(
            intent="GREETING",
            customer_name=self.customer_name,
            amount=self.amount,
            phone_masked=self.phone_masked,
            customer_utterance=None,
        )
        self.dialogue_turns.append({"role": "Agent", "text": res["text"]})

        step = SimulationTurnResult(
            call_state=self.call_state,
            case_state=self.case_state,
            detected_intent=VoiceIntent.GREETING,
            confidence=1.0,
            fired_policy_rule="RULE_OUTBOUND_GREETING_STAGE",
            agent_response=res["text"],
            extracted_entities={"customer_name": self.customer_name, "amount": self.amount},
            customer_utterance="[CALL_CONNECTED]",
            llm_source=res.get("source", "fallback_template"),
        )
        self.history.append(step)
        return step

    def parse_intent(self, text: str) -> Tuple[VoiceIntent, float, Dict[str, Any]]:
        """
        Multi-turn intent classifier supporting Hinglish, Hindi (Devanagari), and English.
        """
        clean = normalize_hinglish_text(text)

        # 1. OPT_OUT (High-priority compliance trigger)
        if any(w in clean for w in ["dnd", "opt out", "opt-out", "list se hatao", "hata do", "number remove", "phone mat karna", "calling band", "do not call"]):
            if "dnd" in clean or "list se" in clean or "hata" in clean or "remove" in clean or "do not call" in clean:
                return VoiceIntent.OPT_OUT, 0.99, {"action": "permanent_dnd"}

        # 2. ANGER / HOSTILITY
        hostility_markers = [
            "bakwas", "bakwaas", "tameez", "harass", "police", "court", "chup raho",
            "shutup", "shut up", "fraud", "complaint", "gali", "chor", "stop calling", "phone mat"
        ]
        if any(w in clean for w in hostility_markers):
            return VoiceIntent.ANGER_HOSTILITY, 0.96, {"hostility_level": "HIGH"}

        # 3. ALREADY_PAID (Safety rule: customer claims payment was completed)
        already_paid_markers = [
            "already paid", "already pay", "already de diya", "pay kar diya",
            "pay kar chuka", "paise kat gaye", "kat chuke", "transfer kar diya tha",
            "shaam ko hi upi se", "kal hi pay kiya", "done payment", "payment kar chuka",
            "already done", "upi se pay kiya", "paid yesterday", "bhej diya tha"
        ]
        if any(w in clean for w in already_paid_markers):
            return VoiceIntent.ALREADY_PAID, 0.98, {"claim": "payment_completed"}

        # 4. INVOICE DISPUTE
        dispute_markers = [
            "invoice galat", "bill galat", "discount nahi laga", "amount galat",
            "wrong bill", "wrong invoice", "extra charge", "po missing", "dispute", "service kharab"
        ]
        if any(w in clean for w in dispute_markers):
            return VoiceIntent.INVOICE_DISPUTE, 0.95, {"issue": "invoice_discrepancy"}

        # 5. SEND_LINK
        link_markers = [
            "link bhej do", "link bhejo", "whatsapp pe link", "whatsapp par link",
            "send link", "send payment link", "message me link", "link whatsapp",
            "whatsapp link", "link send", "whatsapp kar do", "bhej do link"
        ]
        if any(w in clean for w in link_markers) and not any(w in clean for w in ["abhi kar deta", "salary"]):
            return VoiceIntent.SEND_LINK, 0.97, {"channel": "WHATSAPP"}

        # 6. PAY_NOW
        pay_now_markers = [
            "abhi kar deta", "abhi karta hu", "abhi pay kar", "pay right now",
            "pay now", "gpay se abhi", "abhi bhej raha", "online abhi", "ready to pay",
            "abhi transfer kar", "kar deta hu abhi", "doing it now"
        ]
        if any(w in clean for w in pay_now_markers):
            return VoiceIntent.PAY_NOW, 0.96, {"payment_mode": "instant_online"}

        # 7. PROMISE_LATER
        ptp_markers = [
            "salary", "tareekh", "tarikh", "transfer karunga", "pay karunga",
            "dunga", "bhej dunga", "kal dunga", "parso", "next week", "monday",
            "promise", "paise aane do", "baad mein", "abhi paise nahi", "tight hai",
            "thoda time", "do din baad", "agle hafte", "kal karunga"
        ]
        if any(w in clean for w in ptp_markers) or re.search(r"\b\d{1,2}(th|st|nd|rd)?\s*(ko|tak|tareekh|tarikh)\b", clean):
            date_str, date_phrase = self._extract_promise_date(clean)
            return VoiceIntent.PROMISE_LATER, 0.98, {
                "promised_date": date_str,
                "date_phrase": date_phrase,
                "amount": self.amount,
            }

        # 8. NEGOTIATE_DISCOUNT
        discount_markers = [
            "discount", "waiver", "kam kar", "thoda kam", "concession", "mehenga",
            "late fee hata", "offer", "settle"
        ]
        if any(w in clean for w in discount_markers):
            return VoiceIntent.NEGOTIATE_DISCOUNT, 0.92, {"max_discount_pct": 10.0}

        # 9. GOODBYE / CALL CLOSURE
        goodbye_markers = [
            "bye", "good bye", "goodbye", "shukriya", "dhanyawad", "thank you",
            "thanks", "ok rakhta hu", "bas itna hi", "done thanks"
        ]
        if any(w in clean for w in goodbye_markers):
            return VoiceIntent.GOODBYE, 0.95, {}

        # 10. ASK_DETAILS / CONVERSATIONAL ACKNOWLEDGMENT ("Haan boliye", "Kis cheez ka?", "Yes tell me")
        details_markers = [
            "haan", "han", "yes", "bolo", "boliye", "bataiye", "batao", "kaun",
            "kis cheez", "kya hai", "kaunsa", "details", "pending", "why", "kiski",
            "what payment", "which invoice", "sahi samay", "baat kar", "go ahead",
            "hello", "namaste", "hi", "ok", "okay", "accha", "achha", "ji"
        ]
        if any(w in clean for w in details_markers):
            return VoiceIntent.ASK_DETAILS, 0.90, {}

        # 11. Fallback / Clarification needed
        return VoiceIntent.CLARIFY, 0.50, {}

    def _extract_promise_date(self, text: str) -> Tuple[str, str]:
        base = self.reference_date

        if "kal" in text or "tomorrow" in text:
            dt = base + timedelta(days=1)
            return dt.strftime("%Y-%m-%d"), "kal"
        if "parso" in text:
            dt = base + timedelta(days=2)
            return dt.strftime("%Y-%m-%d"), "parso"

        match = re.search(r"\b(\d{1,2})\s*(st|nd|rd|th)?\s*(tareekh|tarikh|ko|tak)?\b", text)
        if match:
            day_num = int(match.group(1))
            if 1 <= day_num <= 31:
                year = base.year
                month = base.month
                if day_num < base.day:
                    month = month + 1 if month < 12 else 1
                    year = year + 1 if month == 1 else year
                try:
                    dt = datetime(year, month, day_num, 9, 0, 0, tzinfo=timezone.utc)
                    return dt.strftime("%Y-%m-%d"), f"{day_num} tareekh"
                except ValueError:
                    pass

        if "salary" in text:
            day_num = 5
            year = base.year
            month = base.month if base.day <= 5 else (base.month + 1 if base.month < 12 else 1)
            year = year + 1 if (base.day > 5 and base.month == 12) else year
            dt = datetime(year, month, day_num, 9, 0, 0, tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%d"), "5 tareekh"

        default_dt = base + timedelta(days=3)
        return default_dt.strftime("%Y-%m-%d"), f"{default_dt.day} tareekh"

    def step(self, customer_utterance: str, multi_turn: bool = False) -> SimulationTurnResult:
        """
        Executes a conversation turn.
        - When a Gemini API key is configured and multi_turn=True, uses Gemini Flash to understand
          nuanced multi-turn Hinglish/Hindi/English dialogue while enforcing deterministic policy actions.
        - When multi_turn=True (used by the live Voice Studio UI), keeps the call alive across multiple turns
          for ASK_DETAILS, NEGOTIATE_DISCOUNT, SEND_LINK, PAY_NOW, and PROMISE_LATER until the user says goodbye
          or a hard compliance stop (ALREADY_PAID, INVOICE_DISPUTE, ANGER_HOSTILITY, OPT_OUT) triggers.
        - When multi_turn=False (default for unit tests), preserves exact single-turn terminal behavior.
        """
        self.dialogue_turns.append({"role": "Customer", "text": customer_utterance})

        det_intent, det_conf, det_entities = self.parse_intent(customer_utterance)
        llm_result = None
        llm_source = "deterministic_engine"

        # If Gemini API key is active in multi-turn studio mode, consult Gemini LLM
        if multi_turn and get_runtime_gemini_key():
            llm_result = analyze_and_respond_with_gemini(
                customer_utterance=customer_utterance,
                customer_name=self.customer_name,
                amount=self.amount,
                phone_masked=self.phone_masked,
                leak_type=self.leak_type,
                predicted_cause=self.predicted_cause,
                conversation_history=self.dialogue_turns[:-1],
            )

        if llm_result:
            llm_source = "gemini-2.5-flash"
            raw_intent = str(llm_result.get("detected_intent", "")).upper()
            # Safety override: if deterministic parser caught a hard compliance stop (OPT_OUT, ANGER, ALREADY_PAID, DISPUTE), enforce it
            if det_intent in (VoiceIntent.OPT_OUT, VoiceIntent.ANGER_HOSTILITY, VoiceIntent.ALREADY_PAID, VoiceIntent.INVOICE_DISPUTE):
                intent = det_intent
                confidence = det_conf
                entities = det_entities
            else:
                try:
                    intent = VoiceIntent(raw_intent)
                except ValueError:
                    intent = det_intent
                confidence = float(llm_result.get("confidence", 0.95))
                entities = dict(det_entities)
                if llm_result.get("promised_date"):
                    entities["promised_date"] = llm_result["promised_date"]
                if llm_result.get("date_phrase"):
                    entities["date_phrase"] = llm_result["date_phrase"]
            custom_agent_reply = llm_result.get("agent_response")
            llm_should_end = bool(llm_result.get("should_end_call", False))
        else:
            intent = det_intent
            confidence = det_conf
            entities = det_entities
            custom_agent_reply = None
            llm_should_end = False

        # ------------------------------------------------------------------
        # MULTI-TURN CONVERSATIONAL INTENTS (ASK_DETAILS, NEGOTIATE_DISCOUNT, GOODBYE)
        # ------------------------------------------------------------------
        if intent == VoiceIntent.ASK_DETAILS:
            self.call_state = CallState.IN_CONVERSATION
            reply_text = custom_agent_reply or get_fallback_phrasing(
                "ASK_DETAILS", customer_name=self.customer_name, amount=self.amount, phone_masked=self.phone_masked
            )
            self.dialogue_turns.append({"role": "Agent", "text": reply_text})
            res = SimulationTurnResult(
                call_state=self.call_state,
                case_state=self.case_state,
                detected_intent=intent,
                confidence=confidence,
                fired_policy_rule="RULE_CONVERSATIONAL_DETAILS_SHARED",
                agent_response=reply_text,
                extracted_entities={"leak_type": self.leak_type, "amount": self.amount},
                call_ended=False,
                customer_utterance=customer_utterance,
                llm_source=llm_source,
            )
            self.history.append(res)
            return res

        if intent == VoiceIntent.NEGOTIATE_DISCOUNT:
            self.call_state = CallState.IN_CONVERSATION
            reply_text = custom_agent_reply or get_fallback_phrasing(
                "NEGOTIATE_DISCOUNT", customer_name=self.customer_name, amount=self.amount, phone_masked=self.phone_masked
            )
            self.dialogue_turns.append({"role": "Agent", "text": reply_text})
            res = SimulationTurnResult(
                call_state=self.call_state,
                case_state=self.case_state,
                detected_intent=intent,
                confidence=confidence,
                fired_policy_rule="RULE_MARGIN_GUARDRAIL_10PCT_OFFERED",
                agent_response=reply_text,
                extracted_entities={"max_discount_pct": 10.0, "discounted_amount": round(self.amount * 0.9, 2)},
                call_ended=False,
                customer_utterance=customer_utterance,
                llm_source=llm_source,
            )
            self.history.append(res)
            return res

        if intent == VoiceIntent.GOODBYE:
            self.call_state = CallState.CALL_ENDED
            reply_text = custom_agent_reply or get_fallback_phrasing(
                "GOODBYE", customer_name=self.customer_name, amount=self.amount, phone_masked=self.phone_masked
            )
            self.dialogue_turns.append({"role": "Agent", "text": reply_text})
            res = SimulationTurnResult(
                call_state=CallState.CALL_ENDED,
                case_state=self.case_state,
                detected_intent=intent,
                confidence=confidence,
                fired_policy_rule="RULE_POLITE_CALL_CLOSURE",
                agent_response=reply_text,
                extracted_entities={},
                call_ended=True,
                customer_utterance=customer_utterance,
                llm_source=llm_source,
            )
            self.history.append(res)
            return res

        # ------------------------------------------------------------------
        # BRANCH 1: PAY_NOW
        # ------------------------------------------------------------------
        if intent == VoiceIntent.PAY_NOW:
            self.call_state = CallState.PAY_NOW_PROCESSING
            self.case_state = CaseState.EXECUTING
            paylink_payload = send_mid_call_paylink(
                case_id=self.case_id,
                customer_phone=self.phone,
                amount=self.amount,
            )
            self.last_dispatched_link = paylink_payload

            if custom_agent_reply:
                reply_text = custom_agent_reply
            else:
                resp = generate_hinglish_response(
                    intent="PAY_NOW",
                    customer_name=self.customer_name,
                    amount=self.amount,
                    phone_masked=self.phone_masked,
                    customer_utterance=customer_utterance,
                )
                reply_text = resp["text"]
                llm_source = resp.get("source", llm_source)

            self.dialogue_turns.append({"role": "Agent", "text": reply_text})
            end_flag = (not multi_turn) or llm_should_end
            result = SimulationTurnResult(
                call_state=CallState.CALL_ENDED if end_flag else CallState.PAY_NOW_PROCESSING,
                case_state=self.case_state,
                detected_intent=intent,
                confidence=confidence,
                fired_policy_rule="RULE_PAY_NOW_LINK_DISPATCH",
                agent_response=reply_text,
                extracted_entities=entities,
                dispatched_link=paylink_payload,
                call_ended=end_flag,
                customer_utterance=customer_utterance,
                llm_source=llm_source,
            )
            self.history.append(result)
            return result

        # ------------------------------------------------------------------
        # BRANCH 2: PROMISE_LATER
        # ------------------------------------------------------------------
        if intent == VoiceIntent.PROMISE_LATER:
            self.call_state = CallState.PROMISED_CAPTURED
            self.case_state = CaseState.PROMISED

            promised_date = entities.get("promised_date")
            date_phrase = entities.get("date_phrase")
            if not promised_date or not date_phrase:
                promised_date, date_phrase = self._extract_promise_date(normalize_hinglish_text(customer_utterance))

            ptp = PromiseToPayRecord(
                ptp_id=f"ptp_{self.case_id}_{int(time.time())}",
                case_id=self.case_id,
                customer_id=self.customer_id,
                promised_date=promised_date,
                promised_amount=self.amount,
                reminder_scheduled_for=f"{promised_date} 09:00:00",
                outreach_frozen=True,
            )
            self.registered_ptp = ptp

            try:
                from engine.execution.ptp import register_promise
                register_promise(self.case_id, promised_date, self.amount)
            except Exception:
                pass

            if custom_agent_reply:
                reply_text = custom_agent_reply
            else:
                resp = generate_hinglish_response(
                    intent="PROMISE_LATER",
                    customer_name=self.customer_name,
                    amount=self.amount,
                    phone_masked=self.phone_masked,
                    date_phrase=date_phrase,
                    customer_utterance=customer_utterance,
                )
                reply_text = resp["text"]
                llm_source = resp.get("source", llm_source)

            self.dialogue_turns.append({"role": "Agent", "text": reply_text})
            end_flag = (not multi_turn) or llm_should_end
            result = SimulationTurnResult(
                call_state=CallState.CALL_ENDED if end_flag else CallState.PROMISED_CAPTURED,
                case_state=self.case_state,
                detected_intent=intent,
                confidence=confidence,
                fired_policy_rule="RULE_PTP_REGISTERED_HOLD_OUTREACH",
                agent_response=reply_text,
                extracted_entities=entities,
                ptp_record=ptp,
                call_ended=end_flag,
                customer_utterance=customer_utterance,
                llm_source=llm_source,
            )
            self.history.append(result)
            return result

        # ------------------------------------------------------------------
        # BRANCH 3: SEND_LINK
        # ------------------------------------------------------------------
        if intent == VoiceIntent.SEND_LINK:
            self.call_state = CallState.LINK_DISPATCHED
            self.case_state = CaseState.EXECUTING

            paylink_payload = send_mid_call_paylink(
                case_id=self.case_id,
                customer_phone=self.phone,
                amount=self.amount,
            )
            self.last_dispatched_link = paylink_payload

            if custom_agent_reply:
                reply_text = custom_agent_reply
            else:
                resp = generate_hinglish_response(
                    intent="SEND_LINK",
                    customer_name=self.customer_name,
                    amount=self.amount,
                    phone_masked=self.phone_masked,
                    customer_utterance=customer_utterance,
                )
                reply_text = resp["text"]
                llm_source = resp.get("source", llm_source)

            self.dialogue_turns.append({"role": "Agent", "text": reply_text})
            end_flag = (not multi_turn) or llm_should_end
            result = SimulationTurnResult(
                call_state=CallState.CALL_ENDED if end_flag else CallState.LINK_DISPATCHED,
                case_state=self.case_state,
                detected_intent=intent,
                confidence=confidence,
                fired_policy_rule="RULE_MIDCALL_LINK_SENT",
                agent_response=reply_text,
                extracted_entities=entities,
                dispatched_link=paylink_payload,
                call_ended=end_flag,
                customer_utterance=customer_utterance,
                llm_source=llm_source,
            )
            self.history.append(result)
            return result

        # ------------------------------------------------------------------
        # BRANCH 4: ALREADY_PAID (Hard Compliance Stop: Immediate Freeze)
        # ------------------------------------------------------------------
        if intent == VoiceIntent.ALREADY_PAID:
            self.call_state = CallState.DISPUTE_FROZEN
            self.case_state = CaseState.DISPUTED

            task = DisputeReconTask(
                task_id=f"recon_{self.case_id}_{int(time.time())}",
                case_id=self.case_id,
                customer_id=self.customer_id,
                dispute_type="already_paid",
                evidence_notes=f"Customer reported payment already made: '{customer_utterance}'",
                freeze_outreach=True,
                assigned_team="Payment Settlement Reconciliation",
            )
            self.dispute_task = task

            try:
                from engine.execution.dispute import freeze_and_create_recon_task
                freeze_and_create_recon_task(self.case_id, "already_paid", customer_utterance)
            except Exception:
                pass

            if custom_agent_reply:
                reply_text = custom_agent_reply
            else:
                resp = generate_hinglish_response(
                    intent="ALREADY_PAID",
                    customer_name=self.customer_name,
                    amount=self.amount,
                    phone_masked=self.phone_masked,
                    customer_utterance=customer_utterance,
                )
                reply_text = resp["text"]
                llm_source = resp.get("source", llm_source)

            self.dialogue_turns.append({"role": "Agent", "text": reply_text})
            result = SimulationTurnResult(
                call_state=CallState.CALL_ENDED,
                case_state=self.case_state,
                detected_intent=intent,
                confidence=confidence,
                fired_policy_rule="RULE_DISPUTE_FREEZE_TRIGGERED",
                agent_response=reply_text,
                extracted_entities=entities,
                dispute_task=task,
                call_ended=True,
                customer_utterance=customer_utterance,
                llm_source=llm_source,
            )
            self.history.append(result)
            return result

        # ------------------------------------------------------------------
        # BRANCH 5: INVOICE_DISPUTE (Hard Compliance Stop: Immediate Freeze)
        # ------------------------------------------------------------------
        if intent == VoiceIntent.INVOICE_DISPUTE:
            self.call_state = CallState.INVOICE_DISPUTE_FROZEN
            self.case_state = CaseState.DISPUTED

            task = DisputeReconTask(
                task_id=f"recon_{self.case_id}_{int(time.time())}",
                case_id=self.case_id,
                customer_id=self.customer_id,
                dispute_type="invoice_discount_discrepancy",
                evidence_notes=f"Customer disputes invoice calculation: '{customer_utterance}'",
                freeze_outreach=True,
                assigned_team="B2B Billing Operations",
            )
            self.dispute_task = task

            if custom_agent_reply:
                reply_text = custom_agent_reply
            else:
                resp = generate_hinglish_response(
                    intent="INVOICE_DISPUTE",
                    customer_name=self.customer_name,
                    amount=self.amount,
                    phone_masked=self.phone_masked,
                    customer_utterance=customer_utterance,
                )
                reply_text = resp["text"]
                llm_source = resp.get("source", llm_source)

            self.dialogue_turns.append({"role": "Agent", "text": reply_text})
            result = SimulationTurnResult(
                call_state=CallState.CALL_ENDED,
                case_state=self.case_state,
                detected_intent=intent,
                confidence=confidence,
                fired_policy_rule="RULE_INVOICE_DISPUTE_FREEZE",
                agent_response=reply_text,
                extracted_entities=entities,
                dispute_task=task,
                call_ended=True,
                customer_utterance=customer_utterance,
                llm_source=llm_source,
            )
            self.history.append(result)
            return result

        # ------------------------------------------------------------------
        # BRANCH 6: ANGER_HOSTILITY (Hard Compliance Stop: 7-Day Hold)
        # ------------------------------------------------------------------
        if intent == VoiceIntent.ANGER_HOSTILITY:
            self.call_state = CallState.HOSTILITY_DEESCALATED
            self.case_state = CaseState.POLICY_BLOCKED
            self.contact_hold_days = 7

            if custom_agent_reply:
                reply_text = custom_agent_reply
            else:
                resp = generate_hinglish_response(
                    intent="ANGER_HOSTILITY",
                    customer_name=self.customer_name,
                    amount=self.amount,
                    phone_masked=self.phone_masked,
                    customer_utterance=customer_utterance,
                )
                reply_text = resp["text"]
                llm_source = resp.get("source", llm_source)

            self.dialogue_turns.append({"role": "Agent", "text": reply_text})
            result = SimulationTurnResult(
                call_state=CallState.CALL_ENDED,
                case_state=self.case_state,
                detected_intent=intent,
                confidence=confidence,
                fired_policy_rule="RULE_HOSTILITY_7DAY_DND_APPLIED",
                agent_response=reply_text,
                extracted_entities={"contact_hold_days": 7, "escalated_to": "Senior Escalations Manager"},
                call_ended=True,
                customer_utterance=customer_utterance,
                llm_source=llm_source,
            )
            self.history.append(result)
            return result

        # ------------------------------------------------------------------
        # BRANCH 7: OPT_OUT (Hard Compliance Stop: Permanent DND)
        # ------------------------------------------------------------------
        if intent == VoiceIntent.OPT_OUT:
            self.call_state = CallState.OPT_OUT_PROCESSED
            self.case_state = CaseState.POLICY_BLOCKED
            self.dnd_opt_out = True

            if custom_agent_reply:
                reply_text = custom_agent_reply
            else:
                resp = generate_hinglish_response(
                    intent="OPT_OUT",
                    customer_name=self.customer_name,
                    amount=self.amount,
                    phone_masked=self.phone_masked,
                    customer_utterance=customer_utterance,
                )
                reply_text = resp["text"]
                llm_source = resp.get("source", llm_source)

            self.dialogue_turns.append({"role": "Agent", "text": reply_text})
            result = SimulationTurnResult(
                call_state=CallState.CALL_ENDED,
                case_state=self.case_state,
                detected_intent=intent,
                confidence=confidence,
                fired_policy_rule="RULE_DND_OPT_OUT_RECORDED",
                agent_response=reply_text,
                extracted_entities={"dnd_status": "REGISTERED_PERMANENT"},
                call_ended=True,
                customer_utterance=customer_utterance,
                llm_source=llm_source,
            )
            self.history.append(result)
            return result

        # ------------------------------------------------------------------
        # BRANCH 8: CLARIFY / CONVERSATIONAL RE-ASK
        # ------------------------------------------------------------------
        self.call_state = CallState.IN_CONVERSATION
        if custom_agent_reply:
            reply_text = custom_agent_reply
        else:
            resp = generate_hinglish_response(
                intent="CLARIFY",
                customer_name=self.customer_name,
                amount=self.amount,
                phone_masked=self.phone_masked,
                customer_utterance=customer_utterance,
            )
            reply_text = resp["text"]
            llm_source = resp.get("source", llm_source)

        self.dialogue_turns.append({"role": "Agent", "text": reply_text})
        result = SimulationTurnResult(
            call_state=self.call_state,
            case_state=self.case_state,
            detected_intent=intent,
            confidence=confidence,
            fired_policy_rule="RULE_CLARIFICATION_REASK",
            agent_response=reply_text,
            extracted_entities=entities,
            call_ended=False,
            customer_utterance=customer_utterance,
            llm_source=llm_source,
        )
        self.history.append(result)
        return result
