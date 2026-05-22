"""Asset-class aware market-session diagnostics for demo scanning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from app.config.instruments import AssetClass


@dataclass(frozen=True)
class SessionWindow:
    """One recurring UTC session window."""

    name: str
    start_hour: int
    end_hour: int
    tradable: bool = True


@dataclass(frozen=True)
class MarketSessionInfo:
    """Current session diagnostics for one symbol and asset class."""

    symbol: str
    asset_class: str
    session_name: str
    is_tradable_session: bool
    reason: str
    next_tradable_window: str


SESSION_WINDOWS: dict[AssetClass, list[SessionWindow]] = {
    AssetClass.FOREX: [
        SessionWindow("asian", 0, 7),
        SessionWindow("london", 7, 13),
        SessionWindow("london_new_york_overlap", 13, 17),
        SessionWindow("new_york", 17, 21),
    ],
    AssetClass.COMMODITIES: [
        SessionWindow("london", 7, 13),
        SessionWindow("high_liquidity_overlap", 13, 17),
        SessionWindow("new_york", 17, 21),
    ],
    AssetClass.INDICES: [
        SessionWindow("us_open", 13, 15),
        SessionWindow("us_close", 20, 21),
        SessionWindow("europe_cash", 8, 16),
        SessionWindow("us_cash", 15, 20),
    ],
}

BEST_SESSION_BY_ASSET_CLASS: dict[AssetClass, str] = {
    AssetClass.FOREX: "london_new_york_overlap",
    AssetClass.COMMODITIES: "high_liquidity_overlap",
    AssetClass.INDICES: "us_open",
}


def get_market_session(now_utc: datetime, asset_class: AssetClass | str, symbol: str) -> MarketSessionInfo:
    """Return the current asset-class session and next tradable window."""

    asset = AssetClass(asset_class)
    moment = _as_utc(now_utc)
    if _is_weekend(moment):
        next_window = _next_tradable_window(moment, asset)
        return MarketSessionInfo(
            symbol=symbol,
            asset_class=asset.value,
            session_name="weekend_closed",
            is_tradable_session=False,
            reason=f"{symbol} is outside configured {asset.value} demo sessions because the market is in weekend mode.",
            next_tradable_window=next_window,
        )

    for window in SESSION_WINDOWS[asset]:
        if _window_contains(window, moment):
            return MarketSessionInfo(
                symbol=symbol,
                asset_class=asset.value,
                session_name=window.name,
                is_tradable_session=window.tradable,
                reason=f"{symbol} is inside the configured {asset.value} {window.name} session.",
                next_tradable_window=_next_tradable_window(moment, asset),
            )

    next_window = _next_tradable_window(moment, asset)
    return MarketSessionInfo(
        symbol=symbol,
        asset_class=asset.value,
        session_name="off_hours",
        is_tradable_session=False,
        reason=f"{symbol} is outside configured {asset.value} demo sessions.",
        next_tradable_window=next_window,
    )


def explain_off_hours(symbol: str, asset_class: AssetClass | str, now_utc: datetime) -> str:
    """Explain why a symbol is currently blocked by the session model."""

    info = get_market_session(now_utc, asset_class, symbol)
    if info.is_tradable_session:
        return (
            f"{symbol} is currently in session {info.session_name}; "
            f"asset_class={info.asset_class} next_tradable_window={info.next_tradable_window}."
        )
    return (
        f"{info.reason} asset_class={info.asset_class} session_name={info.session_name} "
        f"is_tradable_session=false next_tradable_window={info.next_tradable_window}"
    )


def best_session_for_asset_class(asset_class: AssetClass | str) -> str:
    """Return the recommended scan session for one asset class."""

    return BEST_SESSION_BY_ASSET_CLASS[AssetClass(asset_class)]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_weekend(moment: datetime) -> bool:
    return moment.weekday() >= 5


def _window_contains(window: SessionWindow, moment: datetime) -> bool:
    hour = moment.hour + moment.minute / 60.0
    if window.start_hour <= window.end_hour:
        return window.start_hour <= hour < window.end_hour
    return hour >= window.start_hour or hour < window.end_hour


def _next_tradable_window(moment: datetime, asset: AssetClass) -> str:
    windows = [window for window in SESSION_WINDOWS[asset] if window.tradable]
    for day_offset in range(0, 8):
        candidate_day = (moment + timedelta(days=day_offset)).date()
        if candidate_day.weekday() >= 5:
            continue
        for window in windows:
            start = datetime.combine(candidate_day, time(window.start_hour, tzinfo=timezone.utc))
            end = datetime.combine(candidate_day, time(window.end_hour, tzinfo=timezone.utc))
            if start <= moment < end:
                return f"{window.name} now until {end.isoformat()}"
            if start > moment:
                return f"{window.name} {start.isoformat()} to {end.isoformat()}"
    return "no configured tradable window found"
