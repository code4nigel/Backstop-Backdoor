"""
Workflow Scheduler, Delayed Retries & Omnichannel Sequencer.
Handles salary-credit timing, recurring e-mandate retry schedules,
omnichannel escalation ladder, and cross-channel contact suppression.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple, Union
import time

from engine.models import ActionType, RecoveryCase


@dataclass
class ScheduledTouch:
    touch_number: int
    case_id: str
    customer_id: str
    channel: str
    action_type: ActionType
    scheduled_time: datetime
    description: str
    delay_hours: float
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "touch_number": self.touch_number,
            "case_id": self.case_id,
            "customer_id": self.customer_id,
            "channel": self.channel,
            "action_type": self.action_type.value if isinstance(self.action_type, ActionType) else str(self.action_type),
            "scheduled_time": self.scheduled_time.isoformat(),
            "description": self.description,
            "delay_hours": self.delay_hours,
            "payload": self.payload,
        }


@dataclass
class ScheduledRetry:
    case_id: str
    action_type: ActionType
    scheduled_time: datetime
    reason: str
    delay_days: int
    metadata: Dict[str, Any] = field(default_factory=dict)


def calculate_salary_retry_time(
    current_time: Optional[datetime] = None,
    is_salary_week: Optional[bool] = None,
) -> datetime:
    """
    If failure cause is insufficient_funds and today is not salary week,
    schedules retry for the 1st of the upcoming month at 10:00 AM local time.
    """
    now = current_time or datetime.now()

    # Determine if salary week: typically 28th-31st or 1st-5th of month in India
    salary_week = is_salary_week if is_salary_week is not None else (now.day <= 5 or now.day >= 28)

    if salary_week:
        # During salary week, retry in 24 hours at 10:00 AM
        next_day = now + timedelta(days=1)
        return datetime(next_day.year, next_day.month, next_day.day, 10, 0, 0)

    # Next month 1st at 10:00 AM
    if now.month == 12:
        return datetime(now.year + 1, 1, 1, 10, 0, 0)
    else:
        return datetime(now.year, now.month + 1, 1, 10, 0, 0)


def should_suppress_contact(
    customer_id: str,
    current_time: Union[float, datetime],
    contact_history: List[Dict[str, Any]],
    cooldown_hours: float = 12.0,
) -> Tuple[bool, str]:
    """
    Cross-Channel Duplicate Contact Suppression Rule:
    If any customer-facing contact (SMS, WhatsApp, Voice, Email) occurred
    within the last 12 hours, suppress new outbound messages across all channels to prevent spamming.
    """
    if isinstance(current_time, datetime):
        current_ts = current_time.timestamp()
    else:
        current_ts = float(current_time)

    customer_facing_channels = {"whatsapp", "sms", "voice", "email", "call"}

    recent_contact = None
    min_diff = float("inf")

    for contact in contact_history:
        if contact.get("customer_id") != customer_id:
            continue

        channel = contact.get("channel", "").lower()
        if channel not in customer_facing_channels:
            continue

        raw_ts = contact.get("timestamp") or contact.get("ts") or contact.get("created_at")
        if raw_ts is None:
            continue
        if isinstance(raw_ts, datetime):
            c_ts = raw_ts.timestamp()
        else:
            c_ts = float(raw_ts)

        diff_seconds = current_ts - c_ts
        if 0 <= diff_seconds < (cooldown_hours * 3600):
            if diff_seconds < min_diff:
                min_diff = diff_seconds
                recent_contact = (channel, diff_seconds / 3600.0)

    if recent_contact:
        ch, hrs_ago = recent_contact
        return True, f"Contact suppressed: Customer contacted via {ch} {hrs_ago:.1f}h ago (< {cooldown_hours:.1f}h cooldown)"

    return False, ""


class WorkflowScheduler:
    """
    Orchestrates retry planning, mandate sequences, omnichannel escalations,
    and contact suppression checks.
    """
    def __init__(self, cooldown_hours: float = 12.0):
        self.cooldown_hours = cooldown_hours

    def plan_insufficient_funds_retry(
        self,
        case: RecoveryCase,
        current_time: Optional[datetime] = None,
    ) -> ScheduledRetry:
        """Schedules delayed retry based on salary-cycle timing."""
        now = current_time or datetime.now()
        is_salary_week = getattr(case, "is_salary_week", False)
        scheduled_dt = calculate_salary_retry_time(now, is_salary_week=is_salary_week)
        delay_days = (scheduled_dt.date() - now.date()).days

        return ScheduledRetry(
            case_id=case.case_id,
            action_type=ActionType.RETRY_DELAYED,
            scheduled_time=scheduled_dt,
            reason="insufficient_funds_salary_cycle",
            delay_days=max(1, delay_days),
            metadata={
                "target_date": scheduled_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "is_salary_week": is_salary_week,
            },
        )

    def plan_salary_retry(self, current_dt: Optional[datetime] = None) -> Dict[str, Any]:
        """Align retry to 1st of upcoming month at 10:00 AM local time."""
        target = calculate_salary_retry_time(current_dt, is_salary_week=False)
        return {
            "scheduled_time": target.isoformat(),
            "reason": "Aligned to upcoming month-beginning salary credit window",
        }

    def plan_mandate_sequence(self, cycle_day: int = 0) -> Dict[str, Any]:
        """RBI e-Mandate Sequence metadata."""
        return {
            "pre_debit_alert_offset_hours": 24,
            "retry_1_offset_hours": 48,
            "retry_2_offset_hours": 96,
            "compliance_rule": "RBI 24-hr pre-debit customer notification mandate",
        }

    def generate_mandate_sequence(
        self,
        case_id: str,
        customer_id: str,
        failure_time: Optional[datetime] = None,
        amount: float = 0.0,
    ) -> List[ScheduledTouch]:
        """
        Recurring Subscription Mandate Retry Sequencer:
        - Day 0: Failure occurs (baseline).
        - Day +1: Pre-debit alert via WhatsApp.
        - Day +2 (24 hrs later): Execute automated mandate retry.
        - Day +4: Final retry before subscription pause.
        """
        t0 = failure_time or datetime.now()

        t_plus_1 = t0 + timedelta(days=1)
        t_plus_2 = t0 + timedelta(days=2)
        t_plus_4 = t0 + timedelta(days=4)

        touches = [
            ScheduledTouch(
                touch_number=1,
                case_id=case_id,
                customer_id=customer_id,
                channel="whatsapp",
                action_type=ActionType.WHATSAPP_NUDGE,
                scheduled_time=t_plus_1,
                description="Day +1: Send pre-debit alert via WhatsApp",
                delay_hours=24.0,
                payload={
                    "amount": amount,
                    "template": "mandate_pre_debit_alert",
                    "text": f"Backstop Alert: Your subscription payment of ₹{amount:,.2f} will be debited tomorrow. Please ensure sufficient balance.",
                },
            ),
            ScheduledTouch(
                touch_number=2,
                case_id=case_id,
                customer_id=customer_id,
                channel="payment_gateway",
                action_type=ActionType.MANDATE_RETRY_SEQUENCE,
                scheduled_time=t_plus_2,
                description="Day +2: Execute automated mandate retry",
                delay_hours=48.0,
                payload={
                    "amount": amount,
                    "is_mandate": True,
                    "retry_attempt": 1,
                },
            ),
            ScheduledTouch(
                touch_number=3,
                case_id=case_id,
                customer_id=customer_id,
                channel="payment_gateway",
                action_type=ActionType.MANDATE_RETRY_SEQUENCE,
                scheduled_time=t_plus_4,
                description="Day +4: Final retry before subscription pause",
                delay_hours=96.0,
                payload={
                    "amount": amount,
                    "is_mandate": True,
                    "retry_attempt": 2,
                    "is_final": True,
                },
            ),
        ]
        return touches

    def generate_omnichannel_ladder(
        self,
        case_id: str,
        customer_id: str,
        start_time: Optional[datetime] = None,
        amount: float = 0.0,
        pay_link: str = "https://rzp.io/l/backstop_recovery",
    ) -> List[ScheduledTouch]:
        """
        Omnichannel Escalation Ladder:
        - Touch 1 (Day 0): Friendly WhatsApp nudge with secure paylink.
        - Touch 2 (48 hrs later if unpaid): SMS reminder.
        - Touch 3 (96 hrs later): Interactive Hinglish voice call.
        - Touch 4 (7 days / 168 hrs later): Escalated to human accounts manager.
        """
        t0 = start_time or datetime.now()

        return [
            ScheduledTouch(
                touch_number=1,
                case_id=case_id,
                customer_id=customer_id,
                channel="whatsapp",
                action_type=ActionType.WHATSAPP_NUDGE,
                scheduled_time=t0,
                description="Touch 1: Friendly WhatsApp nudge with secure paylink",
                delay_hours=0.0,
                payload={
                    "amount": amount,
                    "pay_link": pay_link,
                    "message": f"Hi! Your payment of ₹{amount:,.2f} could not be processed. Complete it securely here: {pay_link}",
                },
            ),
            ScheduledTouch(
                touch_number=2,
                case_id=case_id,
                customer_id=customer_id,
                channel="sms",
                action_type=ActionType.REMINDER_SMS,
                scheduled_time=t0 + timedelta(hours=48),
                description="Touch 2 (48h later): SMS reminder",
                delay_hours=48.0,
                payload={
                    "amount": amount,
                    "pay_link": pay_link,
                    "dlt_template_id": "DLT_1002",
                    "message": f"Payment Reminder: Pending ₹{amount:,.2f}. Pay via {pay_link} - Backstop",
                },
            ),
            ScheduledTouch(
                touch_number=3,
                case_id=case_id,
                customer_id=customer_id,
                channel="voice",
                action_type=ActionType.VOICE_CALL_HINGLISH,
                scheduled_time=t0 + timedelta(hours=96),
                description="Touch 3 (96h later): Interactive Hinglish voice call",
                delay_hours=96.0,
                payload={
                    "amount": amount,
                    "language": "hinglish",
                    "intent_target": "recovery_call",
                },
            ),
            ScheduledTouch(
                touch_number=4,
                case_id=case_id,
                customer_id=customer_id,
                channel="human",
                action_type=ActionType.HUMAN_ESCALATION,
                scheduled_time=t0 + timedelta(days=7),
                description="Touch 4 (7 days later): Escalated to human accounts manager",
                delay_hours=168.0,
                payload={
                    "amount": amount,
                    "escalation_tier": "accounts_manager",
                    "reason": "Exhausted automated omnichannel recovery ladder",
                },
            ),
        ]

    def check_suppression(
        self,
        customer_id: str,
        current_time: Union[float, datetime],
        contact_history: List[Dict[str, Any]],
    ) -> Tuple[bool, str]:
        return should_suppress_contact(customer_id, current_time, contact_history, self.cooldown_hours)


# Alias
ExecutionScheduler = WorkflowScheduler
