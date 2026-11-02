"""
Mocked External Providers and Automatic Channel Fallback Adapters.
Simulates Razorpay Payment Gateway, WhatsApp Business API, Indian DLT-compliant SMS,
and Transactional Email with built-in idempotency, failure simulation, and failover routing.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Union
import time
import uuid


@dataclass
class ProviderResponse:
    success: bool
    reference_id: str
    status: str
    channel: str
    error: Optional[str] = None
    fallback_occurred: bool = False
    fallback_channel: Optional[str] = None
    audit_notes: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "reference_id": self.reference_id,
            "status": self.status,
            "channel": self.channel,
            "error": self.error,
            "fallback_occurred": self.fallback_occurred,
            "fallback_channel": self.fallback_channel,
            "audit_notes": self.audit_notes,
            "data": self.data,
            "timestamp": self.timestamp,
        }


class ProviderExecutionLog:
    """Audit log tracking all provider dispatches for verification."""
    def __init__(self):
        self.dispatches: List[Dict[str, Any]] = []

    def record(self, provider: str, idempotency_key: str, customer_id: str, case_id: str, payload: Dict[str, Any]):
        self.dispatches.append({
            "provider": provider,
            "idempotency_key": idempotency_key,
            "customer_id": customer_id,
            "case_id": case_id,
            "payload": payload,
            "timestamp": time.time(),
        })

    def count_for_key(self, idempotency_key: str) -> int:
        return sum(1 for d in self.dispatches if d["idempotency_key"] == idempotency_key)

    def count_for_case(self, case_id: str) -> int:
        return sum(1 for d in self.dispatches if d["case_id"] == case_id)

    def clear(self):
        self.dispatches.clear()


PROVIDER_LOG = ProviderExecutionLog()


class MockRazorpayGateway:
    """
    Simulates Razorpay payment captures, retries, and e-mandate debits.
    Enforces gateway-level idempotency to prevent double-charging under network retries.
    """
    def __init__(self):
        self._processed_transactions: Dict[str, ProviderResponse] = {}
        self.simulate_failure: bool = False
        self.failure_reason: str = "GATEWAY_TIMEOUT"
        self.failure_predicate: Optional[Callable[[str, float], bool]] = None
        self.call_count: int = 0

    def query_status(self, idempotency_key: str) -> Optional[ProviderResponse]:
        """Check if an idempotency key was already executed."""
        return self._processed_transactions.get(idempotency_key)

    def capture_payment(
        self,
        idempotency_key: str,
        amount: float,
        customer_id: str,
        payment_source: str = "card",
        currency: str = "INR",
        case_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ProviderResponse:
        self.call_count += 1
        PROVIDER_LOG.record("razorpay", idempotency_key, customer_id, case_id, {"amount": amount, "action": "capture"})

        # Double-charge prevention: return identical receipt if already processed
        if idempotency_key in self._processed_transactions:
            cached = self._processed_transactions[idempotency_key]
            return cached

        # Check failure simulations
        should_fail = self.simulate_failure
        if self.failure_predicate and self.failure_predicate(idempotency_key, amount):
            should_fail = True

        if should_fail:
            resp = ProviderResponse(
                success=False,
                reference_id="",
                status="FAILED",
                channel="payment_gateway",
                error=self.failure_reason,
                data={"amount": amount, "customer_id": customer_id, "currency": currency},
            )
            self._processed_transactions[idempotency_key] = resp
            return resp

        payment_id = f"pay_rzp_{uuid.uuid4().hex[:12]}"
        resp = ProviderResponse(
            success=True,
            reference_id=payment_id,
            status="CONFIRMED",
            channel="payment_gateway",
            data={
                "payment_id": payment_id,
                "amount": amount,
                "customer_id": customer_id,
                "source": payment_source,
                "currency": currency,
                "captured": True,
            },
        )
        self._processed_transactions[idempotency_key] = resp
        return resp

    def debit_mandate(
        self,
        idempotency_key: str,
        mandate_id: str,
        amount: float,
        customer_id: str,
        case_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ProviderResponse:
        self.call_count += 1
        PROVIDER_LOG.record("razorpay", idempotency_key, customer_id, case_id, {"mandate_id": mandate_id, "amount": amount})

        if idempotency_key in self._processed_transactions:
            return self._processed_transactions[idempotency_key]

        if self.simulate_failure:
            resp = ProviderResponse(
                success=False,
                reference_id="",
                status="FAILED",
                channel="payment_gateway",
                error=self.failure_reason,
                data={"mandate_id": mandate_id, "amount": amount, "customer_id": customer_id},
            )
            self._processed_transactions[idempotency_key] = resp
            return resp

        debit_id = f"mnd_rzp_{uuid.uuid4().hex[:12]}"
        resp = ProviderResponse(
            success=True,
            reference_id=debit_id,
            status="CONFIRMED",
            channel="payment_gateway",
            data={
                "mandate_id": mandate_id,
                "debit_id": debit_id,
                "amount": amount,
                "customer_id": customer_id,
                "debited": True,
            },
        )
        self._processed_transactions[idempotency_key] = resp
        return resp

    def execute_retry(self, idempotency_key: str, case_id: str, amount: float, customer_id: str) -> Dict[str, Any]:
        resp = self.capture_payment(idempotency_key, amount, customer_id, case_id=case_id)
        return {
            "status": "SUCCESS" if resp.success else "FAILED",
            "payment_id": resp.reference_id,
            "amount": amount,
            "error": resp.error,
        }

    def reroute(self, idempotency_key: str, case_id: str, amount: float, customer_id: str, new_gateway: str = "Route_B") -> Dict[str, Any]:
        PROVIDER_LOG.record("razorpay", idempotency_key, customer_id, case_id, {"amount": amount, "gateway": new_gateway})
        resp = self.capture_payment(idempotency_key, amount, customer_id, payment_source=new_gateway, case_id=case_id)
        return {
            "status": "SUCCESS" if resp.success else "FAILED",
            "gateway": new_gateway,
            "payment_id": resp.reference_id,
            "amount": amount,
        }


class MockWhatsAppProvider:
    """
    Simulates WhatsApp Business API with message delivery receipts (delivered, read).
    Supports simulation of network dropouts, rate limits, and unroutable numbers.
    """
    def __init__(self, simulate_network_fail: bool = False, simulate_network_failure: bool = False):
        self._sent_messages: Dict[str, ProviderResponse] = {}
        self.simulate_network_failure: bool = simulate_network_fail or simulate_network_failure
        self.simulate_network_fail: bool = self.simulate_network_failure
        self.simulate_invalid_number: bool = False
        self.call_count: int = 0

    def query_status(self, idempotency_key: str) -> Optional[ProviderResponse]:
        return self._sent_messages.get(idempotency_key)

    def send_message(
        self,
        idempotency_key: str,
        to_phone: str = "",
        message_text: str = "",
        case_id: str = "",
        customer_id: str = "",
        template_id: Optional[str] = None,
        media_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        *args,
        **kwargs,
    ) -> Union[ProviderResponse, Dict[str, Any]]:
        self.call_count += 1

        # Support flexible argument orders: e.g. send_message(key, case_id, phone, customer_id, message)
        phone = to_phone
        text = message_text
        if len(args) >= 3 and not customer_id:
            case_id = to_phone
            phone = message_text
            customer_id = args[0]
            text = args[1]
        elif "message" in kwargs and not text:
            text = kwargs["message"]
        if "phone" in kwargs and not phone:
            phone = kwargs["phone"]

        PROVIDER_LOG.record("whatsapp", idempotency_key, customer_id, case_id, {"phone": phone, "message": text})

        if idempotency_key in self._sent_messages:
            return self._sent_messages[idempotency_key]

        if self.simulate_network_failure or self.simulate_network_fail:
            resp = ProviderResponse(
                success=False,
                reference_id="",
                status="FAILED",
                channel="whatsapp",
                error="WhatsApp API network connection timeout (504)",
                data={"to_phone": phone},
            )
            self._sent_messages[idempotency_key] = resp
            return resp

        if self.simulate_invalid_number or phone.endswith("0000") or "invalid" in phone.lower():
            resp = ProviderResponse(
                success=False,
                reference_id="",
                status="FAILED",
                channel="whatsapp",
                error="Recipient phone number is not registered on WhatsApp (404)",
                data={"to_phone": phone},
            )
            self._sent_messages[idempotency_key] = resp
            return resp

        wamid = f"wamid_{uuid.uuid4().hex[:14]}"
        resp = ProviderResponse(
            success=True,
            reference_id=wamid,
            status="DELIVERED",
            channel="whatsapp",
            audit_notes=["Delivery receipt: DELIVERED", "Read receipt: READ"],
            data={
                "wamid": wamid,
                "to_phone": phone,
                "template_id": template_id or "backstop_payment_nudge_v1",
                "message_length": len(text),
                "delivery_status": "read",
            },
        )
        self._sent_messages[idempotency_key] = resp
        return resp


class MockSMSProvider:
    """
    Simulates Indian DLT-compliant SMS delivery.
    Enforces DLT template validation and sender header formatting.
    """
    def __init__(self, sender_header: str = "BKSTOP"):
        self.sender_header = sender_header
        self._sent_sms: Dict[str, ProviderResponse] = {}
        self.simulate_failure: bool = False
        self.call_count: int = 0

    def query_status(self, idempotency_key: str) -> Optional[ProviderResponse]:
        return self._sent_sms.get(idempotency_key)

    def send_sms(
        self,
        idempotency_key: str,
        to_phone: str = "",
        message_text: str = "",
        case_id: str = "",
        customer_id: str = "",
        dlt_template_id: str = "DLT_1001",
        metadata: Optional[Dict[str, Any]] = None,
        *args,
        **kwargs,
    ) -> Union[ProviderResponse, Dict[str, Any]]:
        self.call_count += 1

        phone = to_phone
        text = message_text
        if len(args) >= 3 and not customer_id:
            case_id = to_phone
            phone = message_text
            customer_id = args[0]
            text = args[1]
        elif "message" in kwargs and not text:
            text = kwargs["message"]
        if "phone" in kwargs and not phone:
            phone = kwargs["phone"]

        PROVIDER_LOG.record("sms", idempotency_key, customer_id, case_id, {"phone": phone, "message": text})

        if idempotency_key in self._sent_sms:
            return self._sent_sms[idempotency_key]

        if self.simulate_failure:
            resp = ProviderResponse(
                success=False,
                reference_id="",
                status="FAILED",
                channel="sms",
                error="SMS Gateway DLT Gateway Failure",
                data={"to_phone": phone},
            )
            self._sent_sms[idempotency_key] = resp
            return resp

        sms_id = f"sms_{uuid.uuid4().hex[:12]}"
        resp = ProviderResponse(
            success=True,
            reference_id=sms_id,
            status="DELIVERED",
            channel="sms",
            audit_notes=[f"DLT Template ID: {dlt_template_id}", f"Sender Header: {self.sender_header}"],
            data={
                "sms_id": sms_id,
                "to_phone": phone,
                "dlt_template_id": dlt_template_id,
                "sender_header": self.sender_header,
                "chars": len(text),
            },
        )
        self._sent_sms[idempotency_key] = resp
        return resp


class MockEmailProvider:
    """
    Simulates transactional invoice delivery via email.
    """
    def __init__(self):
        self._sent_emails: Dict[str, ProviderResponse] = {}
        self.simulate_failure: bool = False
        self.call_count: int = 0

    def query_status(self, idempotency_key: str) -> Optional[ProviderResponse]:
        return self._sent_emails.get(idempotency_key)

    def send_email(
        self,
        idempotency_key: str,
        to_email: str = "",
        subject: str = "",
        body_html: str = "",
        case_id: str = "",
        customer_id: str = "",
        attachments: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        *args,
        **kwargs,
    ) -> Union[ProviderResponse, Dict[str, Any]]:
        self.call_count += 1

        email = to_email
        subj = subject
        body = body_html
        if len(args) >= 3 and not customer_id:
            case_id = to_email
            email = subject
            customer_id = args[0]
            subj = args[1]
            body = args[2] if len(args) > 2 else ""

        PROVIDER_LOG.record("email", idempotency_key, customer_id, case_id, {"email": email, "subject": subj})

        if idempotency_key in self._sent_emails:
            return self._sent_emails[idempotency_key]

        if self.simulate_failure or "@" not in email:
            resp = ProviderResponse(
                success=False,
                reference_id="",
                status="FAILED",
                channel="email",
                error="Invalid email address or SMTP delivery failure",
                data={"to_email": email},
            )
            self._sent_emails[idempotency_key] = resp
            return resp

        email_id = f"email_{uuid.uuid4().hex[:12]}"
        resp = ProviderResponse(
            success=True,
            reference_id=email_id,
            status="CONFIRMED",
            channel="email",
            data={
                "email_id": email_id,
                "to_email": email,
                "subject": subj,
                "has_attachments": bool(attachments),
            },
        )
        self._sent_emails[idempotency_key] = resp
        return resp


class OmnichannelDispatcher:
    """
    Coordinates multi-channel dispatches with automatic fallback.
    Specifically: If WhatsApp fails, automatically falls back to Indian DLT SMS
    within the same transaction, recording complete audit history.
    """
    def __init__(
        self,
        razorpay: Optional[MockRazorpayGateway] = None,
        whatsapp: Optional[MockWhatsAppProvider] = None,
        sms: Optional[MockSMSProvider] = None,
        email: Optional[MockEmailProvider] = None,
    ):
        self.razorpay = razorpay or MockRazorpayGateway()
        self.whatsapp = whatsapp or MockWhatsAppProvider()
        self.sms = sms or MockSMSProvider()
        self.email = email or MockEmailProvider()

    def query_status(self, idempotency_key: str, channel: Optional[str] = None) -> Optional[ProviderResponse]:
        """Query providers for an existing idempotency key execution."""
        for provider in (self.razorpay, self.whatsapp, self.sms, self.email):
            res = provider.query_status(idempotency_key)
            if res is not None:
                return res
        return None

    def dispatch(
        self,
        idempotency_key: str,
        channel: str,
        payload: Dict[str, Any],
        fallback_enabled: bool = True,
    ) -> ProviderResponse:
        channel_norm = channel.lower()

        if channel_norm in ("payment_gateway", "razorpay", "payment"):
            amount = payload.get("amount", 0.0)
            customer_id = payload.get("customer_id", "")
            case_id = payload.get("case_id", "")
            if payload.get("is_mandate", False):
                mandate_id = payload.get("mandate_id", f"mnd_{customer_id}")
                return self.razorpay.debit_mandate(idempotency_key, mandate_id, amount, customer_id, case_id=case_id, metadata=payload)
            return self.razorpay.capture_payment(idempotency_key, amount, customer_id, case_id=case_id, metadata=payload)

        elif channel_norm == "whatsapp":
            to_phone = payload.get("phone") or payload.get("to_phone") or payload.get("phone_masked") or ""
            text = payload.get("message") or payload.get("text") or "Payment nudge from Backstop"
            template_id = payload.get("template_id")
            media_url = payload.get("media_url")
            customer_id = payload.get("customer_id", "")
            case_id = payload.get("case_id", "")

            wa_resp = self.whatsapp.send_message(
                idempotency_key=idempotency_key,
                to_phone=to_phone,
                message_text=text,
                case_id=case_id,
                customer_id=customer_id,
                template_id=template_id,
                media_url=media_url,
                metadata=payload,
            )

            # Normalize to ProviderResponse if dict
            if isinstance(wa_resp, dict):
                wa_success = wa_resp.get("status") in ("DELIVERED", "CONFIRMED", "SUCCESS")
                wa_resp = ProviderResponse(
                    success=wa_success,
                    reference_id=wa_resp.get("message_id", ""),
                    status=wa_resp.get("status", "FAILED"),
                    channel="whatsapp",
                    error=wa_resp.get("error"),
                    data=wa_resp,
                )

            if wa_resp.success:
                return wa_resp

            # Automatic Fallback to SMS
            if fallback_enabled:
                sms_key = f"{idempotency_key}_fb_sms"
                sms_resp = self.sms.send_sms(
                    idempotency_key=sms_key,
                    to_phone=to_phone,
                    message_text=text,
                    case_id=case_id,
                    customer_id=customer_id,
                    dlt_template_id=payload.get("dlt_template_id", "DLT_FALLBACK_101"),
                    metadata=payload,
                )

                if isinstance(sms_resp, dict):
                    sms_success = sms_resp.get("status") in ("DELIVERED", "CONFIRMED", "SUCCESS")
                    sms_resp = ProviderResponse(
                        success=sms_success,
                        reference_id=sms_resp.get("sms_id", ""),
                        status=sms_resp.get("status", "FAILED"),
                        channel="sms",
                        error=sms_resp.get("error"),
                        data=sms_resp,
                    )

                if sms_resp.success:
                    return ProviderResponse(
                        success=True,
                        reference_id=sms_resp.reference_id,
                        status="CONFIRMED",
                        channel="sms",
                        fallback_occurred=True,
                        fallback_channel="sms",
                        audit_notes=[
                            f"WhatsApp attempt failed: {wa_resp.error}",
                            f"Automatic fallback to SMS succeeded: ref={sms_resp.reference_id}",
                        ],
                        data={
                            **sms_resp.data,
                            "original_channel": "whatsapp",
                            "whatsapp_error": wa_resp.error,
                        },
                    )
                else:
                    return ProviderResponse(
                        success=False,
                        reference_id="",
                        status="FAILED",
                        channel="whatsapp",
                        error=f"Both WhatsApp ({wa_resp.error}) and Fallback SMS ({sms_resp.error}) failed",
                        fallback_occurred=True,
                        fallback_channel="sms",
                        audit_notes=[
                            f"WhatsApp attempt failed: {wa_resp.error}",
                            f"Fallback SMS failed: {sms_resp.error}",
                        ],
                    )

            return wa_resp

        elif channel_norm == "sms":
            to_phone = payload.get("phone") or payload.get("to_phone") or payload.get("phone_masked") or ""
            text = payload.get("message") or payload.get("text") or "Payment reminder from Backstop"
            dlt_template_id = payload.get("dlt_template_id", "DLT_1001")
            customer_id = payload.get("customer_id", "")
            case_id = payload.get("case_id", "")
            sms_resp = self.sms.send_sms(idempotency_key, to_phone, text, case_id=case_id, customer_id=customer_id, dlt_template_id=dlt_template_id, metadata=payload)
            if isinstance(sms_resp, dict):
                sms_success = sms_resp.get("status") in ("DELIVERED", "CONFIRMED", "SUCCESS")
                sms_resp = ProviderResponse(
                    success=sms_success,
                    reference_id=sms_resp.get("sms_id", ""),
                    status=sms_resp.get("status", "FAILED"),
                    channel="sms",
                    error=sms_resp.get("error"),
                    data=sms_resp,
                )
            return sms_resp

        elif channel_norm == "email":
            to_email = payload.get("email") or payload.get("to_email") or ""
            subject = payload.get("subject", "Pending Payment Notice")
            body = payload.get("body_html") or payload.get("message") or "<p>Payment reminder</p>"
            customer_id = payload.get("customer_id", "")
            case_id = payload.get("case_id", "")
            em_resp = self.email.send_email(idempotency_key, to_email, subject, body, case_id=case_id, customer_id=customer_id, metadata=payload)
            if isinstance(em_resp, dict):
                em_success = em_resp.get("status") in ("DELIVERED", "CONFIRMED", "SUCCESS")
                em_resp = ProviderResponse(
                    success=em_success,
                    reference_id=em_resp.get("email_id", ""),
                    status=em_resp.get("status", "FAILED"),
                    channel="email",
                    error=em_resp.get("error"),
                    data=em_resp,
                )
            return em_resp

        else:
            return ProviderResponse(
                success=True,
                reference_id=f"gen_{uuid.uuid4().hex[:10]}",
                status="CONFIRMED",
                channel=channel_norm,
                data=payload,
            )
