"""Run a local backtest and report performance grouped by setup subtype."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backtest.engine import Backtester
from app.backtest.setup_report import SetupBacktestSummary, export_setup_summaries_csv, summarize_setups
from app.config.env import load_dotenv
from app.config.settings import load_settings
from app.core.types import TradingStyle
from app.data.providers import build_provider
from app.storage.database import Database


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest Forex Supervisor setups and export per-setup metrics.")
    parser.add_argument("--provider", default="synthetic", choices=["synthetic", "auto", "mt5"])
    parser.add_argument("--symbols", nargs="+", default=["EUR/USD", "GBP/USD", "USD/CHF"])
    parser.add_argument("--style", default=TradingStyle.DAY_TRADING.value, choices=[style.value for style in TradingStyle])
    parser.add_argument("--from-date", default=None, help="UTC start date, e.g. 2026-05-01.")
    parser.add_argument("--to-date", default=None, help="UTC end date, e.g. 2026-05-21.")
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--setup", default=None, help="Optional setup subtype, e.g. ema50_pullback or momentum_breakout.")
    args = parser.parse_args()

    load_dotenv()
    settings = load_settings().model_copy(deep=True)
    settings.provider.name = args.provider
    style = TradingStyle(args.style)
    end = _parse_date(args.to_date) if args.to_date else datetime.now(timezone.utc)
    start = _parse_date(args.from_date) if args.from_date else end - timedelta(days=14)
    database = Database(settings.database_absolute_path)
    provider = build_provider(settings)

    print(
        "backtest_setups "
        f"provider={provider.name} style={style.value} symbols={','.join(args.symbols)} "
        f"from={start.date()} to={end.date()} min_score={args.min_score if args.min_score is not None else 'engine_default'} "
        f"setup={args.setup or 'all'}"
    )
    print("warning=Backtest simplifie; les resultats passes ne garantissent aucune performance future.")
    result = Backtester(settings, provider, database).run(args.symbols, style, "all", start, end)
    summaries = summarize_setups(result.trades, setup_filter=args.setup, min_score=args.min_score)
    output = export_setup_summaries_csv(summaries, PROJECT_ROOT / "reports" / "backtest_setups.csv")
    _print_summaries(summaries, total_trades=len(result.trades), output=output)


def _parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _print_summaries(summaries: list[SetupBacktestSummary], *, total_trades: int, output: Path) -> None:
    print(f"total_backtest_trades={total_trades}")
    print(f"setup_rows={len(summaries)}")
    if not summaries:
        print("No setup trades matched the requested filters.")
        print(f"csv_export={output}")
        return
    for row in summaries:
        print(
            "setup "
            f"name={row.setup} trades={row.total_trades} wins={row.wins} losses={row.losses} "
            f"win_rate={row.win_rate:.2f} average_R={row.average_R:.4f} "
            f"profit_factor={row.profit_factor:.4f} max_drawdown={row.max_drawdown:.4f} "
            f"expectancy={row.expectancy:.4f} best_symbol={row.best_symbol or '-'} worst_symbol={row.worst_symbol or '-'}"
        )
    print(f"csv_export={output}")


if __name__ == "__main__":
    main()
