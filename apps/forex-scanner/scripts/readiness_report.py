"""Final readiness report for limited MT5 demo execution.

The report is read-only: it never sends orders, never changes configuration,
and never enables demo execution automatically.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.brokers.mt5_reconciliation import MT5ReconciliationReport, reconcile_mt5_demo
from app.config.env import load_dotenv
from app.config.safety import DemoSafetyError, ensure_mt5_demo_safe_mode
from app.brokers.paper_broker import RealisticPaperBroker
from app.config.settings import load_settings
from app.core.types import DirectionBias, SetupFamily, SetupSubtype, TradingStyle
from app.data.mt5_symbol_resolver import MT5SymbolResolver
from app.data.mt5_symbols_health import diagnose_watchlist_symbols
from app.execution.broker import BrokerExecutionError
from app.execution.models import OrderRequest
from app.execution.mt5_demo_broker import MT5DemoBroker
from app.journal.trade_journal import TRADE_JOURNAL_PATH
from app.risk.daily_limits import DailyRiskConfig
from app.storage.database import Database

READINESS_TXT = PROJECT_ROOT / "reports" / "readiness_report.txt"
READINESS_JSON = PROJECT_ROOT / "reports" / "readiness_report.json"
CRITICAL_TESTS = [
    "tests/test_safety.py",
    "tests/test_demo_bot.py",
    "tests/test_multi_asset_safety.py",
]


@dataclass(frozen=True)
class ReadinessCheck:
    """One readiness check row."""

    name: str
    status: str
    detail: str
    critical: bool = True


def main() -> None:
    """Create a final readiness report."""

    parser = argparse.ArgumentParser(description="Create final readiness report. No orders are sent.")
    parser.add_argument("--skip-tests", action="store_true", help="Skip running critical pytest checks.")
    args = parser.parse_args()

    report = build_readiness_report(run_tests=not args.skip_tests)
    write_readiness_outputs(report)
    print_readiness_report(report)


def build_readiness_report(*, run_tests: bool = True, mt5_module: object | None = None) -> dict[str, object]:
    """Build a conservative readiness report."""

    load_dotenv()
    settings = load_settings()
    database = Database(settings.database_absolute_path)
    checks: list[ReadinessCheck] = []
    mt5_report: MT5ReconciliationReport | None = None

    checks.append(_tests_check(run_tests))
    checks.append(_critical_secrets_check())
    checks.append(_provider_synthetic_check())
    checks.append(_environment_check("ALLOW_LIVE_TRADING", "false"))
    checks.append(_environment_check("MT5_DEMO_ONLY", "true", critical=False))
    checks.append(_environment_default_false_check("ENABLE_DEMO_EXECUTION"))
    checks.append(_environment_default_false_check("ALLOW_MULTI_ASSET_DEMO_TRADING"))
    checks.append(_max_demo_volume_check())
    checks.append(_max_demo_orders_per_day_check())
    checks.append(_module_check("demo_execution_gate", "app.safety.demo_execution_gate", optional=True))
    checks.append(_module_check("mt5_reconciliation_module", "app.brokers.mt5_reconciliation", optional=True))
    checks.append(_module_check("forward_test_paper", "scripts.forward_test_paper", optional=True))
    checks.append(_module_check("backtest", "app.backtest.engine", optional=True))
    checks.append(_module_check("threshold_optimizer", "scripts.threshold_optimizer_report", optional=True))
    checks.append(_module_check("signal_journal", "app.journal.trade_journal", optional=True))
    checks.append(_module_check("multi_asset_signal_report", "scripts.multi_asset_signal_report", optional=True))
    checks.append(_daily_limits_check())
    checks.append(_journal_check(database))
    checks.append(_paper_broker_check(settings, database))
    checks.append(_sessions_check())
    checks.append(_module_check("session_aware_scanning", "_demo_bot_cli", optional=True))

    mt5_available = mt5_module is not None or importlib.util.find_spec("MetaTrader5") is not None
    if not mt5_available:
        checks.append(ReadinessCheck("mt5_connected", "WARN", "MetaTrader5 package is not installed", critical=False))
        checks.append(ReadinessCheck("account_demo_only", "WARN", "MT5 account not checked", critical=False))
        checks.append(ReadinessCheck("symbol_resolver", "WARN", "MT5 resolver not checked", critical=False))
        checks.append(ReadinessCheck("symbol_health", "WARN", "MT5 health not checked", critical=False))
        checks.append(ReadinessCheck("mt5_reconciliation", "WARN", "MT5 reconciliation not checked", critical=False))
    else:
        mt5_checks, mt5_report = _mt5_checks(settings, database, mt5_module=mt5_module)
        checks.extend(mt5_checks)

    status = classify_readiness(checks, mt5_report)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "readiness_status": status,
        "checks": [asdict(check) for check in checks],
        "outputs": {
            "text": str(READINESS_TXT),
            "json": str(READINESS_JSON),
        },
        "mt5_reconciliation": _reconciliation_payload(mt5_report),
        "orders_sent": False,
        "config_modified": False,
        "demo_execution_enabled_by_report": False,
    }
    return payload


def classify_readiness(checks: list[ReadinessCheck], mt5_report: MT5ReconciliationReport | None) -> str:
    """Return NOT_READY, PAPER_READY, or DEMO_READY_LIMITED."""

    critical_failed = [check for check in checks if check.critical and check.status == "FAIL"]
    if critical_failed:
        return "NOT_READY"
    paper_ready = all(
        _check_ok(checks, name)
        for name in [
            "critical_tests",
            "ALLOW_LIVE_TRADING",
            "ENABLE_DEMO_EXECUTION",
            "broker_paper",
            "provider_synthetic",
        ]
    )
    no_critical_secret_gap = not any(c.name == "critical_secrets" and c.status == "FAIL" for c in checks)
    paper_ready = paper_ready and no_critical_secret_gap
    demo_ready = (
        paper_ready
        and mt5_report is not None
        and mt5_report.reconciliation_status == "OK"
        and _check_ok(checks, "mt5_connected")
        and _check_ok(checks, "account_demo_only")
        and _check_ok(checks, "demo_execution_gate")
        and _check_ok(checks, "mt5_reconciliation_module")
        and _check_ok(checks, "daily_limits")
        and _check_ok(checks, "max_demo_order_volume")
        and _check_ok(checks, "max_demo_orders_per_day")
        and _check_ok(checks, "journal")
    )
    if demo_ready:
        return "DEMO_READY_LIMITED"
    if paper_ready:
        return "PAPER_READY"
    return "NOT_READY"


def write_readiness_outputs(report: dict[str, object]) -> None:
    """Write text and JSON reports."""

    READINESS_JSON.parent.mkdir(parents=True, exist_ok=True)
    READINESS_JSON.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    READINESS_TXT.write_text(_readiness_text(report), encoding="utf-8")


def print_readiness_report(report: dict[str, object]) -> None:
    """Print concise readiness output."""

    print(f"readiness_status={report['readiness_status']}")
    for check in report["checks"]:
        print(f"{check['status']} {check['name']}: {check['detail']}")
    print(f"txt_export={READINESS_TXT}")
    print(f"json_export={READINESS_JSON}")


def _tests_check(run_tests: bool) -> ReadinessCheck:
    if not run_tests:
        return ReadinessCheck("critical_tests", "WARN", "critical pytest checks skipped", critical=False)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *CRITICAL_TESTS],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    last_line = (result.stdout.strip().splitlines() or result.stderr.strip().splitlines() or [""])[-1]
    status = "OK" if result.returncode == 0 else "FAIL"
    return ReadinessCheck("critical_tests", status, last_line or f"pytest returncode={result.returncode}")


def _paper_broker_check(settings, database: Database) -> ReadinessCheck:
    try:
        request = OrderRequest(
            symbol="EUR/USD",
            style=TradingStyle.DAY_TRADING,
            setup_family=SetupFamily.TREND_CONTINUATION,
            setup_subtype=SetupSubtype.EMA50_PULLBACK,
            direction=DirectionBias.LONG,
            quantity_units=0.01,
            entry_price=1.1000,
            stop_loss=1.0950,
            take_profit=1.1100,
            tp1=1.1050,
            tp2=1.1080,
            tp3=1.1100,
            source_status="approved",
            final_score=80.0,
            provider="synthetic",
            session="london",
            spread_at_signal=0.0001,
            atr_at_signal=0.0010,
            data_quality_score=95.0,
        )
        simulation = RealisticPaperBroker().simulate_request(request)
    except Exception as exc:
        return ReadinessCheck("broker_paper", "FAIL", str(exc))
    if not simulation.accepted:
        return ReadinessCheck("broker_paper", "FAIL", "; ".join(simulation.reasons))
    return ReadinessCheck(
        "broker_paper",
        "OK",
        f"paper fill simulation ok fill_status={simulation.fill_status} final_rr={simulation.final_risk_reward:.2f}",
    )


def _mt5_checks(settings, database: Database, *, mt5_module: object | None) -> tuple[list[ReadinessCheck], MT5ReconciliationReport | None]:
    checks: list[ReadinessCheck] = []
    broker = MT5DemoBroker(settings, mt5_module=mt5_module)
    report: MT5ReconciliationReport | None = None
    try:
        ensure_mt5_demo_safe_mode(settings, context="readiness_report.py mt5 check")
        account = broker.connect()
        mt5 = broker.mt5
        if mt5 is None:
            raise RuntimeError("MT5 module unavailable after connect")
        checks.append(ReadinessCheck("mt5_connected", "OK", f"server={account.server}"))
        checks.append(ReadinessCheck("account_demo_only", "OK" if account.is_demo else "FAIL", f"demo_only={account.is_demo} server={account.server}"))
        resolution = MT5SymbolResolver(mt5, require_bars=False).resolve("EUR/USD", require_bars=False)
        checks.append(ReadinessCheck("symbol_resolver", "OK" if resolution.ok else "FAIL", f"EUR/USD->{resolution.mt5_symbol} reason={resolution.reason}"))
        health = diagnose_watchlist_symbols(["EUR/USD"], settings=settings, mt5=mt5, bars=120)
        health_ok = bool(health and health[0].healthy)
        checks.append(ReadinessCheck("symbol_health", "OK" if health_ok else "FAIL", health[0].reason if health else "no health result"))
        report = reconcile_mt5_demo(
            mt5,
            account=broker.account,
            local_orders=[*database.load_paper_orders(), *database.load_broker_orders()],
            max_open_positions=2,
        )
        checks.append(ReadinessCheck("mt5_reconciliation", "OK" if report.reconciliation_status == "OK" else "FAIL", report.reconciliation_status))
    except (BrokerExecutionError, DemoSafetyError, Exception) as exc:
        checks.append(ReadinessCheck("mt5_connected", "FAIL", str(exc), critical=False))
        checks.append(ReadinessCheck("account_demo_only", "WARN", "MT5 demo account not confirmed", critical=False))
        checks.append(ReadinessCheck("symbol_resolver", "WARN", "MT5 resolver not checked", critical=False))
        checks.append(ReadinessCheck("symbol_health", "WARN", "MT5 health not checked", critical=False))
        checks.append(ReadinessCheck("mt5_reconciliation", "WARN", "MT5 reconciliation not checked", critical=False))
    finally:
        broker.disconnect()
    return checks, report


def _environment_check(name: str, expected: str, *, critical: bool = True) -> ReadinessCheck:
    actual = os.getenv(name, "")
    status = "OK" if actual.strip().lower() == expected else "FAIL"
    return ReadinessCheck(name, status, f"expected={expected} actual={actual or '<missing>'}", critical=critical)


def _environment_default_false_check(name: str) -> ReadinessCheck:
    actual = os.getenv(name, "false")
    status = "OK" if actual.strip().lower() == "false" else "FAIL"
    return ReadinessCheck(name, status, f"default/actual={actual}")


def _max_demo_volume_check() -> ReadinessCheck:
    raw = os.getenv("MAX_DEMO_ORDER_VOLUME", "0.01")
    try:
        value = float(raw)
    except ValueError:
        return ReadinessCheck("max_demo_order_volume", "FAIL", f"invalid MAX_DEMO_ORDER_VOLUME={raw}")
    status = "OK" if value <= 0.01 else "FAIL"
    return ReadinessCheck("max_demo_order_volume", status, f"MAX_DEMO_ORDER_VOLUME={value}")


def _max_demo_orders_per_day_check() -> ReadinessCheck:
    raw = os.getenv("MAX_DEMO_ORDERS_PER_DAY", "1")
    try:
        value = int(raw)
    except ValueError:
        return ReadinessCheck("max_demo_orders_per_day", "FAIL", f"invalid MAX_DEMO_ORDERS_PER_DAY={raw}")
    status = "OK" if value <= 1 else "FAIL"
    return ReadinessCheck("max_demo_orders_per_day", status, f"MAX_DEMO_ORDERS_PER_DAY={value}")


def _module_check(name: str, module_name: str, *, optional: bool = False) -> ReadinessCheck:
    try:
        __import__(module_name)
    except Exception as exc:
        status = "WARN" if optional else "FAIL"
        return ReadinessCheck(name, status, str(exc), critical=not optional)
    return ReadinessCheck(name, "OK", f"{module_name} available")


def _provider_synthetic_check() -> ReadinessCheck:
    try:
        from app.data.providers import SyntheticForexDataProvider
        from app.config.settings import load_settings as _load_settings

        settings = _load_settings()
        provider = SyntheticForexDataProvider(settings.provider)
    except Exception as exc:
        return ReadinessCheck("provider_synthetic", "FAIL", str(exc))
    return ReadinessCheck("provider_synthetic", "OK", f"provider={provider.name}")


def _critical_secrets_check() -> ReadinessCheck:
    missing = [name for name in ("DB_PASSWORD",) if not os.getenv(name)]
    if missing:
        return ReadinessCheck("critical_secrets", "WARN", f"missing optional secrets: {', '.join(missing)}", critical=False)
    return ReadinessCheck("critical_secrets", "OK", "critical secrets available or not required")


def _daily_limits_check() -> ReadinessCheck:
    try:
        config = DailyRiskConfig.from_env()
    except Exception as exc:
        return ReadinessCheck("daily_limits", "FAIL", str(exc))
    return ReadinessCheck("daily_limits", "OK", f"max_trades_per_day={config.max_trades_per_day} max_open_trades={config.max_open_trades}")


def _wait_for_session_check() -> ReadinessCheck:
    run_demo = PROJECT_ROOT / "scripts" / "run_demo_bot.py"
    if not run_demo.exists():
        return ReadinessCheck("wait_for_session", "WARN", "scripts/run_demo_bot.py missing", critical=False)
    content = run_demo.read_text(encoding="utf-8")
    if "--wait-for-session" in content:
        return ReadinessCheck("wait_for_session", "OK", "--wait-for-session option found")
    return ReadinessCheck("wait_for_session", "WARN", "--wait-for-session option not found", critical=False)


def _journal_check(database: Database) -> ReadinessCheck:
    try:
        events = database.load_trade_events()
    except Exception as exc:
        return ReadinessCheck("journal", "FAIL", str(exc))
    csv_status = "csv_exists" if TRADE_JOURNAL_PATH.exists() else "csv_not_created_yet"
    return ReadinessCheck("journal", "OK", f"events={len(events)} {csv_status}")


def _sessions_check() -> ReadinessCheck:
    try:
        from app.market.sessions import get_market_session
        from app.config.instruments import AssetClass

        info = get_market_session(datetime.now(timezone.utc), AssetClass.FOREX, "EUR/USD")
    except Exception as exc:
        return ReadinessCheck("sessions", "FAIL", str(exc))
    return ReadinessCheck("sessions", "OK", f"current_forex_session={info.session_name} tradable={info.is_tradable_session}")


def _check_ok(checks: list[ReadinessCheck], name: str) -> bool:
    return any(check.name == name and check.status == "OK" for check in checks)


def _reconciliation_payload(report: MT5ReconciliationReport | None) -> dict[str, object] | None:
    if report is None:
        return None
    return {
        "mt5_connected": report.mt5_connected,
        "account_server": report.account_server,
        "demo_only": report.demo_only,
        "open_positions": report.open_positions,
        "pending_orders": report.pending_orders,
        "bot_positions": report.bot_positions,
        "foreign_positions": report.foreign_positions,
        "duplicate_risk": report.duplicate_risk,
        "reconciliation_status": report.reconciliation_status,
        "reasons": report.reasons,
    }


def _readiness_text(report: dict[str, object]) -> str:
    lines = [
        "Forex Supervisor Readiness Report",
        f"Generated at: {report['generated_at']}",
        f"readiness_status: {report['readiness_status']}",
        "",
        "Checks:",
    ]
    for check in report["checks"]:
        lines.append(f"- {check['status']} {check['name']}: {check['detail']}")
    lines.extend(
        [
            "",
            "Safety:",
            "- No order was sent by this report.",
            "- Configuration was not modified.",
            "- Demo execution was not enabled automatically.",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
