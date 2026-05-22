"""Weighted setup scoring engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.config.settings import AppSettings, ContextSettings
from app.core.types import ConfidenceBucket, DataQualityDiagnostic, RawSetup, RiskPlan, SessionName, SetupFamily, SetupGrade
from app.data.validation import pip_size


@dataclass(frozen=True)
class ScoreResult:
    """Detailed score decomposition for candidate approval and diagnostics."""

    technical_score: float
    execution_score: float
    context_score: float
    empirical_score: float
    final_score: float
    confidence: ConfidenceBucket
    grade: SetupGrade
    session: SessionName
    components: dict[str, float]


class ScoringEngine:
    """Score a risk-planned setup on a configurable 0-100 scale."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def score(
        self,
        setup: RawSetup,
        risk_plan: RiskPlan,
        spread_price: float | None,
    ) -> tuple[float, ConfidenceBucket, dict[str, float]]:
        """Return numeric score, confidence bucket, and component scores."""

        result = self.score_detailed(setup, risk_plan, spread_price)
        return result.final_score, result.confidence, result.components

    def score_candidate(
        self,
        setup: RawSetup,
        risk_plan: RiskPlan | None,
        spread_price: float | None,
    ) -> tuple[float, ConfidenceBucket, dict[str, float]]:
        """Score a raw candidate before final approval.

        When no valid risk plan exists, technical components still contribute
        while risk/reward and spread-friction components are scored as failed.
        This keeps rejected candidates diagnostically useful without making
        them tradable.
        """

        result = self.score_detailed(setup, risk_plan, spread_price)
        return result.final_score, result.confidence, result.components

    def score_detailed(
        self,
        setup: RawSetup,
        risk_plan: RiskPlan | None,
        spread_price: float | None,
        *,
        data_quality: DataQualityDiagnostic | None = None,
        timestamp: datetime | None = None,
        empirical_score: float | None = None,
    ) -> ScoreResult:
        """Return technical, execution, context, empirical, and final scores."""

        session = market_session(timestamp)
        neutral_empirical = self.settings.empirical.neutral_score
        empirical = _clip(empirical_score if empirical_score is not None else neutral_empirical)
        invalidation_quality = _invalidation_quality(setup, risk_plan)

        components = {
            "trend_clarity": setup.trend_clarity,
            "structure_quality": setup.structure_quality,
            "multi_timeframe_alignment": setup.mtf_alignment,
            "volatility_suitability": setup.volatility_suitability,
            "momentum_confirmation": setup.momentum_confirmation,
            "spread_friction": _spread_score(setup.symbol, spread_price, setup.entry, risk_plan) if risk_plan is not None else 0.0,
            "spread_to_atr": _spread_to_atr_score(spread_price, setup.atr),
            "spread_to_stop": _spread_to_stop_score(setup.symbol, spread_price, setup.entry, risk_plan),
            "risk_reward": _rr_score(risk_plan.risk_reward, self.settings.styles[setup.style].min_rr) if risk_plan is not None else 0.0,
            "target_clearance": _target_clearance_score(risk_plan, self.settings.styles[setup.style].min_rr) if risk_plan is not None else 0.0,
            "level_proximity": setup.level_proximity,
            "activation_quality": setup.activation_quality,
            "invalidation_quality": invalidation_quality,
            "data_quality": data_quality.score if data_quality is not None else 82.0,
            "data_quality_execution": _data_quality_execution_score(data_quality),
            "session_quality": _session_score(session, setup.symbol),
            "volatility_exploitability": setup.volatility_suitability,
            "empirical_score": empirical,
            "empirical_adjustment": empirical - neutral_empirical,
            "pattern_score": setup.pattern_score,
        }

        component_weights = self.settings.weights.as_dict()
        technical_keys = [
            "trend_clarity",
            "structure_quality",
            "multi_timeframe_alignment",
            "volatility_suitability",
            "momentum_confirmation",
            "level_proximity",
        ]
        technical_score = round(_weighted_average(components, component_weights, technical_keys), 2)
        execution_keys = [
            "spread_friction",
            "spread_to_stop",
            "risk_reward",
            "target_clearance",
            "activation_quality",
            "invalidation_quality",
            "data_quality_execution",
        ]
        execution_score = round(_average(components[key] for key in execution_keys), 2)
        context_score = round(_context_score(components, data_quality, self.settings.context), 2)
        base_final = _layer_blend(technical_score, execution_score, context_score, empirical, self.settings.layer_weights.as_dict())
        final_score = round(_clip(base_final + min(15.0, setup.pattern_score) * 0.2), 2)
        return ScoreResult(
            technical_score=technical_score,
            execution_score=execution_score,
            context_score=context_score,
            empirical_score=round(empirical, 2),
            final_score=final_score,
            confidence=self._bucket(final_score),
            grade=_grade(final_score, technical_score, execution_score, context_score, empirical),
            session=session,
            components=components,
        )

    def minimum_score(self, family: SetupFamily) -> float:
        """Return configured minimum score for a setup family."""

        return self.settings.setups.minimum_scores.get(family, 100.0)

    def _bucket(self, score: float) -> ConfidenceBucket:
        thresholds = self.settings.confidence_thresholds
        if score >= thresholds.high:
            return ConfidenceBucket.HIGH
        if score >= thresholds.medium:
            return ConfidenceBucket.MEDIUM
        return ConfidenceBucket.LOW


def _spread_score(symbol: str, spread_price: float | None, entry: float, risk_plan: RiskPlan) -> float:
    if spread_price is None or spread_price != spread_price or spread_price <= 0.0:
        return 68.0
    risk_distance = abs(entry - risk_plan.stop_loss)
    if risk_distance <= 0.0:
        return 0.0
    pip = pip_size(symbol)
    spread_pips = spread_price / pip
    risk_pips = risk_distance / pip
    ratio = spread_pips / max(risk_pips, 1e-9)
    return max(0.0, min(100.0, 100.0 - ratio * 350.0))


def _rr_score(risk_reward: float, minimum_rr: float) -> float:
    if risk_reward < minimum_rr:
        return 0.0
    excess = min(risk_reward - minimum_rr, minimum_rr)
    return min(100.0, 62.0 + 38.0 * (excess / max(minimum_rr, 1e-9)))


def _target_clearance_score(risk_plan: RiskPlan, minimum_rr: float) -> float:
    nearest_rr = risk_plan.tp1_risk_reward
    if nearest_rr >= minimum_rr:
        return 100.0
    if nearest_rr <= 0.0:
        return 20.0
    return max(25.0, min(92.0, 35.0 + 57.0 * (nearest_rr / max(minimum_rr, 1e-9))))


def _spread_to_atr_score(spread_price: float | None, atr: float | None) -> float:
    if spread_price is None or spread_price != spread_price or spread_price <= 0.0:
        return 68.0
    if atr is None or atr <= 0.0:
        return 60.0
    ratio = spread_price / atr
    return _clip(100.0 - ratio * 360.0)


def _spread_to_stop_score(symbol: str, spread_price: float | None, entry: float, risk_plan: RiskPlan | None) -> float:
    if risk_plan is None:
        return 0.0
    return _spread_score(symbol, spread_price, entry, risk_plan)


def _data_quality_execution_score(data_quality: DataQualityDiagnostic | None) -> float:
    if data_quality is None:
        return 72.0
    score = data_quality.score
    if not data_quality.spread_available:
        score -= 10.0
    if data_quality.resampled:
        score -= 6.0
    if data_quality.stale_minutes is not None and data_quality.stale_minutes > 30.0:
        score -= min(12.0, (data_quality.stale_minutes - 30.0) / 10.0)
    if data_quality.missing_bars:
        score -= min(12.0, data_quality.missing_bars * 0.35)
    if data_quality.duplicate_bars:
        score -= min(10.0, data_quality.duplicate_bars * 1.0)
    return _clip(score)


def _invalidation_quality(setup: RawSetup, risk_plan: RiskPlan | None) -> float:
    if risk_plan is None:
        return setup.invalidation_quality
    risk_distance = abs(risk_plan.entry - risk_plan.stop_loss)
    atr = setup.atr or 0.0
    if atr <= 0.0 or risk_distance <= 0.0:
        return setup.invalidation_quality
    distance_atr = risk_distance / atr
    if 0.8 <= distance_atr <= 2.8:
        distance_score = 88.0
    elif distance_atr < 0.8:
        distance_score = max(25.0, 55.0 + distance_atr * 35.0)
    else:
        distance_score = max(35.0, 100.0 - (distance_atr - 2.8) * 18.0)
    structure_bonus = min(10.0, max(0.0, setup.structure_quality - 60.0) * 0.2)
    return _clip((setup.invalidation_quality * 0.45) + (distance_score * 0.45) + structure_bonus)


def market_session(timestamp: datetime | None) -> SessionName:
    """Map a timestamp to a coarse FX session bucket."""

    moment = timestamp or datetime.now(timezone.utc)
    hour = moment.astimezone(timezone.utc).hour
    if 0 <= hour < 7:
        return SessionName.ASIA
    if 7 <= hour < 13:
        return SessionName.LONDON
    if 13 <= hour < 17:
        return SessionName.NEW_YORK_OVERLAP
    if 17 <= hour < 21:
        return SessionName.NEW_YORK
    return SessionName.OFF_HOURS


def _session_score(session: SessionName, symbol: str) -> float:
    normalized = symbol.replace("/", "").upper()
    if session == SessionName.NEW_YORK_OVERLAP:
        return 92.0
    if session == SessionName.LONDON:
        return 88.0
    if session == SessionName.NEW_YORK:
        return 78.0
    if session == SessionName.ASIA:
        return 75.0 if normalized.endswith("JPY") or "JPY" in normalized else 62.0
    return 46.0


def _context_score(components: dict[str, float], data_quality: DataQualityDiagnostic | None, settings: ContextSettings) -> float:
    score = _average(
        [
            components["session_quality"],
            components["data_quality"],
            components["spread_to_atr"],
            components["volatility_exploitability"],
        ]
    )
    if components["session_quality"] < 55.0:
        score -= settings.dead_session_penalty
    if components["data_quality"] < settings.minimum_data_quality:
        deficit = settings.minimum_data_quality - components["data_quality"]
        score -= min(settings.stale_data_penalty, deficit * 0.35)
    if components["spread_to_atr"] < 55.0:
        score -= min(settings.poor_spread_atr_penalty, (55.0 - components["spread_to_atr"]) * 0.25)
    if data_quality is not None and (data_quality.missing_bars > 0 or data_quality.duplicate_bars > 0):
        score -= min(12.0, data_quality.missing_bars * 0.4 + data_quality.duplicate_bars * 1.0)
    return _clip(score)


def _weighted_average(components: dict[str, float], weights: dict[str, float], keys: list[str]) -> float:
    total = sum(weights.get(key, 0.0) for key in keys)
    if total <= 0.0:
        return _average(components[key] for key in keys)
    return sum(components[key] * weights.get(key, 0.0) for key in keys) / total


def _layer_blend(technical: float, execution: float, context: float, empirical: float, weights: dict[str, float]) -> float:
    values = {
        "technical": technical,
        "execution": execution,
        "context": context,
        "empirical": empirical,
    }
    total = sum(weights.values())
    if total <= 0.0:
        return _average(values.values())
    return _clip(sum(values[key] * weight for key, weight in weights.items()) / total)


def _average(values: object) -> float:
    numeric = [float(value) for value in values]
    if not numeric:
        return 0.0
    return _clip(sum(numeric) / len(numeric))


def _clip(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _grade(final_score: float, technical_score: float, execution_score: float, context_score: float, empirical_score: float) -> SetupGrade:
    if final_score >= 72.0 and technical_score >= 68.0 and execution_score >= 62.0 and context_score >= 62.0 and empirical_score >= 50.0:
        return SetupGrade.A
    if final_score >= 60.0 and technical_score >= 58.0:
        return SetupGrade.B
    if final_score >= 48.0 or technical_score >= 55.0:
        return SetupGrade.C
    return SetupGrade.D
