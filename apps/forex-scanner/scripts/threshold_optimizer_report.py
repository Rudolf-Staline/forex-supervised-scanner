"""Informative threshold optimizer report. Analysis only; no strategy files are modified."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from app.config.instruments import AssetClass, filter_symbols_by_asset_class
from app.config.watchlists import get_watchlist, watchlist_names

BACKTEST_CSV = Path("reports/backtest_multi_asset.csv")
BACKTEST_SUMMARY_JSON = Path("reports/backtest_multi_asset_summary.json")
SIGNAL_JOURNAL_JSONL = Path("reports/signal_journal.jsonl")
OPTIMIZER_CSV = Path("reports/threshold_optimizer_report.csv")
OPTIMIZER_SUMMARY_JSON = Path("reports/threshold_optimizer_summary.json")
ALERT = "Do not deploy optimized thresholds without forward testing."
MIN_SCORE_GRID = [55, 60, 65, 70, 75, 80, 82, 85]
MIN_RISK_REWARD_GRID = [1.5, 1.8, 2.0, 2.5]
MAX_SPREAD_ATR_GRID = [0.20, 0.22, 0.30, 0.40, 0.50]


@dataclass(frozen=True)
class ThresholdScenario:
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
    parser = argparse.ArgumentParser(description="Informative threshold optimizer report. No threshold is deployed automatically.")
    parser.add_argument("--asset-class", default="all", choices=["forex", "commodities", "indices", "all"])
    parser.add_argument("--watchlist", default="multi_asset_demo", choices=watchlist_names())
    parser.add_argument("--export-csv", action="store_true")
    parser.add_argument("--min-sample-size", type=int, default=30)
    args = parser.parse_args()

    missing = [str(p) for p in [BACKTEST_CSV, SIGNAL_JOURNAL_JSONL] if not p.exists()]
    if missing:
        print("threshold_optimizer_report=skipped")
        print(f"missing_inputs={','.join(missing)}")
        print("hint=run scripts/backtest_multi_asset.py or scripts/run_one_cycle.py first")
        return

    backtest_rows = load_backtest_rows(BACKTEST_CSV)
    summary = load_backtest_summary(BACKTEST_SUMMARY_JSON)
    journal = load_signal_journal(SIGNAL_JOURNAL_JSONL)

    filtered_rows = filter_backtest_rows(backtest_rows, asset_class=args.asset_class, watchlist=args.watchlist)
    filtered_journal = filter_journal_records(journal, asset_class=args.asset_class, watchlist=args.watchlist)
    scenarios = build_threshold_scenarios(filtered_rows, filtered_journal, min_sample_size=args.min_sample_size)
    candidate_thresholds = build_candidate_thresholds(scenarios)

    print_report(scenarios, candidate_thresholds, summary, min_sample_size=args.min_sample_size)
    export_summary_json(candidate_thresholds, OPTIMIZER_SUMMARY_JSON)
    print(f"summary_json_export={OPTIMIZER_SUMMARY_JSON}")
    if args.export_csv:
        export_scenarios_csv(scenarios, OPTIMIZER_CSV)
        print(f"csv_export={OPTIMIZER_CSV}")


def load_backtest_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def load_backtest_summary(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_signal_journal(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def filter_backtest_rows(rows: list[dict], *, asset_class: str, watchlist: str) -> list[dict]:
    symbols = set(get_watchlist(watchlist))
    if asset_class != "all":
        symbols = set(filter_symbols_by_asset_class(list(symbols), asset_class))
    return [row for row in rows if row.get("symbol") in symbols]


def filter_journal_records(records: list[dict], *, asset_class: str, watchlist: str) -> list[dict]:
    symbols = set(get_watchlist(watchlist))
    if asset_class != "all":
        symbols = set(filter_symbols_by_asset_class(list(symbols), asset_class))
    return [record for record in records if str(record.get("symbol") or "") in symbols]


def build_threshold_scenarios(rows: list[dict], journal_records: list[dict], *, min_sample_size: int) -> list[ThresholdScenario]:
    diagnostics_by_key = _journal_diagnostics_by_key(journal_records)
    scenarios: list[ThresholdScenario] = []
    for row in rows:
        metrics = {k: _float(row.get(k)) for k in ["win_rate", "expectancy_R", "profit_factor", "max_drawdown_R", "average_R"]}
        row_trades = _int(row.get("total_trades_simulated"))
        row_rejected = _int(row.get("rejected_count"))
        diag = diagnostics_by_key[_row_key(row)]
        for min_score in MIN_SCORE_GRID:
            for min_rr in MIN_RISK_REWARD_GRID:
                for max_spread in MAX_SPREAD_ATR_GRID:
                    if _passes(diag, min_score=min_score, min_rr=min_rr, max_spread=max_spread):
                        trades = row_trades
                        rejected = row_rejected
                        use = metrics
                    else:
                        trades = 0
                        rejected = row_trades + row_rejected
                        use = {"win_rate": 0.0, "expectancy_R": 0.0, "profit_factor": 0.0, "max_drawdown_R": 0.0, "average_R": 0.0}
                    total = trades + rejected
                    scenarios.append(ThresholdScenario(str(row.get("asset_class") or ""), str(row.get("symbol") or ""), str(row.get("setup") or ""), str(row.get("session") or ""), float(min_score), float(min_rr), float(max_spread), trades, use["win_rate"], use["expectancy_R"], use["profit_factor"], use["max_drawdown_R"], use["average_R"], rejected, total < min_sample_size))
    return scenarios


def build_candidate_thresholds(scenarios: list[ThresholdScenario]) -> dict[str, dict[str, float | str | bool]]:
    out: dict[str, dict[str, float | str | bool]] = {}
    for asset in ["forex", "commodities", "indices"]:
        choices = [s for s in scenarios if s.asset_class == asset and s.total_trades_simulated > 0 and not s.sample_size_warning]
        if not choices:
            choices = [s for s in scenarios if s.asset_class == asset and s.total_trades_simulated > 0]
        if not choices:
            out[asset] = {"status": "insufficient_data"}
            continue
        best = max(choices, key=lambda s: (s.expectancy_R, s.profit_factor, s.total_trades_simulated, -s.max_drawdown_R))
        out[asset] = {"status": "informational_only", "min_score": best.min_score, "min_risk_reward": best.min_risk_reward, "max_spread_atr": best.max_spread_atr, "expectancy_R": best.expectancy_R, "profit_factor": best.profit_factor, "sample_size_warning": best.sample_size_warning}
    return out


def print_report(scenarios: list[ThresholdScenario], candidate_thresholds: dict[str, dict[str, float | str | bool]], backtest_summary: dict, *, min_sample_size: int) -> None:
    print("threshold_optimizer_report=analysis_only")
    print(f"alert={ALERT}")
    print("config_modified=false")
    print("orders_sent=false")
    print(f"min_sample_size={min_sample_size}")
    print(f"scenarios_tested={len(scenarios)}")
    if backtest_summary:
        print("backtest_summary_loaded=true")
    print("candidate_thresholds:")
    for asset in ["forex", "commodities", "indices"]:
        print(f"- {asset}: {candidate_thresholds.get(asset, {'status': 'insufficient_data'})}")


def export_scenarios_csv(scenarios: list[ThresholdScenario], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(ThresholdScenario.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(asdict(s) for s in scenarios)


def export_summary_json(candidate_thresholds: dict[str, dict[str, float | str | bool]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"alert": ALERT, "config_modified": False, "orders_sent": False, "candidate_thresholds": candidate_thresholds}, indent=2), encoding="utf-8")


def _journal_diagnostics_by_key(records: list[dict]) -> dict[tuple[str, str, str, str], dict[str, float | None]]:
    grouped: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for record in records:
        key = (str(record.get("asset_class") or ""), str(record.get("symbol") or ""), str(record.get("setup") or ""), str(record.get("session") or ""))
        grouped[key].append(record)
    out: dict[tuple[str, str, str, str], dict[str, float | None]] = defaultdict(lambda: {"max_score": None, "max_risk_reward": None, "avg_spread_atr": None})
    for key, bucket in grouped.items():
        scores = [_float(r.get("score")) for r in bucket if r.get("score") not in (None, "")]
        rrs = [_float(r.get("risk_reward")) for r in bucket if r.get("risk_reward") not in (None, "")]
        spreads = [_float(r.get("spread_atr")) for r in bucket if r.get("spread_atr") not in (None, "")]
        out[key] = {"max_score": max(scores) if scores else None, "max_risk_reward": max(rrs) if rrs else None, "avg_spread_atr": (sum(spreads) / len(spreads)) if spreads else None}
    return out


def _passes(diag: dict[str, float | None], *, min_score: float, min_rr: float, max_spread: float) -> bool:
    score_ok = diag["max_score"] is None or diag["max_score"] >= min_score
    rr_ok = diag["max_risk_reward"] is None or diag["max_risk_reward"] >= min_rr
    spread_ok = diag["avg_spread_atr"] is None or diag["avg_spread_atr"] <= max_spread
    return bool(score_ok and rr_ok and spread_ok)


def _row_key(row: dict) -> tuple[str, str, str, str]:
    return (str(row.get("asset_class") or ""), str(row.get("symbol") or ""), str(row.get("setup") or ""), str(row.get("session") or ""))


def _int(v: object) -> int:
    return 0 if v in (None, "") else int(float(str(v)))


def _float(v: object) -> float:
    return 0.0 if v in (None, "") else float(v)


if __name__ == "__main__":
    main()
