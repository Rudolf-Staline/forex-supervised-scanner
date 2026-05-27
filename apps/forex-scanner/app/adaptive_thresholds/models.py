from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AdaptiveThresholdResult:
    """Detailed calculations and outcome of adaptive threshold logic for one symbol."""

    symbol: str
    asset_class: str
    style: str
    base_min_score: float
    style_adjustment: float
    history_adjustment: float
    confidence_level: str
    sample_size: int
    recommended_min_score: float
    effective_min_score: float
    reason_summary: str
    safety_bounds_applied: bool
    is_fallback: bool = False

    @property
    def increased(self) -> bool:
        return self.effective_min_score > self.base_min_score

    @property
    def decreased(self) -> bool:
        return self.effective_min_score < self.base_min_score


@dataclass
class AdaptiveThresholdReport:
    """Summary of all symbols calculated for a specific run."""

    generated_at: str
    style: str
    symbols: list[str]
    thresholds_by_symbol: dict[str, AdaptiveThresholdResult]
    global_summary: dict[str, object]
    insufficient_data_symbols: list[str]
    increased_threshold_symbols: list[str]
    decreased_threshold_symbols: list[str]
    unchanged_symbols: list[str]
    safety_warning: str = "This adaptive threshold calculation is for informational/paper testing purposes only. It is not proof of profitability and must not be used for live trading."
