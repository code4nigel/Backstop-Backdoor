"""
Backstop Hinglish Voice Simulator Package.
State machine controlled conversational recovery engine with mid-call link dispatch.
"""

from .mid_call_link import send_mid_call_paylink, mask_phone_number
from .prompts import generate_hinglish_response, get_fallback_phrasing
from .simulator import (
    HinglishVoiceSimulator,
    VoiceIntent,
    CallState,
    CaseState,
    CustomerObjection,
)

__all__ = [
    "send_mid_call_paylink",
    "mask_phone_number",
    "generate_hinglish_response",
    "get_fallback_phrasing",
    "HinglishVoiceSimulator",
    "VoiceIntent",
    "CallState",
    "CaseState",
    "CustomerObjection",
]
