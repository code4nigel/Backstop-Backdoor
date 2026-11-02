"""
Backstop Hinglish Voice Dialogue State Machine & Intent Parser.
Deterministic state-machine-controlled conversation engine enforcing RBI fair recovery
guardrails, mid-call WhatsApp link dispatch, PTP logging, and dispute freezes.
"""

import os
import sys
import re
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, Any, Optional, Tuple, List

# Add parent path to support running directly from repository root or subfolder
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voice.mid_call_link import send_mid_call_paylink, mask_phone_number
from voice.prompts import generate_hinglish_response, get_fallback_phrasing

# Ensure UTF-8 output when supported by stream
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


class VoiceIntent(str, Enum):
    GREETING = "GREETING"
    PAY_NOW = "PAY_NOW"
    PROMISE_LATER = "PROMISE_LATER"
    SEND_LINK = "SEND_LINK"
    ALREADY_PAID = "ALREADY_PAID"
    INVOICE_DISPUTE = "INVOICE_DISPUTE"
    ANGER_HOSTILITY = "ANGER_HOSTILITY"
    OPT_OUT = "OPT_OUT"
    CLARIFY = "CLARIFY"
    UNKNOWN = "UNKNOWN"


class CallState(str, Enum):
    OUTBOUND_CONNECT = "OUTBOUND_CONNECT"
    GREETING_STAGED = "GREETING_STAGED"
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
        }


# Preset customer objection scenarios for interactive testing
CustomerObjection = [
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
    Deterministic State Machine Controlled Hinglish Recovery Engine.
    The LLM never chooses the next state; strict state machine transitions dictate
    the workflow and policy rule executions.
    """

    def __init__(
        self,
        case_id: str = "rec_case_89f02a",
        customer_id: str = "cust_in_9041",
        customer_name: str = "Rohan Gupta",
        phone: str = "+91 98765 43210",
        amount: float = 14500.0,
        reference_date: Optional[datetime] = None,
    ):
        self.case_id = case_id
        self.customer_id = customer_id
        self.customer_name = customer_name
        self.phone = phone
        self.phone_masked = mask_phone_number(phone)
        self.amount = amount
        # Deterministic simulation time baseline (defaults to 2026-11-02)
        self.reference_date = reference_date or datetime(2026, 11, 2, 11, 0, 0, tzinfo=timezone.utc)

        # State tracking
        self.call_state = CallState.OUTBOUND_CONNECT
        self.case_state = CaseState.READY
        self.history: List[SimulationTurnResult] = []

        # Operational registers
        self.registered_ptp: Optional[PromiseToPayRecord] = None
        self.dispute_task: Optional[DisputeReconTask] = None
        self.last_dispatched_link: Optional[Dict[str, Any]] = None
        self.dnd_opt_out: bool = False
        self.contact_hold_days: int = 0

    def start_call(self) -> SimulationTurnResult:
        """Initiates outbound call and stages greeting."""
        self.call_state = CallState.GREETING_STAGED
        self.case_state = CaseState.READY

        res = generate_hinglish_response(
            intent="GREETING",
            customer_name=self.customer_name,
            amount=self.amount,
            phone_masked=self.phone_masked,
            customer_utterance=None,
        )

        step = SimulationTurnResult(
            call_state=self.call_state,
            case_state=self.case_state,
            detected_intent=VoiceIntent.GREETING,
            confidence=1.0,
            fired_policy_rule="RULE_OUTBOUND_GREETING_STAGE",
            agent_response=res["text"],
            extracted_entities={"customer_name": self.customer_name, "amount": self.amount},
            customer_utterance="[CALL_CONNECTED]",
        )
        self.history.append(step)
        return step

    def parse_intent(self, text: str) -> Tuple[VoiceIntent, float, Dict[str, Any]]:
        """
        Deterministic intent classifier for Hinglish and English customer recovery utterances.
        Extracts intent, confidence score, and entities (dates, amounts).
        """
        clean = (text or "").strip().lower()

        # 1. OPT_OUT (Check high-priority compliance triggers first)
        if any(w in clean for w in ["dnd", "opt out", "opt-out", "list se hatao", "hata do", "number remove", "phone mat karna", "calling band"]):
            if "dnd" in clean or "list se hatao" in clean or "remove" in clean:
                return VoiceIntent.OPT_OUT, 0.99, {"action": "permanent_dnd"}

        # 2. ANGER / HOSTILITY
        hostility_markers = [
            "bakwas", "bakwaas", "tameez", "harass", "police", "court", "chup raho",
            "shutup", "fraud", "complaint", "gali", "chor", "stop calling"
        ]
        if any(w in clean for w in hostility_markers):
            return VoiceIntent.ANGER_HOSTILITY, 0.96, {"hostility_level": "HIGH"}

        # 3. ALREADY_PAID (Safety rule: customer claims payment was completed)
        already_paid_markers = [
            "already paid", "already pay", "already de diya", "pay kar diya",
            "pay kar chuka", "paise kat gaye", "kat chuke", "transfer kar diya tha",
            "shaam ko hi upi se", "kal hi pay kiya", "done payment", "payment kar chuka",
            "already done"
        ]
        if any(w in clean for w in already_paid_markers):
            return VoiceIntent.ALREADY_PAID, 0.98, {"claim": "payment_completed"}

        # 4. INVOICE DISPUTE
        dispute_markers = [
            "invoice galat", "bill galat", "discount nahi laga", "amount galat",
            "wrong bill", "wrong invoice", "extra charge", "po missing", "dispute"
        ]
        if any(w in clean for w in dispute_markers):
            return VoiceIntent.INVOICE_DISPUTE, 0.95, {"issue": "invoice_discrepancy"}

        # 5. SEND_LINK
        link_markers = [
            "link bhej do", "link bhejo", "whatsapp pe link", "whatsapp par link",
            "send link", "send payment link", "message me link", "link whatsapp",
            "whatsapp link"
        ]
        if any(w in clean for w in link_markers) and not any(w in clean for w in ["abhi kar deta", "salary"]):
            return VoiceIntent.SEND_LINK, 0.97, {"channel": "WHATSAPP"}

        # 6. PAY_NOW
        pay_now_markers = [
            "abhi kar deta", "abhi karta hu", "abhi pay kar", "pay right now",
            "pay now", "gpay se abhi", "abhi bhej raha", "online abhi", "ready to pay"
        ]
        if any(w in clean for w in pay_now_markers):
            return VoiceIntent.PAY_NOW, 0.96, {"payment_mode": "instant_online"}

        # 7. PROMISE_LATER
        ptp_markers = [
            "salary", "tareekh", "tarikh", "transfer karunga", "pay karunga",
            "dunga", "bhej dunga", "kal dunga", "parso", "next week", "monday",
            "promise", "paise aane do"
        ]
        if any(w in clean for w in ptp_markers) or re.search(r"\b\d{1,2}(th|st|nd|rd)?\s*(ko|tak|tareekh|tarikh)?\b", clean):
            date_str, date_phrase = self._extract_promise_date(clean)
            return VoiceIntent.PROMISE_LATER, 0.98, {
                "promised_date": date_str,
                "date_phrase": date_phrase,
                "amount": self.amount,
            }

        # 8. GREETING / ACKNOWLEDGMENT
        greeting_markers = ["hello", "namaste", "haan bolo", "kaun bol raha", "yes", "bataiye"]
        if any(clean.startswith(w) for w in greeting_markers) and len(clean.split()) <= 4:
            return VoiceIntent.GREETING, 0.85, {}

        # 9. Fallback / Clarification needed
        return VoiceIntent.CLARIFY, 0.50, {}

    def _extract_promise_date(self, text: str) -> Tuple[str, str]:
        """
        Extracts future promised payment date from Hinglish phrases.
        Example:
          - "5 tareekh ko" -> ("2026-11-05", "5 tareekh")
          - "salary aane do 5th ko" -> ("2026-11-05", "5 tareekh")
          - "kal dunga" -> ("2026-11-03", "kal")
          - "parso dunga" -> ("2026-11-04", "parso")
        """
        base = self.reference_date

        if "kal" in text:
            dt = base + timedelta(days=1)
            return dt.strftime("%Y-%m-%d"), "kal"
        if "parso" in text:
            dt = base + timedelta(days=2)
            return dt.strftime("%Y-%m-%d"), "parso"

        # Match day numbers: e.g. "5 tareekh", "5th", "5 ko", "10 tareekh"
        match = re.search(r"\b(\d{1,2})\s*(st|nd|rd|th)?\s*(tareekh|tarikh|ko|tak)?\b", text)
        if match:
            day_num = int(match.group(1))
            if 1 <= day_num <= 31:
                year = base.year
                month = base.month
                # If target day has already passed this month, target next month
                if day_num < base.day:
                    month = month + 1 if month < 12 else 1
                    year = year + 1 if month == 1 else year
                try:
                    dt = datetime(year, month, day_num, 9, 0, 0, tzinfo=timezone.utc)
                    return dt.strftime("%Y-%m-%d"), f"{day_num} tareekh"
                except ValueError:
                    pass

        # If only "salary aane do" is spoken without explicit number, default to 5th (standard Indian salary cycle)
        if "salary" in text:
            day_num = 5
            year = base.year
            month = base.month if base.day <= 5 else (base.month + 1 if base.month < 12 else 1)
            year = year + 1 if (base.day > 5 and base.month == 12) else year
            dt = datetime(year, month, day_num, 9, 0, 0, tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%d"), "5 tareekh"

        # Default fallback to 3 days out
        default_dt = base + timedelta(days=3)
        return default_dt.strftime("%Y-%m-%d"), f"{default_dt.day} tareekh"

    def step(self, customer_utterance: str) -> SimulationTurnResult:
        """
        Executes a deterministic state machine transition based on customer utterance.
        Strict transition rules ensure RBI compliance and instant de-escalation.
        """
        intent, confidence, entities = self.parse_intent(customer_utterance)

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

            resp = generate_hinglish_response(
                intent="PAY_NOW",
                customer_name=self.customer_name,
                amount=self.amount,
                phone_masked=self.phone_masked,
                customer_utterance=customer_utterance,
            )

            result = SimulationTurnResult(
                call_state=CallState.CALL_ENDED,
                case_state=self.case_state,
                detected_intent=intent,
                confidence=confidence,
                fired_policy_rule="RULE_PAY_NOW_LINK_DISPATCH",
                agent_response=resp["text"],
                extracted_entities=entities,
                dispatched_link=paylink_payload,
                call_ended=True,
                customer_utterance=customer_utterance,
            )
            self.history.append(result)
            return result

        # ------------------------------------------------------------------
        # BRANCH 2: PROMISE_LATER
        # ------------------------------------------------------------------
        if intent == VoiceIntent.PROMISE_LATER:
            self.call_state = CallState.PROMISED_CAPTURED
            self.case_state = CaseState.PROMISED

            promised_date = entities.get("promised_date", "2026-11-05")
            date_phrase = entities.get("date_phrase", "5 tareekh")

            # Register PTP
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

            # Attempt hook to engine.execution.ptp if present
            try:
                from engine.execution.ptp import register_promise
                register_promise(self.case_id, promised_date, self.amount)
            except Exception:
                pass  # Standalone mode handles this safely

            resp = generate_hinglish_response(
                intent="PROMISE_LATER",
                customer_name=self.customer_name,
                amount=self.amount,
                phone_masked=self.phone_masked,
                date_phrase=date_phrase,
                customer_utterance=customer_utterance,
            )

            result = SimulationTurnResult(
                call_state=CallState.CALL_ENDED,
                case_state=self.case_state,
                detected_intent=intent,
                confidence=confidence,
                fired_policy_rule="RULE_PTP_REGISTERED_HOLD_OUTREACH",
                agent_response=resp["text"],
                extracted_entities=entities,
                ptp_record=ptp,
                call_ended=True,
                customer_utterance=customer_utterance,
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

            resp = generate_hinglish_response(
                intent="SEND_LINK",
                customer_name=self.customer_name,
                amount=self.amount,
                phone_masked=self.phone_masked,
                customer_utterance=customer_utterance,
            )

            result = SimulationTurnResult(
                call_state=CallState.CALL_ENDED,
                case_state=self.case_state,
                detected_intent=intent,
                confidence=confidence,
                fired_policy_rule="RULE_MIDCALL_LINK_SENT",
                agent_response=resp["text"],
                extracted_entities=entities,
                dispatched_link=paylink_payload,
                call_ended=True,
                customer_utterance=customer_utterance,
            )
            self.history.append(result)
            return result

        # ------------------------------------------------------------------
        # BRANCH 4: ALREADY_PAID (Safety Rule: Immediate Freeze)
        # ------------------------------------------------------------------
        if intent == VoiceIntent.ALREADY_PAID:
            self.call_state = CallState.DISPUTE_FROZEN
            self.case_state = CaseState.DISPUTED

            # Generate Dispute Recon Task for human accounts team
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

            # Attempt hook to engine.execution.dispute if present
            try:
                from engine.execution.dispute import freeze_and_create_recon_task
                freeze_and_create_recon_task(self.case_id, "already_paid", customer_utterance)
            except Exception:
                pass

            resp = generate_hinglish_response(
                intent="ALREADY_PAID",
                customer_name=self.customer_name,
                amount=self.amount,
                phone_masked=self.phone_masked,
                customer_utterance=customer_utterance,
            )

            result = SimulationTurnResult(
                call_state=CallState.CALL_ENDED,
                case_state=self.case_state,
                detected_intent=intent,
                confidence=confidence,
                fired_policy_rule="RULE_DISPUTE_FREEZE_TRIGGERED",
                agent_response=resp["text"],
                extracted_entities=entities,
                dispute_task=task,
                call_ended=True,
                customer_utterance=customer_utterance,
            )
            self.history.append(result)
            return result

        # ------------------------------------------------------------------
        # BRANCH 5: INVOICE_DISPUTE
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

            resp = generate_hinglish_response(
                intent="INVOICE_DISPUTE",
                customer_name=self.customer_name,
                amount=self.amount,
                phone_masked=self.phone_masked,
                customer_utterance=customer_utterance,
            )

            result = SimulationTurnResult(
                call_state=CallState.CALL_ENDED,
                case_state=self.case_state,
                detected_intent=intent,
                confidence=confidence,
                fired_policy_rule="RULE_INVOICE_DISPUTE_FREEZE",
                agent_response=resp["text"],
                extracted_entities=entities,
                dispute_task=task,
                call_ended=True,
                customer_utterance=customer_utterance,
            )
            self.history.append(result)
            return result

        # ------------------------------------------------------------------
        # BRANCH 6: ANGER_HOSTILITY
        # ------------------------------------------------------------------
        if intent == VoiceIntent.ANGER_HOSTILITY:
            self.call_state = CallState.HOSTILITY_DEESCALATED
            self.case_state = CaseState.POLICY_BLOCKED
            self.contact_hold_days = 7

            resp = generate_hinglish_response(
                intent="ANGER_HOSTILITY",
                customer_name=self.customer_name,
                amount=self.amount,
                phone_masked=self.phone_masked,
                customer_utterance=customer_utterance,
            )

            result = SimulationTurnResult(
                call_state=CallState.CALL_ENDED,
                case_state=self.case_state,
                detected_intent=intent,
                confidence=confidence,
                fired_policy_rule="RULE_HOSTILITY_7DAY_DND_APPLIED",
                agent_response=resp["text"],
                extracted_entities={"contact_hold_days": 7, "escalated_to": "Senior Escalations Manager"},
                call_ended=True,
                customer_utterance=customer_utterance,
            )
            self.history.append(result)
            return result

        # ------------------------------------------------------------------
        # BRANCH 7: OPT_OUT
        # ------------------------------------------------------------------
        if intent == VoiceIntent.OPT_OUT:
            self.call_state = CallState.OPT_OUT_PROCESSED
            self.case_state = CaseState.POLICY_BLOCKED
            self.dnd_opt_out = True

            resp = generate_hinglish_response(
                intent="OPT_OUT",
                customer_name=self.customer_name,
                amount=self.amount,
                phone_masked=self.phone_masked,
                customer_utterance=customer_utterance,
            )

            result = SimulationTurnResult(
                call_state=CallState.CALL_ENDED,
                case_state=self.case_state,
                detected_intent=intent,
                confidence=confidence,
                fired_policy_rule="RULE_DND_OPT_OUT_RECORDED",
                agent_response=resp["text"],
                extracted_entities={"dnd_status": "REGISTERED_PERMANENT"},
                call_ended=True,
                customer_utterance=customer_utterance,
            )
            self.history.append(result)
            return result

        # ------------------------------------------------------------------
        # BRANCH 8: CLARIFY / RE-ASK
        # ------------------------------------------------------------------
        self.call_state = CallState.GREETING_STAGED
        resp = generate_hinglish_response(
            intent="CLARIFY",
            customer_name=self.customer_name,
            amount=self.amount,
            phone_masked=self.phone_masked,
            customer_utterance=customer_utterance,
        )

        result = SimulationTurnResult(
            call_state=self.call_state,
            case_state=self.case_state,
            detected_intent=intent,
            confidence=confidence,
            fired_policy_rule="RULE_CLARIFICATION_REASK",
            agent_response=resp["text"],
            extracted_entities=entities,
            call_ended=False,
            customer_utterance=customer_utterance,
        )
        self.history.append(result)
        return result


def run_interactive_cli():
    """
    Interactive Terminal Simulator for Demonstrating Hinglish Recovery Calls.
    Allows testing preset Indian customer responses or free-form text input.
    """
    sim = HinglishVoiceSimulator(
        case_id="rec_case_89f02a",
        customer_name="Rohan Gupta",
        phone="+91 98765 43210",
        amount=14500.0,
    )

    print("================================================================================")
    print("      BACKSTOP AI: HINGLISH VOICE RECOVERY SIMULATOR (CLI INTERACTION)         ")
    print("================================================================================")
    print(f"Target Case: {sim.case_id} | Customer: {sim.customer_name}")
    print(f"Phone: {sim.phone_masked} | Balance at Risk: INR {sim.amount:,.2f}")
    print("State Machine: Deterministic Policy Engine with Instant Compliance Guardrails")
    print("================================================================================\n")

    # Step 1: Outbound greeting
    init_step = sim.start_call()
    print("[SYSTEM]: Outbound recovery call connected.")
    print(f"AGENT  : \"{init_step.agent_response}\"\n")

    # Interactive loop
    while not sim.history[-1].call_ended:
        print("--------------------------------------------------------------------------------")
        print("CHOOSE A REALISTIC INDIAN CUSTOMER PRESET OR TYPE YOUR OWN UTTERANCE:")
        for idx, item in enumerate(CustomerObjection, 1):
            print(f"  [{idx}] {item['phrase']} ({item['label']})")
        print("  [C] Custom free-text input")
        print("  [Q] Exit call")
        print("--------------------------------------------------------------------------------")

        choice = input("Enter choice (1-7, C, or Q): ").strip()
        if choice.lower() == "q":
            print("Simulator exited by user.")
            break

        customer_text = ""
        if choice in [str(i) for i in range(1, len(CustomerObjection) + 1)]:
            customer_text = CustomerObjection[int(choice) - 1]["phrase"]
        elif choice.lower() == "c":
            customer_text = input("Customer says: ").strip()
        else:
            # Check if user directly typed text instead of menu number
            customer_text = choice

        if not customer_text:
            continue

        print(f"\nCUSTOMER: \"{customer_text}\"")

        # Execute turn
        step = sim.step(customer_text)

        # Print Guardrail Inspector sidecar
        print("\n+--- [GUARDRAIL INSPECTOR SIDECAR] ---------------------------------------------+")
        print(f"| Detected Intent    : {step.detected_intent.value:<56} |")
        print(f"| Confidence Score   : {step.confidence * 100:.1f}%{'':<51} |")
        print(f"| Fired Policy Rule  : {step.fired_policy_rule:<56} |")
        print(f"| Case State Change  : {sim.case_state.value:<56} |")

        if step.ptp_record:
            ptp_info = f"Date: {step.ptp_record.promised_date} | Reminder: 09:00 AM | Outreach Frozen"
            print(f"| PTP Registered     : {ptp_info:<56} |")

        if step.dispute_task:
            disp_info = f"Type: {step.dispute_task.dispute_type} | Task: {step.dispute_task.task_id[:16]}..."
            print(f"| Dispute Recon Task : {disp_info:<56} |")

        if step.dispatched_link:
            link_info = f"{step.dispatched_link['pay_link']} (Receipt: {step.dispatched_link['delivery_receipt'][:18]}...)"
            print(f"| WhatsApp PayLink   : {link_info:<56} |")

        print("+-------------------------------------------------------------------------------+\n")

        # Print agent spoken reply
        print(f"AGENT   : \"{step.agent_response}\"\n")

        if step.call_ended:
            print(f"[SYSTEM]: Call completed safely under state: {step.call_state.value}. Terminal status reached.\n")
            break


if __name__ == "__main__":
    run_interactive_cli()
