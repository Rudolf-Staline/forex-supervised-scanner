"""Deterministic integration smoke check for local delivery validation."""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backtest.engine import Backtester
from app.config.settings import AppSettings, load_settings
from app.core.pipeline import ScannerService
from app.core.types import OpportunityStatus, TradingStyle
from app.data.providers import SyntheticForexDataProvider, build_provider
from app.storage.database import Database
from app.utils.logging import configure_logging


def main() -> None:
    """Run a deterministic smoke check and print compact results."""

    parser = argparse.ArgumentParser(description="Run deterministic Forex scanner smoke validation.")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["EUR/USD", "GBP/USD", "USD/CHF"],
        help="Symbols to scan. Defaults cover trend, breakout, and range demo scenarios.",
    )
    args = parser.parse_args()

    configure_logging()
    base_settings = load_settings()
    configured_provider = build_provider(base_settings)
    settings = _fast_smoke_settings(base_settings)
    synthetic_provider = SyntheticForexDataProvider(settings.provider)

    with tempfile.TemporaryDirectory(prefix="forex-scanner-smoke-") as temp_dir:
        database = Database(Path(temp_dir) / "smoke.sqlite")
        scan = ScannerService(settings, synthetic_provider, database).scan(
            TradingStyle.DAY_TRADING,
            args.symbols,
            timestamp=datetime(2025, 1, 15, 14, tzinfo=timezone.utc),
        )
        tradable = [opportunity for opportunity in scan.opportunities if opportunity.status in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM}]
        diagnosed = [opportunity for opportunity in scan.opportunities if opportunity.raw_setup_family is not None]
        if not tradable:
            print("scan=ok approved=0 deterministic opportunities were diagnosed but not approved")
            for opportunity in scan.opportunities:
                reason = opportunity.rejection_reason or opportunity.explanation
                setup = opportunity.raw_setup_family.value if opportunity.raw_setup_family else "none"
                score = "n/a" if opportunity.pre_gate_score is None else f"{opportunity.pre_gate_score:.2f}"
                failed = ",".join(opportunity.failed_gates) if opportunity.failed_gates else "none"
                category = opportunity.rejection_category.value if opportunity.rejection_category else "unclassified"
                print(
                    "no_trade "
                    f"symbol={opportunity.symbol} status={opportunity.status.value} raw_setup={setup} pre_gate_score={score} "
                    f"category={category} failed_gates={failed} reason={reason}"
                )
            if not diagnosed:
                raise SystemExit(1)

        backtest_symbol = (tradable or diagnosed or scan.opportunities)[0].symbol
        backtest = Backtester(settings, synthetic_provider, database).run(
            symbols=[backtest_symbol],
            style=TradingStyle.DAY_TRADING,
            setup_filter="all",
            start=datetime(2025, 1, 10, tzinfo=timezone.utc),
            end=datetime(2025, 1, 11, tzinfo=timezone.utc),
        )

    first = (tradable or diagnosed or scan.opportunities)[0]
    print(f"settings=ok provider={configured_provider.name} deterministic_provider={synthetic_provider.name}")
    print(
        "scan=ok "
        f"opportunities={len(scan.opportunities)} errors={len(scan.errors)} "
        f"approved={len(tradable)} first={first.symbol}:{first.status.value}:{first.setup_family.value}:{first.direction.value}:score={first.score:.2f}"
    )
    print(f"backtest=ok symbol={backtest_symbol} trades={backtest.metrics.number_of_trades} equity_points={len(backtest.equity_curve)}")


def _fast_smoke_settings(settings: AppSettings) -> AppSettings:
    """Return a copy with shorter deterministic smoke horizons."""

    adjusted = settings.model_copy(deep=True)
    adjusted.provider.name = "synthetic"
    adjusted.provider.fallback_to_synthetic = True
    adjusted.provider.max_bars = 300
    style = adjusted.styles[TradingStyle.DAY_TRADING]
    style.lookback_bars = 220
    style.max_hold_bars = 4
    return adjusted


if __name__ == "__main__":
    main()
