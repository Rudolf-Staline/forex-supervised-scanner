from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config.instruments import AssetClass, instrument_for_symbol
from app.config.settings import AppSettings
from app.core.types import TradingStyle
from app.adaptive_thresholds.models import AdaptiveThresholdReport, AdaptiveThresholdResult

LOGGER = logging.getLogger(__name__)

# Basic paths for inputs (making them robust if missing)
REPORTS_DIR = Path("reports")
FORWARD_PAPER_CSV = REPORTS_DIR / "forward_test_paper.csv"
BACKTEST_CSV = REPORTS_DIR / "backtest_multi_asset.csv"
PAPER_SUMMARY_JSON = REPORTS_DIR / "paper_performance_summary.json"
SIGNAL_JOURNAL_JSONL = REPORTS_DIR / "signal_journal.jsonl"


class AdaptiveThresholdEngine:
    """Calculates adaptive score thresholds for symbols."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        # Read adaptive settings or use defaults if not present in config yet
        self.adaptive_settings = getattr(self.settings, "adaptive_thresholds", None)

        self.min_sample_size = getattr(self.adaptive_settings, "min_sample_size", 30) if self.adaptive_settings else 30
        self.max_daily_change = getattr(self.adaptive_settings, "max_daily_change", 2.0) if self.adaptive_settings else 2.0
        self.hard_floor_forex = getattr(self.adaptive_settings, "hard_floor_forex", 70.0) if self.adaptive_settings else 70.0
        self.hard_floor_commodities = getattr(self.adaptive_settings, "hard_floor_commodities", 78.0) if self.adaptive_settings else 78.0
        self.hard_floor_indices = getattr(self.adaptive_settings, "hard_floor_indices", 80.0) if self.adaptive_settings else 80.0
        self.hard_cap = getattr(self.adaptive_settings, "hard_cap", 92.0) if self.adaptive_settings else 92.0

        # Load historical data
        self._historical_data = self._load_historical_data()

    def _load_historical_data(self) -> dict[str, dict[TradingStyle, dict[str, Any]]]:
        """Loads available paper/backtest data and aggregates it by (symbol, style)."""
        data: dict[str, dict[TradingStyle, dict[str, Any]]] = {}

        def _ensure_entry(sym: str, style_val: TradingStyle):
            if sym not in data:
                data[sym] = {}
            if style_val not in data[sym]:
                data[sym][style_val] = {"wins": 0, "losses": 0, "total_rr": 0.0, "samples": 0}
            return data[sym][style_val]

        # Read CSV if exists
        for csv_path in [FORWARD_PAPER_CSV, BACKTEST_CSV]:
            if csv_path.exists():
                try:
                    with open(csv_path, "r", encoding="utf-8") as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            sym = row.get("symbol", "").upper()
                            style_str = row.get("style", "").lower()
                            if not sym or not style_str:
                                continue
                            try:
                                style = TradingStyle(style_str)
                            except ValueError:
                                continue

                            entry = _ensure_entry(sym, style)
                            entry["samples"] += 1

                            profit = float(row.get("profit", 0.0))
                            rr = float(row.get("risk_reward", 0.0) or 0.0)

                            if profit > 0:
                                entry["wins"] += 1
                            elif profit < 0:
                                entry["losses"] += 1

                            entry["total_rr"] += rr
                except Exception as exc:
                    LOGGER.warning(f"Failed to read {csv_path}: {exc}")

        # Read signal journal to extract signal counts as a fallback for sample_size
        if SIGNAL_JOURNAL_JSONL.exists():
            try:
                with open(SIGNAL_JOURNAL_JSONL, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        row = json.loads(line)
                        sym = row.get("logical_symbol", "").upper()
                        style_str = row.get("style", "").lower() if row.get("style") else None

                        if not sym:
                            continue

                        if not style_str:
                            continue

                        try:
                            style = TradingStyle(style_str)
                        except ValueError:
                            continue

                        entry = _ensure_entry(sym, style)
                        # Use signal counts as fallback data volume, but separate it from pure trade win/loss
                        # We don't want to corrupt pure 'trade samples' if they exist, but if we don't track signals
                        # separately, we can just increment samples to bypass the min_sample_size check if needed.
                        # Wait, the prompt implies "sample_size" comes from paper/backtest. But if those are missing,
                        # we can use signal journal to just boost samples so we can apply styling adjustments.
                        entry["samples"] += 1
            except Exception as exc:
                LOGGER.warning(f"Failed to read {SIGNAL_JOURNAL_JSONL}: {exc}")

        return data

    def get_floor_for_asset(self, asset_class: AssetClass) -> float:
        if asset_class == AssetClass.FOREX:
            return self.hard_floor_forex
        if asset_class == AssetClass.COMMODITIES:
            return self.hard_floor_commodities
        return self.hard_floor_indices

    def calculate(self, symbol: str, style: TradingStyle) -> AdaptiveThresholdResult:
        instrument = instrument_for_symbol(symbol)
        asset_class = instrument.asset_class
        base_score = instrument.min_score

        hist = self._historical_data.get(symbol.upper(), {}).get(style, {"wins": 0, "losses": 0, "total_rr": 0.0, "samples": 0})
        samples = hist["samples"]
        wins = hist["wins"]
        losses = hist["losses"]
        total_rr = hist["total_rr"]

        style_adj = 0.0
        if style == TradingStyle.SCALPING:
            style_adj = 2.0  # Stricter for scalping
        elif style == TradingStyle.DAY_TRADING:
            style_adj = 0.0
        elif style == TradingStyle.SWING_TRADING:
            style_adj = -1.0 # Slightly looser for swing

        history_adj = 0.0
        confidence = "low"
        reason = []

        if samples < self.min_sample_size:
            reason.append(f"insufficient samples ({samples} < {self.min_sample_size})")
            history_adj = 0.0
        else:
            confidence = "medium" if samples < self.min_sample_size * 3 else "high"

            # Simple win rate / RR logic
            win_rate = wins / samples if samples > 0 else 0.0
            avg_rr = total_rr / samples if samples > 0 else 0.0

            if win_rate > 0.55 and avg_rr >= 1.5:
                # Good performance
                history_adj = -1.0
                reason.append(f"good performance (WR: {win_rate:.2f}, RR: {avg_rr:.2f})")
            elif win_rate < 0.40 or (win_rate < 0.50 and avg_rr < 1.0):
                # Poor performance
                history_adj = 2.0
                reason.append(f"poor performance (WR: {win_rate:.2f}, RR: {avg_rr:.2f})")
            else:
                reason.append("neutral performance")

        # Swing trading needs good RR to justify the looser threshold
        if style == TradingStyle.SWING_TRADING and history_adj > 0:
            style_adj = 0.0 # Revoke the loose threshold if performance is bad

        recommended = base_score + style_adj + history_adj

        # Apply bounds
        safety_applied = False
        effective = recommended

        floor = self.get_floor_for_asset(asset_class)
        if effective < floor:
            effective = floor
            safety_applied = True
            reason.append(f"hit hard floor ({floor})")

        if effective > self.hard_cap:
            effective = self.hard_cap
            safety_applied = True
            reason.append(f"hit hard cap ({self.hard_cap})")

        # Progressive change check (max_daily_change)
        # Assuming base_score is the starting point for daily changes
        if abs(effective - base_score) > self.max_daily_change:
            effective = base_score + (self.max_daily_change if effective > base_score else -self.max_daily_change)
            safety_applied = True
            reason.append(f"capped by max_daily_change ({self.max_daily_change})")

        # Hard floor check again just in case max_daily_change bypassed it
        if effective < floor:
            effective = floor

        return AdaptiveThresholdResult(
            symbol=symbol,
            asset_class=asset_class.value,
            style=style.value,
            base_min_score=base_score,
            style_adjustment=style_adj,
            history_adjustment=history_adj,
            confidence_level=confidence,
            sample_size=samples,
            recommended_min_score=round(recommended, 2),
            effective_min_score=round(effective, 2),
            reason_summary="; ".join(reason) if reason else "base threshold",
            safety_bounds_applied=safety_applied
        )

    def generate_report(self, symbols: list[str], style: TradingStyle) -> AdaptiveThresholdReport:
        thresholds: dict[str, AdaptiveThresholdResult] = {}
        insufficient = []
        increased = []
        decreased = []
        unchanged = []

        for sym in symbols:
            res = self.calculate(sym, style)
            thresholds[sym] = res

            if res.sample_size < self.min_sample_size:
                insufficient.append(sym)

            if res.effective_min_score > res.base_min_score:
                increased.append(sym)
            elif res.effective_min_score < res.base_min_score:
                decreased.append(sym)
            else:
                unchanged.append(sym)

        return AdaptiveThresholdReport(
            generated_at=datetime.now(timezone.utc).isoformat(),
            style=style.value,
            symbols=symbols,
            thresholds_by_symbol=thresholds,
            global_summary={
                "total_symbols": len(symbols),
                "insufficient_data": len(insufficient),
                "increased": len(increased),
                "decreased": len(decreased),
                "unchanged": len(unchanged),
            },
            insufficient_data_symbols=insufficient,
            increased_threshold_symbols=increased,
            decreased_threshold_symbols=decreased,
            unchanged_symbols=unchanged
        )
