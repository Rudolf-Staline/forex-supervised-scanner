"""Shared helpers for the layered paper/demo supervisor stack.

This module is the home for logic duplicated across the autonomous supervisor
(L1), realtime paper supervisor (L2), and realtime command center (L3). It is
introduced additively per `docs/ops_consolidation_plan.md`: existing public
functions delegate here without changing their signatures or behaviour.

Paper/demo only — nothing here sends orders or mutates configuration.
"""

from __future__ import annotations

from app.config.watchlists import get_watchlist


def resolve_supervisor_symbols(
    symbols: list[str] | None,
    watchlist: str | None,
    *,
    default: list[str],
) -> list[str]:
    """Resolve a supervisor symbol list from explicit symbols or a watchlist.

    Precedence (matching the L2 ``symbols_from_args`` behaviour exactly):

    1. explicit ``symbols`` (stripped, upper-cased, blanks dropped), else
    2. the named ``watchlist`` as-is, else
    3. the provided ``default`` list.
    """

    if symbols:
        return [symbol.strip().upper() for symbol in symbols if symbol.strip()]
    if watchlist:
        return get_watchlist(watchlist)
    return list(default)
