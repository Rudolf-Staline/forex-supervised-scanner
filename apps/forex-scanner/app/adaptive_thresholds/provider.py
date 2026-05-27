from __future__ import annotations

import logging
from typing import Literal

from app.config.instruments import instrument_for_symbol
from app.config.settings import AppSettings
from app.core.types import TradingStyle
from app.adaptive_thresholds.engine import AdaptiveThresholdEngine
from app.adaptive_thresholds.models import AdaptiveThresholdResult

LOGGER = logging.getLogger(__name__)


class AdaptiveThresholdProvider:
    """Provides adaptive minimum score thresholds depending on the configuration mode."""

    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.adaptive_settings = getattr(self.settings, "adaptive_thresholds", None)
        self.enabled = getattr(self.adaptive_settings, "enabled", False) if self.adaptive_settings else False
        self.mode = getattr(self.adaptive_settings, "mode", "report_only") if self.adaptive_settings else "report_only"
        self.engine = AdaptiveThresholdEngine(settings) if self.enabled else None

    def get_threshold(self, symbol: str, style: TradingStyle) -> AdaptiveThresholdResult:
        """
        Returns the threshold calculation for the symbol.
        If disabled, or if engine fails, returns a fallback static threshold result.
        """
        instrument = instrument_for_symbol(symbol)
        base_score = instrument.min_score

        if not self.enabled or not self.engine:
            return self._fallback(symbol, base_score, style, "disabled")

        try:
            return self.engine.calculate(symbol, style)
        except Exception as exc:
            LOGGER.warning(f"Adaptive threshold engine failed for {symbol}: {exc}. Using static fallback.")
            return self._fallback(symbol, base_score, style, "engine_error")

    def get_effective_min_score(self, symbol: str, style: TradingStyle) -> float:
        """
        Returns the single numeric score to be used by the pipeline.
        Only returns the dynamic score if mode == 'scanner_effective'.
        """
        result = self.get_threshold(symbol, style)
        if self.enabled and self.mode == "scanner_effective" and not result.is_fallback:
            return result.effective_min_score
        return result.base_min_score

    def _fallback(self, symbol: str, base_score: float, style: TradingStyle, reason: str) -> AdaptiveThresholdResult:
        instrument = instrument_for_symbol(symbol)
        return AdaptiveThresholdResult(
            symbol=symbol,
            asset_class=instrument.asset_class.value,
            style=style.value,
            base_min_score=base_score,
            style_adjustment=0.0,
            history_adjustment=0.0,
            confidence_level="none",
            sample_size=0,
            recommended_min_score=base_score,
            effective_min_score=base_score,
            reason_summary=reason,
            safety_bounds_applied=False,
            is_fallback=True
        )
