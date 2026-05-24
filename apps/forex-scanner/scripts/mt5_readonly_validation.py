"""Validate MT5 demo connectivity in strict read-only mode."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.brokers.mt5_reconciliation import reconcile_mt5_demo
from app.config.instruments import AssetClass
from app.config.watchlists import get_watchlist
from app.market.sessions import get_market_session

REPORTS_DIR = PROJECT_ROOT / "reports"
JSON_REPORT_PATH = REPORTS_DIR / "mt5_readonly_validation.json"
TXT_REPORT_PATH = REPORTS_DIR / "mt5_readonly_validation.txt"


@dataclass
class ValidationReport:
    mt5_available: bool
    initialized: bool
    account_server: str
    demo_only: bool
    symbols_checked: list[str]
    symbols_ok: list[str]
    symbols_failed: list[str]
    reconciliation_status: str
    open_positions_count: int
    foreign_positions_count: int
    next_tradable_windows: dict[str, str]
    final_status: str
    balance: float | None = None
    equity: float | None = None
    margin: float | None = None
    free_margin: float | None = None
    message: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate MT5 demo access without placing any order.")
    parser.add_argument("--watchlist", default="multi_asset_demo")
    parser.add_argument("--symbols", nargs="*", default=["EUR/USD", "XAU/USD", "US500"])
    parser.add_argument("--export-report", action="store_true")
    parser.add_argument("--show-next-windows", action="store_true")
    return parser.parse_args()


def run_validation(mt5: object | None, *, watchlist: str, symbols: list[str], show_next_windows: bool) -> ValidationReport:
    if mt5 is None:
        message = "MT5 terminal is not available in cloud environment."
        print(message)
        return ValidationReport(
            mt5_available=False,
            initialized=False,
            account_server="",
            demo_only=False,
            symbols_checked=symbols,
            symbols_ok=[],
            symbols_failed=list(symbols),
            reconciliation_status="MT5_UNAVAILABLE",
            open_positions_count=0,
            foreign_positions_count=0,
            next_tradable_windows={},
            final_status="MT5_UNAVAILABLE",
            message=message,
        )

    initialized = bool(_safe_mt5_call(mt5, "initialize", default=False))
    account = _safe_mt5_call(mt5, "account_info")
    server = str(getattr(account, "server", "") or "")
    demo_only = _is_demo_only(mt5, account)
    balance = _optional_float(getattr(account, "balance", None))
    equity = _optional_float(getattr(account, "equity", None))
    margin = _optional_float(getattr(account, "margin", None))
    free_margin = _optional_float(getattr(account, "margin_free", None))

    symbols_checked = _symbols_to_check(watchlist, symbols)
    symbols_ok: list[str] = []
    symbols_failed: list[str] = []
    for logical_symbol in symbols_checked:
        resolved_symbol = _resolve_symbol_name(mt5, logical_symbol)
        if not resolved_symbol:
            symbols_failed.append(logical_symbol)
            continue
        symbol_info = _safe_mt5_call(mt5, "symbol_info", resolved_symbol)
        tick = _safe_mt5_call(mt5, "symbol_info_tick", resolved_symbol)
        if symbol_info is None or tick is None:
            symbols_failed.append(logical_symbol)
        else:
            symbols_ok.append(logical_symbol)

    reconciliation_status = "NOT_AVAILABLE"
    open_positions_count = 0
    foreign_positions_count = 0
    reconcile_fn = reconcile_mt5_demo if callable(reconcile_mt5_demo) else None
    if reconcile_fn is not None:
        report = reconcile_fn(mt5, account=account)
        reconciliation_status = str(getattr(report, "reconciliation_status", "NOT_AVAILABLE"))
        open_positions_count = int(getattr(report, "open_positions", 0) or 0)
        foreign_positions_count = int(getattr(report, "foreign_positions", 0) or 0)

    windows = _next_tradable_windows(symbols_checked) if show_next_windows else {}

    final_status = "READY_READONLY"
    if not initialized or not demo_only or symbols_failed:
        final_status = "BLOCKED"

    return ValidationReport(
        mt5_available=True,
        initialized=initialized,
        account_server=server,
        demo_only=demo_only,
        symbols_checked=symbols_checked,
        symbols_ok=symbols_ok,
        symbols_failed=symbols_failed,
        reconciliation_status=reconciliation_status,
        open_positions_count=open_positions_count,
        foreign_positions_count=foreign_positions_count,
        next_tradable_windows=windows,
        final_status=final_status,
        balance=balance,
        equity=equity,
        margin=margin,
        free_margin=free_margin,
    )


def export_report(report: ValidationReport) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    JSON_REPORT_PATH.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    lines = [f"{key}={value}" for key, value in asdict(report).items()]
    TXT_REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _safe_mt5_call(mt5: object, name: str, *args, default=None):
    fn = getattr(mt5, name, None)
    if not callable(fn):
        return default
    try:
        value = fn(*args)
    except Exception:
        return default
    return default if value is None else value


def _is_demo_only(mt5: object, account: object | None) -> bool:
    if account is None:
        return False
    demo_constant = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", None)
    trade_mode = getattr(account, "trade_mode", None)
    if demo_constant is not None and trade_mode is not None:
        return int(trade_mode) == int(demo_constant)
    return "demo" in str(getattr(account, "server", "")).lower()


def _symbols_to_check(watchlist: str, symbols: list[str]) -> list[str]:
    watchlist_symbols = get_watchlist(watchlist)
    if symbols:
        return list(dict.fromkeys(symbols))
    return list(dict.fromkeys(watchlist_symbols))


def _resolve_symbol_name(mt5: object, logical_symbol: str) -> str | None:
    for candidate in (logical_symbol, logical_symbol.replace("/", "")):
        if _safe_mt5_call(mt5, "symbol_info", candidate) is not None:
            return candidate
    return None


def _next_tradable_windows(symbols: list[str]) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    windows: dict[str, str] = {}
    for symbol in symbols:
        session = get_market_session(now, _asset_class_for(symbol), symbol)
        windows[symbol] = session.next_tradable_window
    return windows


def _asset_class_for(symbol: str) -> AssetClass:
    normalized = symbol.upper().replace("/", "")
    if normalized in {"US500", "US30", "NAS100", "GER40"}:
        return AssetClass.INDICES
    if normalized in {"XAUUSD", "XAGUSD", "WTIOIL", "BRENTOIL"}:
        return AssetClass.COMMODITIES
    return AssetClass.FOREX


def load_mt5_module() -> object | None:
    try:
        import MetaTrader5 as mt5  # type: ignore
    except Exception:
        return None
    return mt5


def print_report(report: ValidationReport, *, show_next_windows: bool) -> None:
    print(f"mt5_available={str(report.mt5_available).lower()}")
    print(f"initialized={str(report.initialized).lower()}")
    print(f"account_server={report.account_server or '-'}")
    print(f"demo_only={str(report.demo_only).lower()}")
    print(f"balance={report.balance if report.balance is not None else '-'}")
    print(f"equity={report.equity if report.equity is not None else '-'}")
    print(f"margin={report.margin if report.margin is not None else '-'}")
    print(f"free_margin={report.free_margin if report.free_margin is not None else '-'}")
    print(f"symbols_checked={','.join(report.symbols_checked)}")
    print(f"symbols_ok={','.join(report.symbols_ok)}")
    print(f"symbols_failed={','.join(report.symbols_failed)}")
    print(f"reconciliation_status={report.reconciliation_status}")
    print(f"open_positions_count={report.open_positions_count}")
    print(f"foreign_positions_count={report.foreign_positions_count}")
    if show_next_windows:
        print(f"next_tradable_windows={report.next_tradable_windows}")
    print(f"final_status={report.final_status}")


def main() -> None:
    args = parse_args()
    mt5 = load_mt5_module()
    report = run_validation(mt5, watchlist=args.watchlist, symbols=args.symbols, show_next_windows=args.show_next_windows)
    if args.export_report:
        export_report(report)
    print_report(report, show_next_windows=args.show_next_windows)


if __name__ == "__main__":
    main()


def _optional_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
