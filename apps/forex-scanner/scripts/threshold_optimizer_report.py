"""Informative threshold optimizer report. Analysis only; no strategy files are modified."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.env import load_dotenv
from app.config.instruments import AssetClass, filter_symbols_by_asset_class, instrument_for_symbol
from app.config.settings import load_settings
from app.config.watchlists import get_watchlist, watchlist_names
from app.execution.rejected_signals import RejectedSignalRecord
from app.storage.database import Database

BACKTEST_CSV = PROJECT_ROOT / "reports" / "backtest_multi_asset.csv"
BACKTEST_SUMMARY_JSON = PROJECT_ROOT / "reports" / "backtest_multi_asset_summary.json"
OPTIMIZER_CSV = PROJECT_ROOT / "reports" / "threshold_optimizer_report.csv"
OPTIMIZER_SUMMARY_JSON = PROJECT_ROOT / "reports" / "threshold_optimizer_summary.json"
ALERT = "Do not deploy optimized thresholds without forward testing."
MIN_SCORE_GRID = [55, 60, 65, 70, 75, 80, 82, 85]
MIN_RISK_REWARD_GRID = [1.5, 1.8, 2.0, 2.5]
MAX_SPREAD_ATR_GRID = [0.20, 0.22, 0.30, 0.40, 0.50]


@dataclass(frozen=True)
class ThresholdScenario:
    """One simulated threshold scenario for a grouped backtest row."""

    asset_class: str
    symbol: str
    setup: str
    session: str
    min_score: float
    min_risk_reward: float
    max_spread_atr: float
    total_trades_simulated: int
    win_rate: float
    expectancy_R: float
    profit_factor: float
    max_drawdown_R: float
    average_R: float
    rejected_count: int
    sample_size_warning: bool


def main() -> None:
    """Print an informative threshold simulation report."""

    parser = argparse.ArgumentParser(description="Analyze threshold candidates from backtest reports. No config is modified.")
    parser.add_argument("--asset-class", default="all", choices=["forex", "commodities", "indices", "all"])
    parser.add_argument("--watchlist", default="multi_asset_demo", choices=watchlist_names())
    parser.add_argument("--export-csv", action="store_true")
    parser.add_argument("--min-sample-size", type=int, default=30)
    args = parser.parse_args()

    load_dotenv()
    settings = load_settings()
    backtest_rows = load_backtest_rows(BACKTEST_CSV)
    summary = load_backtest_summary(BACKTEST_SUMMARY_JSON)
    rejected = Database(settings.database_absolute_path).load_rejected_signals()
    filtered_rows = filter_backtest_rows(backtest_rows, asset_class=args.asset_class, watchlist=args.watchlist)
    filtered_rejected = filter_rejected_records(rejected, asset_class=args.asset_class, watchlist=args.watchlist)
    scenarios = build_threshold_scenarios(filtered_rows, filtered_rejected, min_sample_size=args.min_sample_size)
    candidate_thresholds = build_candidate_thresholds(scenarios)
    print_report(scenarios, candidate_thresholds, summary, min_sample_size=args.min_sample_size)
    if args.export_csv:
        export_scenarios_csv(scenarios, OPTIMIZER_CSV)
        export_summary_json(candidate_thresholds, OPTIMIZER_SUMMARY_JSON)
        print(f"csv_export={OPTIMIZER_CSV}")
        print(f"summary_json_export={OPTIMIZER_SUMMARY_JSON}")


def load_backtest_rows(path: Path = BACKTEST_CSV) -> list[dict]:
    """Load grouped rows from backtest_multi_asset.csv."""

    if not path.exists():
        raise SystemExit(f"missing_backtest_report={path}; run scripts/backtest_multi_asset.py --export-csv first")
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def load_backtest_summary(path: Path = BACKTEST_SUMMARY_JSON) -> dict:
    """Load summary JSON if available."""

    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def filter_backtest_rows(rows: list[dict], *, asset_class: str, watchlist: str) -> list[dict]:
    """Filter report rows by watchlist and asset class."""

    symbols = set(get_watchlist(watchlist))
    if asset_class != "all":
        symbols = set(filter_symbols_by_asset_class(list(symbols), asset_class))
    return [row for row in rows if row.get("symbol") in symbols]


def filter_rejected_records(
    records: list[RejectedSignalRecord],
    *,
    asset_class: str,
    watchlist: str,
) -> list[RejectedSignalRecord]:
    """Filter rejected-signal records by watchlist and asset class."""

    symbols = set(get_watchlist(watchlist))
    if asset_class != "all":
        symbols = set(filter_symbols_by_asset_class(list(symbols), asset_class))
    return [record for record in records if record.symbol in symbols]


def build_threshold_scenarios(
    rows: list[dict],
    rejected_records: list[RejectedSignalRecord],
    *,
    min_sample_size: int,
) -> list[ThresholdScenario]:
    """Build threshold-grid scenarios using backtest rows and stored signal diagnostics."""

    rejected_by_key = _rejected_by_key(rejected_records)
    scenarios: list[ThresholdScenario] = []
    for row in rows:
        key = _row_key(row)
        diagnostics = _diagnostics_for_key(rejected_by_key.get(key, []))
        for min_score in MIN_SCORE_GRID:
            for min_rr in MIN_RISK_REWARD_GRID:
                for max_spread in MAX_SPREAD_ATR_GRID:
                    if not _row_passes_thresholds(row, diagnostics, min_score=min_score, min_rr=min_rr, max_spread=max_spread):
                        simulated_trades = 0
                        rejected_count = _int(row.get("total_signals")) or _int(row.get("rejected_count"))
                        metrics = _zero_metrics()
                    else:
                        simulated_trades = _int(row.get("total_trades_simulated"))
                        rejected_count = _int(row.get("rejected_count"))
                        metrics = row
                    total_sample = simulated_trades + rejected_count
                    scenarios.append(
                        ThresholdScenario(
                            asset_class=str(row.get("asset_class") or ""),
                            symbol=str(row.get("symbol") or ""),
                            setup=str(row.get("setup") or ""),
                            session=str(row.get("session") or ""),
                            min_score=float(min_score),
                            min_risk_reward=float(min_rr),
                            max_spread_atr=float(max_spread),
                            total_trades_simulated=simulated_trades,
                            win_rate=_float(metrics.get("win_rate")),
                            expectancy_R=_float(metrics.get("expectancy_R")),
                            profit_factor=_float(metrics.get("profit_factor")),
                            max_drawdown_R=_float(metrics.get("max_drawdown_R")),
                            average_R=_float(metrics.get("average_R")),
                            rejected_count=rejected_count,
                            sample_size_warning=total_sample < min_sample_size,
                        )
                    )
    return scenarios


def build_candidate_thresholds(scenarios: list[ThresholdScenario]) -> dict[str, dict[str, float | str]]:
    """Select informative candidate thresholds per asset class."""

    result: dict[str, dict[str, float | str]] = {}
    for asset in AssetClass:
        candidates = [
            scenario
            for scenario in scenarios
            if scenario.asset_class == asset.value and scenario.total_trades_simulated > 0 and not scenario.sample_size_warning
        ]
        if not candidates:
            candidates = [
                scenario
                for scenario in scenarios
                if scenario.asset_class == asset.value and scenario.total_trades_simulated > 0
            ]
        if not candidates:
            result[asset.value] = {"status": "insufficient_data"}
            continue
        best = max(
            candidates,
            key=lambda item: (
                item.expectancy_R,
                item.profit_factor,
                item.total_trades_simulated,
                -item.max_drawdown_R,
            ),
        )
        result[asset.value] = {
            "status": "informational_only",
            "min_score": best.min_score,
            "min_risk_reward": best.min_risk_reward,
            "max_spread_atr": best.max_spread_atr,
            "expectancy_R": best.expectancy_R,
            "profit_factor": best.profit_factor,
            "sample_size_warning": best.sample_size_warning,
        }
    return result


def print_report(
    scenarios: list[ThresholdScenario],
    candidate_thresholds: dict[str, dict[str, float | str]],
    backtest_summary: dict,
    *,
    min_sample_size: int,
) -> None:
    """Print the optimizer report."""

    print("threshold_optimizer_report=analysis_only")
    print(f"alert={ALERT}")
    print("config_modified=false")
    print("orders_sent=false")
    print(f"min_sample_size={min_sample_size}")
    print(f"scenarios_tested={len(scenarios)}")
    print("candidate_thresholds:")
    for asset in AssetClass:
        print(f"- {asset.value}: {_format_candidate(candidate_thresholds.get(asset.value, {'status': 'insufficient_data'}))}")
    if backtest_summary:
        print("backtest_summary_loaded=true")
    print("top_threshold_scenarios:")
    for scenario in _top_scenarios(scenarios)[:20]:
        print(
            "scenario "
            f"asset_class={scenario.asset_class} symbol={scenario.symbol} setup={scenario.setup} session={scenario.session} "
            f"min_score={scenario.min_score:.0f} min_risk_reward={scenario.min_risk_reward:.1f} "
            f"max_spread_atr={scenario.max_spread_atr:.2f} total_trades_simulated={scenario.total_trades_simulated} "
            f"win_rate={scenario.win_rate:.2f} expectancy_R={scenario.expectancy_R:.4f} "
            f"profit_factor={scenario.profit_factor:.4f} max_drawdown_R={scenario.max_drawdown_R:.4f} "
            f"average_R={scenario.average_R:.4f} rejected_count={scenario.rejected_count} "
            f"sample_size_warning={str(scenario.sample_size_warning).lower()}"
        )


def export_scenarios_csv(scenarios: list[ThresholdScenario], path: Path = OPTIMIZER_CSV) -> None:
    """Export threshold scenario rows to CSV."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(ThresholdScenario.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for scenario in scenarios:
            writer.writerow(asdict(scenario))


def export_summary_json(candidate_thresholds: dict[str, dict[str, float | str]], path: Path = OPTIMIZER_SUMMARY_JSON) -> None:
    """Export candidate thresholds to JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "alert": ALERT,
        "config_modified": False,
        "orders_sent": False,
        "candidate_thresholds": candidate_thresholds,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _row_passes_thresholds(
    row: dict,
    diagnostics: dict[str, float | None],
    *,
    min_score: float,
    min_rr: float,
    max_spread: float,
) -> bool:
    score = diagnostics.get("max_score")
    risk_reward = diagnostics.get("max_risk_reward")
    spread = diagnostics.get("average_spread_atr")
    row_has_trades = _int(row.get("total_trades_simulated")) > 0
    score_ok = True if score is None and row_has_trades else _float(score) >= min_score
    rr_ok = True if risk_reward is None and row_has_trades else _float(risk_reward) >= min_rr
    spread_ok = True if spread is None else _float(spread) <= max_spread
    return score_ok and rr_ok and spread_ok


def _diagnostics_for_key(records: list[RejectedSignalRecord]) -> dict[str, float | None]:
    scores = [float(record.score) for record in records if record.score is not None]
    risk_rewards = [float(record.risk_reward) for record in records if record.risk_reward is not None]
    spreads = [float(record.spread_atr) for record in records if record.spread_atr is not None]
    return {
        "max_score": max(scores) if scores else None,
        "max_risk_reward": max(risk_rewards) if risk_rewards else _rr_from_reasons(records),
        "average_spread_atr": sum(spreads) / len(spreads) if spreads else None,
    }


def _rr_from_reasons(records: list[RejectedSignalRecord]) -> float | None:
    values: list[float] = []
    for record in records:
        for reason in record.rejection_reasons:
            match = re.search(r"risk/reward\s+([0-9.]+)", reason)
            if match:
                values.append(float(match.group(1)))
    return max(values) if values else None


def _rejected_by_key(records: list[RejectedSignalRecord]) -> dict[tuple[str, str, str, str], list[RejectedSignalRecord]]:
    grouped: dict[tuple[str, str, str, str], list[RejectedSignalRecord]] = defaultdict(list)
    for record in records:
        asset_class = instrument_for_symbol(record.symbol).asset_class.value
        grouped[(asset_class, record.symbol, record.setup or "none", "any")].append(record)
    return grouped


def _row_key(row: dict) -> tuple[str, str, str, str]:
    return (
        str(row.get("asset_class") or ""),
        str(row.get("symbol") or ""),
        str(row.get("setup") or ""),
        "any",
    )


def _top_scenarios(scenarios: list[ThresholdScenario]) -> list[ThresholdScenario]:
    return sorted(
        [scenario for scenario in scenarios if scenario.total_trades_simulated > 0],
        key=lambda item: (
            item.expectancy_R,
            item.profit_factor,
            item.total_trades_simulated,
            -item.max_drawdown_R,
        ),
        reverse=True,
    )


def _format_candidate(candidate: dict[str, float | str]) -> str:
    if candidate.get("status") == "insufficient_data":
        return "status=insufficient_data"
    return (
        f"status={candidate.get('status')} min_score={candidate.get('min_score')} "
        f"min_risk_reward={candidate.get('min_risk_reward')} max_spread_atr={candidate.get('max_spread_atr')} "
        f"expectancy_R={candidate.get('expectancy_R')} profit_factor={candidate.get('profit_factor')} "
        f"sample_size_warning={str(candidate.get('sample_size_warning')).lower()}"
    )


def _zero_metrics() -> dict[str, float]:
    return {
        "win_rate": 0.0,
        "expectancy_R": 0.0,
        "profit_factor": 0.0,
        "max_drawdown_R": 0.0,
        "average_R": 0.0,
    }


def _int(value: object) -> int:
    if value in {None, ""}:
        return 0
    return int(float(str(value)))


def _float(value: object) -> float:
    if value in {None, ""}:
        return 0.0
    return float(value)


if __name__ == "__main__":
    main()
