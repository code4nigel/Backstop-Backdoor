"""
Systemic Route and Issuer Degradation Analyzer.

Monitors real-time transaction success rates across (bank, gateway, upi_app) routes
over rolling windows. Detects bank/gateway outages and suppresses customer outreach,
rerouting via alternate gateways or delaying retries until recovery.
"""
from collections import deque, namedtuple
from typing import Dict, List, Optional, Tuple, Any, Set
import time

from engine.models import ActionType
from engine.config import SYSTEMIC_DROP_THRESHOLD, SYSTEMIC_WINDOW_SIZE


class SystemicIncident:
    """Represents an active systemic degradation incident on a route."""

    def __init__(
        self,
        route_key: str,
        baseline_rate: float,
        current_rate: float,
        reason: str,
    ):
        self.route_key = route_key
        self.baseline_rate = baseline_rate
        self.current_rate = current_rate
        self.drop_pts = round((baseline_rate - current_rate) * 100, 1)
        self.reason = reason
        self.started_at = time.time()
        self.deferred_cases_count = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "route_key": self.route_key,
            "baseline_rate": round(self.baseline_rate, 3),
            "current_rate": round(self.current_rate, 3),
            "drop_pts": self.drop_pts,
            "reason": self.reason,
            "started_at": self.started_at,
            "deferred_cases_count": self.deferred_cases_count,
        }


class DegradationResult(namedtuple("DegradationResult", ["is_degraded", "incident"])):
    """
    Tuple that behaves as a boolean when used in if-statements,
    while also unpacking cleanly as (is_degraded, incident).
    """

    def __bool__(self) -> bool:
        return bool(self.is_degraded)


class SystemicDegradationDetector:
    """
    Rolling window analyzer tracking success rates per (bank, gateway, upi_app).
    Flags a route as DEGRADED when success rate drops by >= drop_threshold (default 15%)
    below historical baseline.
    """

    CUSTOMER_OUTREACH_ACTIONS: Set[ActionType] = {
        ActionType.REMINDER_SMS,
        ActionType.WHATSAPP_NUDGE,
        ActionType.VOICE_CALL_HINGLISH,
        ActionType.CARD_UPDATE_LINK,
        ActionType.DISCOUNT_INCENTIVE,
    }

    def __init__(
        self,
        window_size: int = SYSTEMIC_WINDOW_SIZE,
        drop_threshold: float = SYSTEMIC_DROP_THRESHOLD,
        degradation_threshold: Optional[float] = None,
        default_baseline: float = 0.85,
    ):
        self.window_size = window_size
        self.drop_threshold = degradation_threshold if degradation_threshold is not None else drop_threshold
        self.degradation_threshold = self.drop_threshold
        self.default_baseline = default_baseline

        # Key: (bank, gateway, upi_app) or string key
        self._history: Dict[Tuple[str, Optional[str], Optional[str]], deque] = {}
        self._bank_history: Dict[str, deque] = {}
        self._windows: Dict[str, deque] = {}

        # Baseline success rates
        self._baselines: Dict[Tuple[str, Optional[str], Optional[str]], float] = {}
        self._bank_baselines: Dict[str, float] = {
            "HDFC": 0.85,
            "SBI": 0.82,
            "ICICI": 0.86,
            "AXIS": 0.84,
            "KOTAK": 0.85,
        }
        self.active_incidents: Dict[str, SystemicIncident] = {}

    def _normalize_key(
        self, bank: str, gateway: Optional[str] = None, upi_app: Optional[str] = None
    ) -> Tuple[str, Optional[str], Optional[str]]:
        bank_norm = bank.strip().upper() if bank else "UNKNOWN"
        gw_norm = gateway.strip().upper() if gateway else None
        app_norm = upi_app.strip().upper() if upi_app else None
        return (bank_norm, gw_norm, app_norm)

    def _make_key(self, bank: str, gateway: str = "", upi_app: str = "") -> str:
        parts = [p.strip().upper() for p in (bank, gateway or upi_app) if p and p.strip()]
        return ":".join(parts) if parts else "UNKNOWN:ROUTE"

    def set_baseline(
        self,
        bank: str,
        gateway: Optional[str] = None,
        upi_app: Optional[str] = None,
        rate: float = 0.85,
    ) -> None:
        """Set expected baseline success rate for a specific route or bank."""
        key = self._normalize_key(bank, gateway, upi_app)
        self._baselines[key] = rate
        if not gateway and not upi_app:
            self._bank_baselines[key[0]] = rate

    def get_baseline(
        self, bank: str, gateway: Optional[str] = None, upi_app: Optional[str] = None
    ) -> float:
        """Get baseline success rate for a route, falling back to bank or default."""
        key = self._normalize_key(bank, gateway, upi_app)
        if key in self._baselines:
            return self._baselines[key]
        if key[0] in self._bank_baselines:
            return self._bank_baselines[key[0]]
        return self.default_baseline

    def record_outcome(
        self,
        bank: str,
        gateway: Optional[str] = None,
        upi_app: Optional[str] = None,
        success: bool = True,
        timestamp: Optional[float] = None,
    ) -> None:
        """Record a single transaction outcome into the rolling window."""
        key = self._normalize_key(bank, gateway, upi_app)
        if key not in self._history:
            self._history[key] = deque(maxlen=self.window_size)
        self._history[key].append(bool(success))

        # Also record to bank-level rolling window
        bank_name = key[0]
        if bank_name not in self._bank_history:
            self._bank_history[bank_name] = deque(maxlen=self.window_size)
        self._bank_history[bank_name].append(bool(success))

        str_key = self._make_key(bank, gateway or "", upi_app or "")
        if str_key not in self._windows:
            self._windows[str_key] = deque(maxlen=self.window_size)
        self._windows[str_key].append(bool(success))

    def record_case(self, case: Any, success: bool = False) -> None:
        """Helper to record from a RecoveryCase or raw dictionary."""
        if hasattr(case, "metadata"):
            meta = case.metadata or {}
            bank = meta.get("bank", "")
            gateway = meta.get("gateway", None)
            upi_app = meta.get("upi_app", None)
        elif isinstance(case, dict):
            meta = case.get("metadata", case)
            bank = meta.get("bank", case.get("bank", ""))
            gateway = meta.get("gateway", case.get("gateway", None))
            upi_app = meta.get("upi_app", case.get("upi_app", None))
        else:
            return
        if bank:
            self.record_outcome(bank, gateway, upi_app, success=success)

    def get_success_rate(
        self, bank: str, gateway: Optional[str] = None, upi_app: Optional[str] = None
    ) -> float:
        """Compute the current success rate over the rolling window."""
        key = self._normalize_key(bank, gateway, upi_app)
        window = self._history.get(key)
        if window and len(window) >= 5:
            return sum(window) / len(window)

        # Fallback to bank-level window
        bank_window = self._bank_history.get(key[0])
        if bank_window and len(bank_window) >= 5:
            return sum(bank_window) / len(bank_window)

        str_key = self._make_key(bank, gateway or "", upi_app or "")
        if str_key in self._windows and len(self._windows[str_key]) >= 5:
            return sum(self._windows[str_key]) / len(self._windows[str_key])

        return self.get_baseline(bank, gateway, upi_app)

    def is_route_degraded(
        self, bank: str, gateway: Optional[str] = None, upi_app: Optional[str] = None
    ) -> bool:
        """
        Check if the route or bank success rate has dropped >= drop_threshold (15%) below baseline.
        """
        key = self._normalize_key(bank, gateway, upi_app)
        str_key = self._make_key(bank, gateway or "", upi_app or "")

        # Check manual incident injection
        if str_key in self.active_incidents or key[0] in self.active_incidents:
            return True

        baseline = self.get_baseline(bank, gateway, upi_app)

        # Check route-level window if populated
        route_window = self._history.get(key)
        if route_window and len(route_window) >= 5:
            route_rate = sum(route_window) / len(route_window)
            if (baseline - route_rate) >= self.drop_threshold or (
                baseline > 0 and (baseline - route_rate) / baseline >= self.drop_threshold
            ):
                return True

        # Check bank-level window
        bank_window = self._bank_history.get(key[0])
        if bank_window and len(bank_window) >= 5:
            bank_rate = sum(bank_window) / len(bank_window)
            bank_base = self._bank_baselines.get(key[0], self.default_baseline)
            if (bank_base - bank_rate) >= self.drop_threshold or (
                bank_base > 0 and (bank_base - bank_rate) / bank_base >= self.drop_threshold
            ):
                return True

        return False

    def is_degraded(
        self, bank: str, gateway: str = "", upi_app: str = ""
    ) -> DegradationResult:
        """
        Dual-mode degradation check returning a DegradationResult.
        Can be used as boolean or unpacked as (bool, incident).
        """
        degraded = self.is_route_degraded(bank, gateway, upi_app)
        str_key = self._make_key(bank, gateway, upi_app)
        incident = self.active_incidents.get(str_key) or self.active_incidents.get(bank.strip().upper())
        if degraded and not incident:
            current = self.get_success_rate(bank, gateway, upi_app)
            base = self.get_baseline(bank, gateway, upi_app)
            incident = SystemicIncident(
                route_key=str_key,
                baseline_rate=base,
                current_rate=current,
                reason=f"{str_key} success rate dropped from {int(base*100)}% to {int(current*100)}%",
            )
            self.active_incidents[str_key] = incident

        if incident and degraded:
            incident.deferred_cases_count += 1

        return DegradationResult(degraded, incident if degraded else None)

    def get_route_status(
        self, bank: str, gateway: Optional[str] = None, upi_app: Optional[str] = None
    ) -> Dict[str, Any]:
        """Returns structured diagnostic status for the route."""
        baseline = self.get_baseline(bank, gateway, upi_app)
        current = self.get_success_rate(bank, gateway, upi_app)
        degraded = self.is_route_degraded(bank, gateway, upi_app)
        key = self._normalize_key(bank, gateway, upi_app)
        sample_count = len(self._history.get(key, [])) or len(
            self._bank_history.get(key[0], [])
        )

        return {
            "bank": key[0],
            "gateway": key[1],
            "upi_app": key[2],
            "status": "DEGRADED" if degraded else "HEALTHY",
            "is_degraded": degraded,
            "baseline_success_rate": round(baseline, 3),
            "current_success_rate": round(current, 3),
            "rate_drop": round(baseline - current, 3),
            "sample_count": sample_count,
        }

    def get_override_action(
        self,
        bank: str,
        gateway: Optional[str] = None,
        upi_app: Optional[str] = None,
        alternate_gateway_available: bool = True,
    ) -> ActionType:
        """
        Systemic Action Override:
        Returns REROUTE_GATEWAY if alternate gateway available, else RETRY_DELAYED.
        """
        if alternate_gateway_available:
            return ActionType.REROUTE_GATEWAY
        return ActionType.RETRY_DELAYED

    def is_outreach_allowed(
        self, bank: str, gateway: Optional[str] = None, upi_app: Optional[str] = None
    ) -> bool:
        """
        Customer outreach is strictly prohibited during systemic degradation.
        """
        return not self.is_route_degraded(bank, gateway, upi_app)

    def inject_incident(
        self,
        bank: str,
        gateway: Optional[str] = None,
        upi_app: Optional[str] = None,
        success_rate: float = 0.30,
        count: int = 50,
    ) -> None:
        """Simulate an incident (e.g. HDFC drops to 30% or 50%) for testing."""
        key = self._normalize_key(bank, gateway, upi_app)
        self._history[key] = deque(maxlen=self.window_size)
        num_success = int(round(count * success_rate))
        for i in range(count):
            succ = i < num_success
            self.record_outcome(bank, gateway, upi_app, success=succ)

        str_key = self._make_key(bank, gateway or "", upi_app or "")
        base = self.get_baseline(bank, gateway, upi_app)
        self.active_incidents[str_key] = SystemicIncident(
            route_key=str_key,
            baseline_rate=base,
            current_rate=success_rate,
            reason=f"Injected incident on {str_key}: success rate = {success_rate:.0%}",
        )
        self.active_incidents[key[0]] = self.active_incidents[str_key]

    def force_incident(
        self,
        route_key: str,
        current_rate: float = 0.32,
        reason: str = "Bank core banking outage",
    ) -> SystemicIncident:
        """Alias for injecting incident by route_key string."""
        baseline = 0.88
        incident = SystemicIncident(
            route_key=route_key,
            baseline_rate=baseline,
            current_rate=current_rate,
            reason=reason,
        )
        self.active_incidents[route_key] = incident
        bank = route_key.split(":")[0]
        self.active_incidents[bank.upper()] = incident
        return incident

    def clear_incident(
        self, bank: str, gateway: Optional[str] = None, upi_app: Optional[str] = None
    ) -> None:
        """Clear degraded status and reset to healthy baseline."""
        key = self._normalize_key(bank, gateway, upi_app)
        if key in self._history:
            self._history[key].clear()
        if key[0] in self._bank_history:
            self._bank_history[key[0]].clear()
        str_key = self._make_key(bank, gateway or "", upi_app or "")
        self.active_incidents.pop(str_key, None)
        self.active_incidents.pop(key[0], None)

    def get_active_alerts(self) -> List[Dict[str, Any]]:
        return [inc.to_dict() for inc in self.active_incidents.values()]


# Backward-compatible alias
SystemicDetector = SystemicDegradationDetector

__all__ = [
    "SystemicDegradationDetector",
    "SystemicDetector",
    "SystemicIncident",
    "DegradationResult",
]
