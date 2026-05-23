"""Read-only notifications for interesting paper/demo signals."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from app.config.instruments import instrument_for_symbol
from app.data.mt5_symbol_resolver import mt5_symbol_override_for
from app.market.sessions import MarketSessionInfo

if TYPE_CHECKING:
    from app.execution.demo_bot import DemoBotCycleResult, DemoBotDecision

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = PROJECT_ROOT / "reports"
ALERTS_LOG = REPORTS_DIR / "alerts.log"
SESSION_STATE_PATH = REPORTS_DIR / "notification_session_state.json"

NOTIFICATIONS_ENABLED_ENV = "NOTIFICATIONS_ENABLED"
NOTIFICATION_CHANNEL_ENV = "NOTIFICATION_CHANNEL"
ALERT_MIN_SCORE_ENV = "ALERT_MIN_SCORE"


@dataclass(frozen=True)
class AlertPayload:
    """One informational alert. It contains no trade-execution command."""

    timestamp_utc: str
    asset_class: str
    logical_symbol: str
    mt5_symbol: str
    setup: str
    status: str
    score: float | None
    risk_reward: float | None
    pattern_score: float
    detected_patterns: list[str]
    session_name: str
    reasons: list[str]
    broker: str
    mode: str
    safety_status: str
    near_miss: bool = False
    alert_type: str = "signal"
    action: str = "read_only_no_trade_execution"


@dataclass(frozen=True)
class NotificationSettings:
    enabled: bool = False
    channel: str = "console"
    alert_min_score: float = 70.0
    alerts_log_path: Path = ALERTS_LOG
    session_state_path: Path = SESSION_STATE_PATH

    @classmethod
    def from_env(cls) -> "NotificationSettings":
        return cls(
            enabled=_env_bool(NOTIFICATIONS_ENABLED_ENV, False),
            channel=os.getenv(NOTIFICATION_CHANNEL_ENV, "console").strip().lower() or "console",
            alert_min_score=_env_float(ALERT_MIN_SCORE_ENV, 70.0),
        )


class NotificationChannel(Protocol):
    def send(self, alert: AlertPayload) -> None: ...


class ConsoleNotificationChannel:
    def send(self, alert: AlertPayload) -> None:
        print(f"notification {json.dumps(asdict(alert), sort_keys=True)}")


class FileNotificationChannel:
    def __init__(self, path: Path = ALERTS_LOG) -> None:
        self.path = path

    def send(self, alert: AlertPayload) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(alert), sort_keys=True) + "\n")


class CompositeNotificationChannel:
    def __init__(self, channels: list[NotificationChannel]) -> None:
        self.channels = channels

    def send(self, alert: AlertPayload) -> None:
        for channel in self.channels:
            channel.send(alert)


class PlaceholderNotificationChannel:
    """Extensible placeholder for Telegram/Discord/email without required setup."""

    def __init__(self, name: str) -> None:
        self.name = name

    def send(self, alert: AlertPayload) -> None:
        print(f"notification_channel_unconfigured channel={self.name} alert_symbol={alert.logical_symbol}")


def notify_cycle_result(
    result: "DemoBotCycleResult",
    *,
    broker_mode: str,
    settings: NotificationSettings | None = None,
    session_by_symbol: dict[str, MarketSessionInfo] | None = None,
) -> list[AlertPayload]:
    settings = settings or NotificationSettings.from_env()
    alerts = [
        build_signal_alert(decision, broker_mode=broker_mode, session=session_by_symbol.get(decision.symbol) if session_by_symbol else None)
        for decision in result.decisions
        if should_alert_decision(decision, settings)
    ]
    return send_alerts(alerts, settings=settings)


def notify_session_transition(
    session: MarketSessionInfo,
    *,
    broker_mode: str,
    settings: NotificationSettings | None = None,
) -> AlertPayload | None:
    settings = settings or NotificationSettings.from_env()
    if not settings.enabled:
        return None
    previous = _load_session_state(settings.session_state_path)
    was_tradable = previous.get(session.symbol, {}).get("is_tradable_session")
    _save_session_state(settings.session_state_path, session.symbol, session)
    if session.is_tradable_session and was_tradable is False:
        alert = AlertPayload(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            asset_class=session.asset_class,
            logical_symbol=session.symbol,
            mt5_symbol=_mt5_symbol(session.symbol),
            setup="session_transition",
            status="detected",
            score=None,
            risk_reward=None,
            pattern_score=0.0,
            detected_patterns=[],
            session_name=session.session_name,
            reasons=["symbol moved from off_hours to tradable session"],
            broker=broker_mode,
            mode="paper",
            safety_status=safety_status_for_broker(broker_mode),
            alert_type="session_transition",
        )
        send_alerts([alert], settings=settings)
        return alert
    return None



def update_session_notification_state(
    session: MarketSessionInfo,
    *,
    settings: NotificationSettings | None = None,
) -> None:
    settings = settings or NotificationSettings.from_env()
    if not settings.enabled:
        return
    _save_session_state(settings.session_state_path, session.symbol, session)

def should_alert_decision(decision: "DemoBotDecision", settings: NotificationSettings | None = None) -> bool:
    settings = settings or NotificationSettings.from_env()
    return (
        (decision.final_score or 0.0) >= settings.alert_min_score
        or (decision.pattern_score or 0.0) > 0.0
        or decision.status in {"watchlist", "detected"}
        or is_near_miss_decision(decision, settings=settings)
    )


def is_near_miss_decision(decision: "DemoBotDecision", settings: NotificationSettings | None = None) -> bool:
    settings = settings or NotificationSettings.from_env()
    reasons = " ".join(decision.reasons).lower()
    return (decision.final_score or 0.0) >= max(55.0, settings.alert_min_score - 15.0) and (
        "near" in reasons or "miss" in reasons or "threshold" in reasons
    )


def build_signal_alert(decision: "DemoBotDecision", *, broker_mode: str, session: MarketSessionInfo | None = None) -> AlertPayload:
    instrument = instrument_for_symbol(decision.symbol)
    near_miss = is_near_miss_decision(decision)
    reasons = list(decision.reasons)
    if near_miss and "near_miss detected" not in reasons:
        reasons.append("near_miss detected")
    return AlertPayload(
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        asset_class=instrument.asset_class.value,
        logical_symbol=decision.symbol,
        mt5_symbol=_mt5_symbol(decision.symbol),
        setup=decision.setup_subtype,
        status=decision.status,
        score=decision.final_score,
        risk_reward=decision.risk_reward,
        pattern_score=decision.pattern_score,
        detected_patterns=list(decision.detected_patterns),
        session_name=session.session_name if session else _session_from_reasons(decision.reasons),
        reasons=reasons,
        broker=broker_mode,
        mode="paper",
        safety_status=safety_status_for_broker(broker_mode),
        near_miss=near_miss,
    )


def send_alerts(alerts: list[AlertPayload], *, settings: NotificationSettings | None = None) -> list[AlertPayload]:
    settings = settings or NotificationSettings.from_env()
    if not settings.enabled:
        return []
    channel = build_channel(settings)
    for alert in alerts:
        channel.send(alert)
    return alerts


def build_channel(settings: NotificationSettings) -> NotificationChannel:
    if settings.channel == "console":
        return ConsoleNotificationChannel()
    if settings.channel == "file":
        return FileNotificationChannel(settings.alerts_log_path)
    if settings.channel == "console,file":
        return CompositeNotificationChannel([ConsoleNotificationChannel(), FileNotificationChannel(settings.alerts_log_path)])
    if settings.channel in {"telegram", "discord", "email"}:
        return PlaceholderNotificationChannel(settings.channel)
    return ConsoleNotificationChannel()


def safety_status_for_broker(broker_mode: str) -> str:
    broker = broker_mode.strip().lower() or "paper"
    return (
        f"demo_only=true live_trading_disabled=true broker={broker} "
        "notifications_read_only=true no_trade_execution_command=true"
    )


def _mt5_symbol(symbol: str) -> str:
    override = mt5_symbol_override_for(symbol)
    if override:
        return override
    config = instrument_for_symbol(symbol)
    return config.mt5_symbol_candidates[0] if config.mt5_symbol_candidates else symbol.replace("/", "")


def _session_from_reasons(reasons: list[str]) -> str:
    for reason in reasons:
        marker = "session_name="
        if marker in reason:
            return reason.split(marker, 1)[1].split()[0].strip(";")
    return "unknown"


def _load_session_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_session_state(path: Path, symbol: str, session: MarketSessionInfo) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _load_session_state(path)
    state[symbol] = {
        "asset_class": session.asset_class,
        "session_name": session.session_name,
        "is_tradable_session": session.is_tradable_session,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)
