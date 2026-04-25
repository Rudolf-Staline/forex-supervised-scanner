"""Compare a few conservative calibration profiles with the existing backtester."""

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
from app.core.types import SetupFamily, TradingStyle
from app.data.providers import SyntheticForexDataProvider, build_provider
from app.storage.database import Database


def main() -> None:
    """Run a small parameter comparison and print compact metric rows."""

    parser = argparse.ArgumentParser(description="Compare scanner calibration profiles with historical backtests.")
    parser.add_argument("--symbols", nargs="+", default=["EUR/USD", "GBP/USD", "USD/CHF"])
    parser.add_argument("--style", default=TradingStyle.DAY_TRADING.value, choices=[style.value for style in TradingStyle])
    parser.add_argument("--start", default="2025-01-10")
    parser.add_argument("--end", default="2025-01-14")
    parser.add_argument("--synthetic", action="store_true", help="Use deterministic development data for repeatable local comparison.")
    args = parser.parse_args()

    base = load_settings()
    style = TradingStyle(args.style)
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    with tempfile.TemporaryDirectory(prefix="forex-calibration-") as temp_dir:
        database = Database(Path(temp_dir) / "calibration.sqlite")
        for name, settings in _profiles(base):
            provider = SyntheticForexDataProvider(settings.provider) if args.synthetic else build_provider(settings)
            result = Backtester(settings, provider, database).run(args.symbols, style, "all", start, end)
            metrics = result.metrics
            print(
                f"profile={name} trades={metrics.number_of_trades} win_rate={metrics.win_rate:.2f} "
                f"profit_factor={metrics.profit_factor:.2f} expectancy={metrics.expectancy:.3f} "
                f"max_dd={metrics.max_drawdown:.3f} sharpe_like={metrics.sharpe_like:.3f}"
            )


def _profiles(settings: AppSettings) -> list[tuple[str, AppSettings]]:
    """Return a compact set of comparable calibration variants."""

    current = settings.model_copy(deep=True)
    balanced = settings.model_copy(deep=True)
    balanced.risk.target_profile = "balanced"

    conservative = settings.model_copy(deep=True)
    conservative.risk.target_profile = "conservative"
    conservative.setups.minimum_scores[SetupFamily.BREAKOUT_CONFIRMATION] += 3.0
    conservative.setups.minimum_scores[SetupFamily.MEAN_REVERSION] += 3.0

    aggressive = settings.model_copy(deep=True)
    aggressive.risk.target_profile = "aggressive"
    aggressive.setups.minimum_scores[SetupFamily.TREND_CONTINUATION] = max(45.0, aggressive.setups.minimum_scores[SetupFamily.TREND_CONTINUATION] - 3.0)
    aggressive.setups.minimum_scores[SetupFamily.BREAKOUT_CONFIRMATION] = max(45.0, aggressive.setups.minimum_scores[SetupFamily.BREAKOUT_CONFIRMATION] - 3.0)

    return [("current", current), ("balanced", balanced), ("conservative", conservative), ("aggressive", aggressive)]


if __name__ == "__main__":
    main()
