"""End-to-end scan orchestration for market data, indicators, setups, risk, and scoring."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from app.config.settings import AppSettings, ApprovalSettings
from app.core.diagnostics import build_gate_breakdown, failed_gate_names, rejection_category, rejection_summary
from app.core.types import (
    ConfidenceBucket,
    DataQualityDiagnostic,
    DirectionBias,
    GateBreakdown,
    MarketRegime,
    Opportunity,
    OpportunityStatus,
    RejectionCategory,
    RiskPlan,
    ScanReport,
    SetupGrade,
    SetupFamily,
    SetupSubtype,
    SymbolAnalysisError,
    Timeframe,
    TradingStyle,
    SessionName,
)
from app.data.providers import DataProviderError, MarketDataProvider, debug_market_data_enabled
from app.data.validation import DataValidationError, window_for_bars
from app.indicators.calculations import add_indicators
from app.indicators.levels import find_key_levels
from app.market_regime.regime import MarketRegimeDetector
from app.risk.engine import RiskEngine
from app.scoring.engine import ScoringEngine, market_session
from app.setups.detector import detect_setups
from app.storage.database import Database
from app.adaptive_thresholds.provider import AdaptiveThresholdProvider

LOGGER = logging.getLogger(__name__)


class ScannerService:
    """Main application service for one-shot technical scans."""

    def __init__(
        self,
        settings: AppSettings,
        provider: MarketDataProvider,
        database: Database | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.database = database
        self.regime_detector = MarketRegimeDetector()
        self.risk_engine = RiskEngine(settings)
        self.scoring_engine = ScoringEngine(settings)
        self.adaptive_thresholds = AdaptiveThresholdProvider(settings)

    def scan(self, style: TradingStyle, symbols: list[str], timestamp: datetime | None = None) -> ScanReport:
        """Scan a symbol universe and return ranked opportunities plus recoverable errors."""

        timestamp = timestamp or datetime.now(timezone.utc)
        opportunities: list[Opportunity] = []
        errors: list[SymbolAnalysisError] = []
        for symbol in symbols:
            try:
                opportunities.extend(self._analyze_symbol(symbol, style, timestamp))
            except (DataProviderError, DataValidationError) as exc:
                if debug_market_data_enabled():
                    LOGGER.exception("symbol analysis failed", extra={"symbol": symbol, "style": style.value})
                else:
                    LOGGER.warning(
                        "symbol analysis failed",
                        extra={"symbol": symbol, "style": style.value, "error": str(exc)},
                    )
                errors.append(SymbolAnalysisError(symbol=symbol, reason=str(exc)))
            except Exception as exc:
                LOGGER.exception("symbol analysis failed", extra={"symbol": symbol, "style": style.value})
                errors.append(SymbolAnalysisError(symbol=symbol, reason=str(exc)))

        opportunities.sort(key=lambda item: (_status_rank(item.status), item.final_score or item.score), reverse=True)
        report = ScanReport(timestamp=timestamp, style=style, opportunities=opportunities, errors=errors)
        if self.database is not None:
            self.database.save_scan_report(report)
        return report

    def _analyze_symbol(self, symbol: str, style: TradingStyle, timestamp: datetime) -> list[Opportunity]:
        style_settings = self.settings.styles[style]
        higher_tf = style_settings.higher_timeframe
        entry_tf = style_settings.entry_timeframe
        trigger_tf = style_settings.trigger_timeframe
        higher = self._fetch_enriched(symbol, higher_tf, style_settings.lookback_bars, timestamp)
        entry = self._fetch_enriched(symbol, entry_tf, style_settings.lookback_bars, timestamp)
        trigger = self._fetch_enriched(symbol, trigger_tf, style_settings.lookback_bars, timestamp)

        higher_regime = self.regime_detector.analyze(higher)
        entry_regime = self.regime_detector.analyze(entry)
        trigger_regime = self.regime_detector.analyze(trigger)
        levels = find_key_levels(entry, tolerance_atr=self.settings.setups.level_tolerance_atr)
        provider_name = str(entry.attrs.get("provider", self.provider.name))
        warning = _first_warning(higher, entry, trigger)
        data_quality = _combined_data_quality(higher, entry, trigger)

        raw_setups = detect_setups(
            symbol=symbol,
            style=style,
            higher_df=higher,
            entry_df=entry,
            trigger_df=trigger,
            higher_regime=higher_regime,
            entry_regime=entry_regime,
            trigger_regime=trigger_regime,
            levels=levels,
            settings=self.settings,
        )
        opportunities: list[Opportunity] = []
        rejected_candidates: list[Opportunity] = []
        spread = _latest_spread(entry)
        session = market_session(timestamp)

        for raw in raw_setups:
            risk_decision = self.risk_engine.plan(raw, style)
            scoring_plan = risk_decision.plan or risk_decision.diagnostic_plan
            empirical = _empirical_score(
                database=self.database,
                symbol=symbol,
                style=style,
                family=raw.family,
                subtype=raw.subtype,
                session=session,
                regime=raw.regime,
                neutral=self.settings.empirical.neutral_score,
                minimum_samples=self.settings.empirical.minimum_samples,
                min_condition_samples=self.settings.empirical.min_condition_samples,
                shrinkage_samples=self.settings.empirical.shrinkage_samples,
                max_adjustment=self.settings.empirical.max_adjustment,
            )
            score_result = self.scoring_engine.score_detailed(
                raw,
                scoring_plan,
                spread,
                data_quality=data_quality,
                timestamp=timestamp,
                empirical_score=empirical,
            )

            adaptive_res = self.adaptive_thresholds.get_threshold(symbol, style)
            effective_min_score = adaptive_res.effective_min_score if self.adaptive_thresholds.enabled and self.adaptive_thresholds.mode == "scanner_effective" else adaptive_res.base_min_score

            minimum_score = effective_min_score
            minimum_rr = self.settings.styles[style].min_rr
            gates = build_gate_breakdown(
                setup=raw,
                risk_plan=scoring_plan,
                score=score_result.final_score,
                minimum_score=minimum_score,
                minimum_rr=minimum_rr,
            )
            status = _candidate_status(
                raw.watchlist_candidate,
                risk_decision.plan,
                score_result.final_score,
                score_result.technical_score,
                score_result.execution_score,
                score_result.context_score,
                score_result.empirical_score,
                minimum_score,
                self.settings.approval,
                data_quality.score if data_quality is not None else None,
                raw.activation_quality,
                raw.invalidation_quality,
            )
            approved = status in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM}

            if not approved:
                approval_gaps = _approval_gaps(
                    execution_score=score_result.execution_score,
                    context_score=score_result.context_score,
                    empirical_score=score_result.empirical_score,
                    data_quality_score=data_quality.score if data_quality is not None else None,
                    activation_quality=raw.activation_quality,
                    invalidation_quality=raw.invalidation_quality,
                    approval=self.settings.approval,
                )
                base_reason = rejection_summary(
                    gates=gates,
                    rejection_reason=risk_decision.rejection_reason,
                    score=score_result.final_score,
                    minimum_score=minimum_score,
                    minimum_rr=minimum_rr,
                    risk_plan=scoring_plan,
                )
                reason_parts = [base_reason, *approval_gaps]
                reason = "; ".join(item for item in dict.fromkeys(reason_parts) if item)
                missing_conditions = _activation_gaps(
                    raw.missing_conditions,
                    gates,
                    risk_decision.rejection_reason,
                    score_result.final_score,
                    minimum_score,
                    approval_gaps,
                )
                rejected_candidates.append(
                    _opportunity_from_candidate(
                        timestamp=timestamp,
                        symbol=symbol,
                        style=style,
                        setup_family=raw.family,
                        setup_subtype=raw.subtype,
                        regime=raw.regime,
                        direction=raw.direction,
                        score=score_result.final_score,
                        confidence=score_result.confidence,
                        risk_plan=scoring_plan,
                        explanation=f"Rejected {raw.family.value}: {raw.explanation}",
                        timeframe_higher=higher_tf,
                        timeframe_entry=entry_tf,
                        timeframe_trigger=trigger_tf,
                        score_components=score_result.components,
                        provider=provider_name,
                        data_warning=warning,
                        approved=False,
                        status=status,
                        raw_setup_family=raw.family,
                        pre_gate_score=score_result.final_score,
                        technical_score=score_result.technical_score,
                        execution_score=score_result.execution_score,
                        context_score=score_result.context_score,
                        empirical_score=score_result.empirical_score,
                        final_score=score_result.final_score,
                        grade=score_result.grade,
                        gate_breakdown=gates,
                        rejection_reason=reason,
                        rejection_category=rejection_category(gates, reason),
                        required_min_rr=minimum_rr,
                        missing_conditions=missing_conditions,
                        invalidation="; ".join(raw.invalidation_notes) if raw.invalidation_notes else None,
                        activation_quality=raw.activation_quality,
                        invalidation_quality=raw.invalidation_quality,
                        spread=spread,
                        atr=raw.atr,
                        key_level_distances=raw.key_level_distances,
                        session=score_result.session,
                        htf_regime=higher_regime.regime,
                        entry_regime=entry_regime.regime,
                        trigger_regime=trigger_regime.regime,
                        data_quality=data_quality,
                        detected_patterns=raw.detected_patterns,
                        pattern_score=raw.pattern_score,
                        pattern_explanations=raw.pattern_explanations,
                    )
                )
                continue

            opportunity = _opportunity_from_candidate(
                timestamp=timestamp,
                symbol=symbol,
                style=style,
                    setup_family=raw.family,
                    setup_subtype=raw.subtype,
                    regime=raw.regime,
                    direction=raw.direction,
                    score=score_result.final_score,
                    confidence=score_result.confidence,
                    risk_plan=risk_decision.plan,
                    explanation=f"{raw.explanation} SL via {risk_decision.plan.stop_method}; TP via {risk_decision.plan.target_method}.",
                    timeframe_higher=higher_tf,
                    timeframe_entry=entry_tf,
                    timeframe_trigger=trigger_tf,
                    score_components=score_result.components,
                    provider=provider_name,
                    data_warning=warning,
                    approved=True,
                    status=status,
                    raw_setup_family=raw.family,
                    pre_gate_score=score_result.final_score,
                    technical_score=score_result.technical_score,
                    execution_score=score_result.execution_score,
                    context_score=score_result.context_score,
                    empirical_score=score_result.empirical_score,
                    final_score=score_result.final_score,
                    grade=score_result.grade,
                    gate_breakdown=gates,
                    failed_gates=[],
                    rejection_reason=None,
                    rejection_category=None,
                    required_min_rr=minimum_rr,
                    missing_conditions=[],
                    invalidation="; ".join(raw.invalidation_notes) if raw.invalidation_notes else None,
                    activation_quality=raw.activation_quality,
                    invalidation_quality=raw.invalidation_quality,
                    spread=spread,
                    atr=raw.atr,
                    key_level_distances=raw.key_level_distances,
                    session=score_result.session,
                    htf_regime=higher_regime.regime,
                    entry_regime=entry_regime.regime,
                    trigger_regime=trigger_regime.regime,
                    data_quality=data_quality,
                    detected_patterns=raw.detected_patterns,
                pattern_score=raw.pattern_score,
                pattern_explanations=raw.pattern_explanations,
            )
            opportunity.adaptive_threshold_enabled = self.adaptive_thresholds.enabled
            opportunity.base_min_score = adaptive_res.base_min_score
            opportunity.adaptive_min_score = adaptive_res.recommended_min_score
            opportunity.effective_min_score = adaptive_res.effective_min_score
            opportunity.adaptive_threshold_confidence = adaptive_res.confidence_level
            opportunity.adaptive_threshold_reason = adaptive_res.reason_summary

            opportunities.append(opportunity)

        if opportunities or rejected_candidates:
            return opportunities + rejected_candidates

        reason = higher_regime.explanation
        return [
            Opportunity(
                timestamp=timestamp,
                symbol=symbol,
                style=style,
                setup_family=SetupFamily.NO_TRADE,
                setup_subtype=SetupSubtype.NONE,
                regime=higher_regime.regime if higher_regime.regime != MarketRegime.HIGH_VOLATILITY else MarketRegime.HIGH_VOLATILITY,
                direction=DirectionBias.NO_TRADE,
                score=0.0,
                confidence=ConfidenceBucket.LOW,
                entry=None,
                stop_loss=None,
                take_profit=None,
                risk_reward=None,
                explanation=f"No ranked setup: {reason}",
                timeframe_higher=higher_tf,
                timeframe_entry=entry_tf,
                timeframe_trigger=trigger_tf,
                score_components={},
                provider=provider_name,
                data_warning=warning,
                rejection_reason=reason,
                approved=False,
                status=OpportunityStatus.REJECTED,
                raw_setup_family=None,
                pre_gate_score=None,
                technical_score=None,
                execution_score=None,
                context_score=None,
                empirical_score=None,
                final_score=0.0,
                grade=None,
                gate_breakdown=None,
                failed_gates=[],
                rejection_category=RejectionCategory.NO_RAW_SETUP,
                required_min_rr=style_settings.min_rr,
                missing_conditions=["no raw setup detected on the current completed candles"],
                invalidation=None,
                session=session,
                htf_regime=higher_regime.regime,
                entry_regime=entry_regime.regime,
                trigger_regime=trigger_regime.regime,
                data_quality=data_quality,
            )
        ]

    def _fetch_enriched(self, symbol: str, timeframe: Timeframe, bars: int, end: datetime) -> pd.DataFrame:
        window = window_for_bars(timeframe, min(bars, self.settings.provider.max_bars), end)
        raw = self.provider.get_ohlcv(symbol, timeframe, window.start, window.end)
        enriched = add_indicators(raw)
        if len(enriched.dropna(subset=["ema_200", "atr_14"])) < 20:
            raise DataValidationError(f"{symbol} {timeframe.value} has insufficient indicator history")
        enriched.attrs.update(raw.attrs)
        return enriched.tail(self.settings.provider.max_bars)


def _first_warning(*frames: pd.DataFrame) -> str | None:
    for frame in frames:
        warning = frame.attrs.get("warning")
        if warning:
            return str(warning)
    return None


def _latest_spread(frame: pd.DataFrame) -> float | None:
    if "spread" not in frame:
        return None
    value = frame["spread"].dropna()
    if value.empty:
        return None
    return float(value.iloc[-1])


def _combined_data_quality(*frames: pd.DataFrame) -> DataQualityDiagnostic | None:
    diagnostics = [frame.attrs.get("data_quality") for frame in frames if frame.attrs.get("data_quality") is not None]
    typed = [item for item in diagnostics if isinstance(item, DataQualityDiagnostic)]
    if not typed:
        return None
    warnings: list[str] = []
    for diagnostic in typed:
        warnings.extend(diagnostic.warnings)
    worst = min(typed, key=lambda item: item.score)
    return DataQualityDiagnostic(
        score=worst.score,
        missing_bars=sum(item.missing_bars for item in typed),
        stale_minutes=max((item.stale_minutes or 0.0) for item in typed),
        spread_available=all(item.spread_available for item in typed),
        resampled=any(item.resampled for item in typed),
        duplicate_bars=sum(item.duplicate_bars for item in typed),
        warnings=list(dict.fromkeys(warnings)),
    )


def _candidate_status(
    watchlist_candidate: bool,
    risk_plan: RiskPlan | None,
    final_score: float,
    technical_score: float,
    execution_score: float,
    context_score: float,
    empirical_score: float,
    minimum_score: float,
    approval: ApprovalSettings | None = None,
    data_quality_score: float | None = None,
    activation_quality: float | None = None,
    invalidation_quality: float | None = None,
) -> OpportunityStatus:
    approval = approval or ApprovalSettings()
    data_quality_score = 100.0 if data_quality_score is None else data_quality_score
    activation_quality = 100.0 if activation_quality is None else activation_quality
    invalidation_quality = 100.0 if invalidation_quality is None else invalidation_quality
    if risk_plan is not None and final_score >= minimum_score and not watchlist_candidate:
        if (
            execution_score < approval.minimum_execution_score
            or context_score < approval.minimum_context_score
            or empirical_score < approval.minimum_empirical_score
            or data_quality_score < approval.minimum_data_quality_score
            or activation_quality < approval.minimum_activation_quality
            or invalidation_quality < approval.minimum_invalidation_quality
        ):
            if technical_score >= 55.0:
                return OpportunityStatus.WATCHLIST
            return OpportunityStatus.DETECTED
        if (
            final_score >= approval.premium_final_score
            and technical_score >= approval.premium_technical_score
            and execution_score >= approval.premium_execution_score
            and context_score >= approval.premium_context_score
            and empirical_score >= approval.premium_empirical_score
            and data_quality_score >= approval.premium_data_quality_score
            and activation_quality >= approval.premium_activation_quality
            and invalidation_quality >= approval.premium_invalidation_quality
        ):
            return OpportunityStatus.PREMIUM
        return OpportunityStatus.APPROVED
    if watchlist_candidate or (technical_score >= 55.0 and final_score >= minimum_score - 12.0):
        return OpportunityStatus.WATCHLIST
    if technical_score >= 42.0:
        return OpportunityStatus.DETECTED
    return OpportunityStatus.REJECTED


def _activation_gaps(
    missing_conditions: list[str],
    gates: GateBreakdown,
    risk_rejection: str | None,
    final_score: float,
    minimum_score: float,
    approval_gaps: list[str] | None = None,
) -> list[str]:
    gaps = list(missing_conditions)
    gaps.extend(failed_gate_names(gates))
    if risk_rejection:
        gaps.append(risk_rejection)
    if final_score < minimum_score:
        gaps.append(f"final score needs {minimum_score:.1f}; current {final_score:.1f}")
    gaps.extend(approval_gaps or [])
    return list(dict.fromkeys(gaps))


def _approval_gaps(
    execution_score: float,
    context_score: float,
    empirical_score: float,
    data_quality_score: float | None,
    activation_quality: float | None,
    invalidation_quality: float | None,
    approval: ApprovalSettings,
) -> list[str]:
    """Return approval-layer gaps that are separate from raw setup gates."""

    quality = 100.0 if data_quality_score is None else data_quality_score
    activation = 100.0 if activation_quality is None else activation_quality
    invalidation = 100.0 if invalidation_quality is None else invalidation_quality
    checks = [
        ("execution score", execution_score, approval.minimum_execution_score),
        ("context score", context_score, approval.minimum_context_score),
        ("empirical score", empirical_score, approval.minimum_empirical_score),
        ("data quality", quality, approval.minimum_data_quality_score),
        ("activation quality", activation, approval.minimum_activation_quality),
        ("invalidation quality", invalidation, approval.minimum_invalidation_quality),
    ]
    return [
        f"{name} {value:.1f} below approval gate {minimum:.1f}"
        for name, value, minimum in checks
        if value < minimum
    ]


def _status_rank(status: OpportunityStatus) -> int:
    return {
        OpportunityStatus.PREMIUM: 4,
        OpportunityStatus.APPROVED: 3,
        OpportunityStatus.WATCHLIST: 2,
        OpportunityStatus.DETECTED: 1,
        OpportunityStatus.REJECTED: 0,
    }[status]


def _empirical_score(
    database: Database | None,
    symbol: str,
    style: TradingStyle,
    family: SetupFamily,
    subtype: SetupSubtype,
    session: SessionName,
    regime: MarketRegime,
    neutral: float,
    minimum_samples: int,
    min_condition_samples: int,
    shrinkage_samples: int,
    max_adjustment: float,
) -> float:
    if database is None:
        return neutral
    try:
        return database.lookup_empirical_score(
            symbol=symbol,
            style=style.value,
            family=family,
            subtype=subtype,
            session=session,
            regime=regime,
            minimum_samples=minimum_samples,
            neutral_score=neutral,
            min_condition_samples=min_condition_samples,
            shrinkage_samples=shrinkage_samples,
            max_adjustment=max_adjustment,
        )
    except Exception:
        LOGGER.exception("empirical score lookup failed", extra={"symbol": symbol, "family": family.value, "subtype": subtype.value})
        return neutral


def _opportunity_from_candidate(
    timestamp: datetime,
    symbol: str,
    style: TradingStyle,
    setup_family: SetupFamily,
    setup_subtype: SetupSubtype,
    regime: MarketRegime,
    direction: DirectionBias,
    score: float,
    confidence: ConfidenceBucket,
    risk_plan: RiskPlan | None,
    explanation: str,
    timeframe_higher: Timeframe,
    timeframe_entry: Timeframe,
    timeframe_trigger: Timeframe,
    score_components: dict[str, float],
    provider: str,
    data_warning: str | None,
    approved: bool,
    status: OpportunityStatus,
    raw_setup_family: SetupFamily | None,
    pre_gate_score: float | None,
    technical_score: float | None,
    execution_score: float | None,
    context_score: float | None,
    empirical_score: float | None,
    final_score: float | None,
    grade: SetupGrade | None,
    gate_breakdown: GateBreakdown | None,
    rejection_reason: str | None,
    rejection_category: RejectionCategory | None,
    required_min_rr: float,
    missing_conditions: list[str],
    invalidation: str | None,
    activation_quality: float | None,
    invalidation_quality: float | None,
    spread: float | None,
    atr: float | None,
    key_level_distances: dict[str, float],
    session: SessionName | None,
    htf_regime: MarketRegime,
    entry_regime: MarketRegime,
    trigger_regime: MarketRegime,
    data_quality: DataQualityDiagnostic | None,
    detected_patterns: list[str] | None = None,
    pattern_score: float = 0.0,
    pattern_explanations: list[str] | None = None,
    failed_gates: list[str] | None = None,
) -> Opportunity:
    gates = gate_breakdown
    derived_failed_gates = failed_gates
    if derived_failed_gates is None and gates is not None:
        derived_failed_gates = failed_gate_names(gates)
    return Opportunity(
        timestamp=timestamp,
        symbol=symbol,
        style=style,
        setup_family=setup_family,
        setup_subtype=setup_subtype,
        regime=regime,
        direction=direction,
        score=score,
        confidence=confidence,
        entry=risk_plan.entry if risk_plan is not None else None,
        stop_loss=risk_plan.stop_loss if risk_plan is not None else None,
        take_profit=risk_plan.take_profit if risk_plan is not None else None,
        risk_reward=round(risk_plan.risk_reward, 2) if risk_plan is not None else None,
        explanation=explanation,
        timeframe_higher=timeframe_higher,
        timeframe_entry=timeframe_entry,
        timeframe_trigger=timeframe_trigger,
        score_components={key: round(value, 2) for key, value in score_components.items()},
        provider=provider,
        data_warning=data_warning,
        rejection_reason=rejection_reason,
        approved=approved,
        status=status,
        raw_setup_family=raw_setup_family,
        pre_gate_score=pre_gate_score,
        technical_score=technical_score,
        execution_score=execution_score,
        context_score=context_score,
        empirical_score=empirical_score,
        final_score=final_score,
        grade=grade,
        gate_breakdown=gates,
        failed_gates=derived_failed_gates or [],
        rejection_category=rejection_category,
        required_min_rr=required_min_rr,
        missing_conditions=missing_conditions,
        invalidation=invalidation,
        tp1=risk_plan.tp1 if risk_plan is not None else None,
        tp2=risk_plan.tp2 if risk_plan is not None else None,
        tp3=risk_plan.tp3 if risk_plan is not None else None,
        activation_quality=activation_quality,
        invalidation_quality=invalidation_quality,
        spread=spread,
        atr=atr,
        key_level_distances=key_level_distances,
        detected_patterns=detected_patterns or [],
        pattern_score=pattern_score,
        pattern_explanations=pattern_explanations or [],
        session=session,
        htf_regime=htf_regime,
        entry_regime=entry_regime,
        trigger_regime=trigger_regime,
        data_quality=data_quality,
    )
