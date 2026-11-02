"""
Backstop Central Configuration, Thresholds, Policy Levers, and Environment Loader.

Reads parameters from .env with fallback to sensible defaults.
Provides typed access to financial guardrails, contact window policies,
run isolation settings, and unit costs.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

# Attempt to load python-dotenv if available
try:
    from dotenv import load_dotenv
    HAS_DOTENV = True
except ImportError:
    HAS_DOTENV = False
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:
        return False

# Load environment on module import
for _candidate in [".env", "BACKSTOP--RAZORPAY/.env", "../.env"]:
    if os.path.exists(_candidate):
        load_dotenv(dotenv_path=_candidate)
        break

# Database & Environment
SQLITE_DB_PATH: str = os.getenv("SQLITE_DB_PATH", "models/recovery_ledger.db")
DEFAULT_RUN_ID: str = os.getenv("DEFAULT_RUN_ID", "default_run")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

# Operational & Policy Levers
MAX_ATTEMPTS: int = int(os.getenv("MAX_ATTEMPTS", "3"))
MAX_CUSTOMER_TOUCHES_7D: int = int(os.getenv("MAX_CUSTOMER_TOUCHES_7D", "2"))
CONFIDENCE_HUMAN_FLOOR: float = float(os.getenv("CONFIDENCE_HUMAN_FLOOR", "0.35"))
MARGIN_DISCOUNT_CAP_PCT: float = float(os.getenv("MARGIN_DISCOUNT_CAP_PCT", "0.10"))
HIGH_VALUE_THRESHOLD: float = float(os.getenv("HIGH_VALUE_THRESHOLD", "50000.0"))
MIN_CASE_VALUE: float = float(os.getenv("MIN_CASE_VALUE", "200.0"))

# Contact Rules (RBI / TRAI Norms)
QUIET_HOURS_START: int = int(os.getenv("QUIET_HOURS_START", "19"))  # 7:00 PM
QUIET_HOURS_END: int = int(os.getenv("QUIET_HOURS_END", "8"))      # 8:00 AM
SUPPRESSION_WINDOW_HOURS: int = int(os.getenv("SUPPRESSION_WINDOW_HOURS", "12"))

# Scientific Measurement
HOLDOUT_RATIO: float = float(os.getenv("HOLDOUT_RATIO", "0.15"))

# Systemic Degradation Thresholds
SYSTEMIC_DROP_THRESHOLD: float = float(os.getenv("SYSTEMIC_DROP_THRESHOLD", "0.15"))  # 15% drop
SYSTEMIC_WINDOW_SIZE: int = int(os.getenv("SYSTEMIC_WINDOW_SIZE", "50"))             # rolling 50 txns
DEFAULT_SEED: int = int(os.getenv("DEFAULT_SEED", "42"))

# Human Review Ops Fee
HUMAN_REVIEW_FEE: float = float(os.getenv("HUMAN_REVIEW_FEE", "150.0"))

# Standard File Paths
DATA_PATH: str = os.getenv("DATA_PATH", "data/transactions.csv")
DEMO_BATCH_PATH: str = os.getenv("DEMO_BATCH_PATH", "data/active_cases_300.json")
HISTORICAL_DATA_PATH: str = os.getenv("HISTORICAL_DATA_PATH", "data/historical_cases.csv")

# Unit Channel & Action Costs (in INR ₹)
CHANNEL_UNIT_COSTS: Dict[str, float] = {
    "retry_immediate": 2.00,
    "retry_delayed": 2.00,
    "reroute_gateway": 5.00,
    "card_update_link": 4.00,
    "mandate_retry_sequence": 6.00,
    "reminder_sms": 3.00,
    "whatsapp_nudge": 4.00,
    "voice_call_hinglish": 18.00,
    "discount_incentive": 4.00,  # + applied discount amount
    "human_escalation": 150.00,
    "do_nothing": 0.00,
}


@dataclass
class Config:
    """Central configuration container for Backstop."""

    # Persistence & Run Isolation
    SQLITE_DB_PATH: str = SQLITE_DB_PATH
    DEFAULT_RUN_ID: str = DEFAULT_RUN_ID
    GEMINI_API_KEY: str = GEMINI_API_KEY

    # Operational & Policy Levers
    MAX_ATTEMPTS: int = MAX_ATTEMPTS
    MAX_CUSTOMER_TOUCHES_7D: int = MAX_CUSTOMER_TOUCHES_7D
    CONFIDENCE_HUMAN_FLOOR: float = CONFIDENCE_HUMAN_FLOOR
    MARGIN_DISCOUNT_CAP_PCT: float = MARGIN_DISCOUNT_CAP_PCT
    HIGH_VALUE_THRESHOLD: float = HIGH_VALUE_THRESHOLD
    MIN_CASE_VALUE: float = MIN_CASE_VALUE

    # Contact Rules
    QUIET_HOURS_START: int = QUIET_HOURS_START
    QUIET_HOURS_END: int = QUIET_HOURS_END
    SUPPRESSION_WINDOW_HOURS: int = SUPPRESSION_WINDOW_HOURS

    # Scientific Measurement
    HOLDOUT_RATIO: float = HOLDOUT_RATIO
    DEFAULT_SEED: int = DEFAULT_SEED
    HUMAN_REVIEW_FEE: float = HUMAN_REVIEW_FEE

    # File Paths
    DATA_PATH: str = DATA_PATH
    DEMO_BATCH_PATH: str = DEMO_BATCH_PATH
    HISTORICAL_DATA_PATH: str = HISTORICAL_DATA_PATH

    # Channel Costs
    CHANNEL_UNIT_COSTS: Dict[str, float] = field(default_factory=lambda: dict(CHANNEL_UNIT_COSTS))

    def is_quiet_hours(self, hour: Optional[int] = None) -> bool:
        """Check if a given hour (or current system hour) is in quiet hours.

        Quiet hours default to 19:00 (7 PM) through 08:00 (8 AM).
        During quiet hours, outbound outreach is blocked.

        Args:
            hour: Integer hour in 24h format (0-23).

        Returns:
            True if in quiet hours, False if allowed for contact.
        """
        if hour is None:
            hour = datetime.now().hour

        if self.QUIET_HOURS_START > self.QUIET_HOURS_END:
            return hour >= self.QUIET_HOURS_START or hour < self.QUIET_HOURS_END
        else:
            return self.QUIET_HOURS_START <= hour < self.QUIET_HOURS_END

    def to_dict(self) -> Dict[str, Any]:
        """Return configuration as a dictionary."""
        return asdict(self)


def load_config(env_path: Optional[str] = None, **overrides: Any) -> Config:
    """Load configuration from environment with optional overrides.

    Args:
        env_path: Optional path to .env file
        **overrides: Key-value parameter overrides

    Returns:
        Config instance populated with environment values and overrides
    """
    if env_path is not None and os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=True)

    cfg = Config(
        SQLITE_DB_PATH=os.getenv("SQLITE_DB_PATH", SQLITE_DB_PATH),
        DEFAULT_RUN_ID=os.getenv("DEFAULT_RUN_ID", DEFAULT_RUN_ID),
        GEMINI_API_KEY=os.getenv("GEMINI_API_KEY", GEMINI_API_KEY),
        MAX_ATTEMPTS=int(os.getenv("MAX_ATTEMPTS", str(MAX_ATTEMPTS))),
        MAX_CUSTOMER_TOUCHES_7D=int(os.getenv("MAX_CUSTOMER_TOUCHES_7D", str(MAX_CUSTOMER_TOUCHES_7D))),
        CONFIDENCE_HUMAN_FLOOR=float(os.getenv("CONFIDENCE_HUMAN_FLOOR", str(CONFIDENCE_HUMAN_FLOOR))),
        MARGIN_DISCOUNT_CAP_PCT=float(os.getenv("MARGIN_DISCOUNT_CAP_PCT", str(MARGIN_DISCOUNT_CAP_PCT))),
        HIGH_VALUE_THRESHOLD=float(os.getenv("HIGH_VALUE_THRESHOLD", str(HIGH_VALUE_THRESHOLD))),
        MIN_CASE_VALUE=float(os.getenv("MIN_CASE_VALUE", str(MIN_CASE_VALUE))),
        QUIET_HOURS_START=int(os.getenv("QUIET_HOURS_START", str(QUIET_HOURS_START))),
        QUIET_HOURS_END=int(os.getenv("QUIET_HOURS_END", str(QUIET_HOURS_END))),
        SUPPRESSION_WINDOW_HOURS=int(os.getenv("SUPPRESSION_WINDOW_HOURS", str(SUPPRESSION_WINDOW_HOURS))),
        HOLDOUT_RATIO=float(os.getenv("HOLDOUT_RATIO", str(HOLDOUT_RATIO))),
        DEFAULT_SEED=int(os.getenv("DEFAULT_SEED", str(DEFAULT_SEED))),
        HUMAN_REVIEW_FEE=float(os.getenv("HUMAN_REVIEW_FEE", str(HUMAN_REVIEW_FEE))),
        DATA_PATH=os.getenv("DATA_PATH", DATA_PATH),
        DEMO_BATCH_PATH=os.getenv("DEMO_BATCH_PATH", DEMO_BATCH_PATH),
        HISTORICAL_DATA_PATH=os.getenv("HISTORICAL_DATA_PATH", HISTORICAL_DATA_PATH),
    )

    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    return cfg


# Module-level default instance
config: Config = load_config()
