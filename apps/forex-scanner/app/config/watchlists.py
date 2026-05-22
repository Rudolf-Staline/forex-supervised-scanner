"""Named Forex watchlists for local scanner and demo bot runs."""

from __future__ import annotations

WATCHLISTS: dict[str, list[str]] = {
    "major_forex": [
        "EUR/USD",
        "GBP/USD",
        "USD/CHF",
        "USD/JPY",
        "AUD/USD",
        "USD/CAD",
        "NZD/USD",
    ],
    "jpy_pairs": [
        "USD/JPY",
        "EUR/JPY",
        "GBP/JPY",
    ],
    "gbp_pairs": [
        "GBP/USD",
        "GBP/JPY",
        "EUR/GBP",
    ],
    "all_forex_demo": [
        "EUR/USD",
        "GBP/USD",
        "USD/CHF",
        "USD/JPY",
        "AUD/USD",
        "USD/CAD",
        "NZD/USD",
        "EUR/JPY",
        "GBP/JPY",
        "EUR/GBP",
    ],
}


def watchlist_names() -> list[str]:
    """Return configured watchlist profile names."""

    return sorted(WATCHLISTS)


def get_watchlist(name: str) -> list[str]:
    """Return a copy of a configured watchlist or raise a clear error."""

    try:
        return list(WATCHLISTS[name])
    except KeyError as exc:
        available = ", ".join(watchlist_names())
        raise ValueError(f"unknown watchlist {name!r}; available watchlists: {available}") from exc
