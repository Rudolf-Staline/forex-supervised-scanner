"""Shared typed domain objects for the scanner and backtester."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TradingStyle(str, Enum):
    """Supported trading horizons."""

    SCALPING = "scalping"
    DAY_TRADING = "day_trading"
    SWING_TRADING = "swing_trading"


class Timeframe(str, Enum):
    """Canonical timeframe identifiers used by the app."""

    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"


TIMEFRAME_MINUTES: dict[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
    Timeframe.D1: 1440,
}

TIMEFRAME_PANDAS_RULE: dict[Timeframe, str] = {
    Timeframe.M1: "1min",
    Timeframe.M5: "5min",
    Timeframe.M15: "15min",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1D",
}


class DirectionBias(str, Enum):
    """Directional action suggested by a rules-based setup."""

    LONG = "long"
    SHORT = "short"
    NO_TRADE = "no-trade"


class MarketRegime(str, Enum):
    """Supported market regime classifications."""

    TRENDING_UP = "trending up"
    TRENDING_DOWN = "trending down"
    WEAK_TREND_UP = "weak trend up"
    WEAK_TREND_DOWN = "weak trend down"
    TRANSITION = "transition"
    RANGING = "ranging"
    BREAKOUT_CANDIDATE = "breakout candidate"
    HIGH_VOLATILITY = "high volatility / unstable"
    NO_TRADE = "no-trade"


class VolatilityRegime(str, Enum):
    """Volatility state inferred from ATR percentage and Bollinger Band width."""

    COMPRESSED = "compressed"
    NORMAL = "normal"
    ELEVATED = "elevated"
    HIGH_VOLATILITY = "high volatility / unstable"
    UNKNOWN = "unknown"


class SetupFamily(str, Enum):
    """Rules-based setup families available in V1."""

    TREND_CONTINUATION = "trend_continuation"
    BREAKOUT_CONFIRMATION = "breakout_confirmation"
    MEAN_REVERSION = "mean_reversion"
    NO_TRADE = "no_trade"


class SetupSubtype(str, Enum):
    """More precise setup archetypes used for ranking and calibration."""

    SHALLOW_EMA20_PULLBACK = "shallow_ema20_pullback"
    EMA50_PULLBACK = "ema50_pullback"
    RETEST_CONTINUATION = "retest_continuation"
    BREAKOUT_CLOSE = "breakout_close"
    BREAKOUT_RETEST = "breakout_retest"
    SQUEEZE_BREAKOUT = "squeeze_breakout"
    MOMENTUM_BREAKOUT = "momentum_breakout"
    RANGE_EDGE_REVERSAL = "range_edge_reversal"
    VOLATILITY_SPIKE_FADE = "volatility_spike_fade"
    BOLLINGER_SNAPBACK = "bollinger_snapback"
    NONE = "none"


class ConfidenceBucket(str, Enum):
    """Human-readable confidence bucket derived from the numeric score."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OpportunityStatus(str, Enum):
    """Lifecycle status for a detected technical setup."""

    PREMIUM = "premium"
    APPROVED = "approved"
    WATCHLIST = "watchlist"
    DETECTED = "detected"
    REJECTED = "rejected"


class SessionName(str, Enum):
    """Coarse FX session bucket for context scoring and analytics."""

    ASIA = "asia"
    LONDON = "london"
    NEW_YORK_OVERLAP = "new_york_overlap"
    NEW_YORK = "new_york"
    OFF_HOURS = "off_hours"


class TradeOutcomeLabel(str, Enum):
    """Richer realized outcome labels for calibration beyond binary win/loss."""

    WIN_CLEAN = "win_clean"
    WIN_MESSY = "win_messy"
    PARTIAL_WIN = "partial_win"
    BREAKEVEN = "breakeven"
    TIMEOUT = "timeout"
    LOSS_CLEAN = "loss_clean"
    LOSS_FAST = "loss_fast"


class SetupGrade(str, Enum):
    """Coarse product-facing classification from final and component scores."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"


class RejectionCategory(str, Enum):
    """Primary diagnostic reason a raw setup failed final approval."""

    NO_RAW_SETUP = "no raw setup"
    WEAK_STRUCTURE = "weak structure"
    INSUFFICIENT_RR = "insufficient RR"
    CONFLICTING_TIMEFRAMES = "conflicting timeframes"
    WEAK_MOMENTUM = "weak momentum"
    UNSUITABLE_VOLATILITY = "unsuitable volatility"
    WEAK_EXECUTION = "weak execution"
    WEAK_CONTEXT = "weak context"
    LOW_EMPIRICAL_SUPPORT = "low empirical support"
    POOR_DATA_QUALITY = "poor data quality"
    WEAK_ACTIVATION = "weak activation"
    WEAK_INVALIDATION = "weak invalidation"
    SCORE_TOO_LOW = "score below threshold"
    INVALID_RISK = "invalid risk"


class GateBreakdown(BaseModel):
    """Pass/fail diagnostics for final approval evidence."""

    trend: bool
    structure: bool
    momentum: bool
    volatility: bool
    multi_timeframe_alignment: bool
    minimum_rr: bool
    score_threshold: bool


class DataQualityDiagnostic(BaseModel):
    """Quality diagnostics attached to provider data used for a scan."""

    score: float = Field(ge=0.0, le=100.0)
    missing_bars: int = Field(ge=0)
    stale_minutes: float | None = Field(default=None, ge=0.0)
    spread_available: bool
    resampled: bool
    duplicate_bars: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


class PriceLevel(BaseModel):
    """A technical support or resistance level inferred from recent swings."""

    model_config = ConfigDict(frozen=True)

    price: float
    kind: Literal["support", "resistance"]
    strength: float = Field(ge=0.0, le=100.0)
    touches: int = Field(ge=1)
    label: str


class RegimeResult(BaseModel):
    """Result of market regime and structure classification."""

    regime: MarketRegime
    direction_bias: DirectionBias
    trend_clarity: float = Field(ge=0.0, le=100.0)
    structure_quality: float = Field(ge=0.0, le=100.0)
    volatility_score: float = Field(ge=0.0, le=100.0)
    momentum_score: float = Field(ge=0.0, le=100.0)
    explanation: str


class VolatilityResult(BaseModel):
    """Result of volatility-regime detection."""

    regime: VolatilityRegime
    suitability_score: float = Field(ge=0.0, le=100.0)
    percentile_rank: float = Field(ge=0.0, le=100.0)
    is_unstable: bool
    explanation: str


class RawSetup(BaseModel):
    """Setup emitted before final risk and score filtering."""

    symbol: str
    style: TradingStyle
    family: SetupFamily
    subtype: SetupSubtype = SetupSubtype.NONE
    regime: MarketRegime
    direction: DirectionBias
    entry: float
    stop_candidates: dict[str, float]
    target_candidates: dict[str, float]
    trend_clarity: float = Field(ge=0.0, le=100.0)
    structure_quality: float = Field(ge=0.0, le=100.0)
    mtf_alignment: float = Field(ge=0.0, le=100.0)
    volatility_suitability: float = Field(ge=0.0, le=100.0)
    momentum_confirmation: float = Field(ge=0.0, le=100.0)
    level_proximity: float = Field(ge=0.0, le=100.0)
    activation_quality: float = Field(default=60.0, ge=0.0, le=100.0)
    invalidation_quality: float = Field(default=60.0, ge=0.0, le=100.0)
    atr: float | None = Field(default=None, ge=0.0)
    key_level_distances: dict[str, float] = Field(default_factory=dict)
    explanation: str
    invalidation_notes: list[str] = Field(default_factory=list)
    missing_conditions: list[str] = Field(default_factory=list)
    watchlist_candidate: bool = False
    detected_patterns: list[str] = Field(default_factory=list)
    pattern_score: float = Field(default=0.0, ge=0.0, le=15.0)
    pattern_explanations: list[str] = Field(default_factory=list)


class RiskPlan(BaseModel):
    """Final trade management levels produced by the risk engine."""

    entry: float
    stop_loss: float
    take_profit: float
    tp1: float
    tp2: float
    tp3: float
    risk_reward: float
    tp1_risk_reward: float
    tp2_risk_reward: float
    tp3_risk_reward: float
    stop_method: str
    target_method: str
    target_profile: Literal["conservative", "balanced", "aggressive"] = "balanced"
    rejection_reason: str | None = None


class Opportunity(BaseModel):
    """A scanner output row, including diagnostics for rejected raw candidates."""

    timestamp: datetime
    symbol: str
    style: TradingStyle
    setup_family: SetupFamily
    setup_subtype: SetupSubtype = SetupSubtype.NONE
    regime: MarketRegime
    direction: DirectionBias
    score: float = Field(ge=0.0, le=100.0)
    confidence: ConfidenceBucket
    entry: float | None
    stop_loss: float | None
    take_profit: float | None
    risk_reward: float | None
    explanation: str
    timeframe_higher: Timeframe
    timeframe_entry: Timeframe
    timeframe_trigger: Timeframe
    score_components: dict[str, float] = Field(default_factory=dict)
    provider: str
    data_warning: str | None = None
    rejection_reason: str | None = None
    approved: bool = False
    status: OpportunityStatus = OpportunityStatus.REJECTED
    raw_setup_family: SetupFamily | None = None
    pre_gate_score: float | None = Field(default=None, ge=0.0, le=100.0)
    technical_score: float | None = Field(default=None, ge=0.0, le=100.0)
    execution_score: float | None = Field(default=None, ge=0.0, le=100.0)
    context_score: float | None = Field(default=None, ge=0.0, le=100.0)
    empirical_score: float | None = Field(default=None, ge=0.0, le=100.0)
    final_score: float | None = Field(default=None, ge=0.0, le=100.0)
    grade: SetupGrade | None = None
    gate_breakdown: GateBreakdown | None = None
    failed_gates: list[str] = Field(default_factory=list)
    rejection_category: RejectionCategory | None = None
    required_min_rr: float | None = Field(default=None, gt=0.0)
    missing_conditions: list[str] = Field(default_factory=list)
    invalidation: str | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None
    activation_quality: float | None = Field(default=None, ge=0.0, le=100.0)
    invalidation_quality: float | None = Field(default=None, ge=0.0, le=100.0)
    spread: float | None = Field(default=None, ge=0.0)
    atr: float | None = Field(default=None, ge=0.0)
    key_level_distances: dict[str, float] = Field(default_factory=dict)
    detected_patterns: list[str] = Field(default_factory=list)
    pattern_score: float = Field(default=0.0, ge=0.0, le=15.0)
    pattern_explanations: list[str] = Field(default_factory=list)
    session: SessionName | None = None
    htf_regime: MarketRegime | None = None
    entry_regime: MarketRegime | None = None
    trigger_regime: MarketRegime | None = None
    data_quality: DataQualityDiagnostic | None = None

    adaptive_threshold_enabled: bool = False
    base_min_score: float | None = None
    adaptive_min_score: float | None = None
    effective_min_score: float | None = None
    adaptive_threshold_confidence: str | None = None
    adaptive_threshold_reason: str | None = None

    outcome: TradeOutcomeLabel | None = None
    tp1_hit: bool | None = None
    tp2_hit: bool | None = None
    tp3_hit: bool | None = None
    mae: float | None = None
    mfe: float | None = None
    bars_to_activation: int | None = Field(default=None, ge=0)
    bars_to_invalidation: int | None = Field(default=None, ge=0)
    bars_to_tp1: int | None = Field(default=None, ge=0)
    bars_to_tp2: int | None = Field(default=None, ge=0)
    bars_to_tp3: int | None = Field(default=None, ge=0)

    @field_validator("entry", "stop_loss", "take_profit", "risk_reward", "tp1", "tp2", "tp3")
    @classmethod
    def positive_optional_price(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("price and ratio fields must be positive when present")
        return value


class SymbolAnalysisError(BaseModel):
    """A recoverable per-symbol analysis failure shown in the UI."""

    symbol: str
    reason: str


class ScanReport(BaseModel):
    """Complete result of one scanner run."""

    timestamp: datetime
    style: TradingStyle
    opportunities: list[Opportunity]
    errors: list[SymbolAnalysisError] = Field(default_factory=list)


class TradeRecord(BaseModel):
    """One completed backtest trade."""

    symbol: str
    style: TradingStyle
    setup_family: SetupFamily
    setup_subtype: SetupSubtype = SetupSubtype.NONE
    direction: DirectionBias
    entry_time: datetime
    exit_time: datetime
    entry: float
    stop_loss: float
    take_profit: float
    exit_price: float
    gross_r: float
    net_r: float
    exit_reason: Literal["take_profit", "stop_loss", "time_exit", "end_of_data"]
    cost_pips: float
    session: SessionName | None = None
    regime: MarketRegime | None = None
    technical_score: float | None = Field(default=None, ge=0.0, le=100.0)
    execution_score: float | None = Field(default=None, ge=0.0, le=100.0)
    context_score: float | None = Field(default=None, ge=0.0, le=100.0)
    empirical_score: float | None = Field(default=None, ge=0.0, le=100.0)
    final_score: float | None = Field(default=None, ge=0.0, le=100.0)
    detected_patterns: list[str] = Field(default_factory=list)
    pattern_score: float = Field(default=0.0, ge=0.0, le=15.0)
    outcome: TradeOutcomeLabel | None = None
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    mae: float = 0.0
    mfe: float = 0.0
    bars_to_activation: int | None = Field(default=None, ge=0)
    bars_to_invalidation: int | None = Field(default=None, ge=0)
    bars_to_tp1: int | None = Field(default=None, ge=0)
    bars_to_tp2: int | None = Field(default=None, ge=0)
    bars_to_tp3: int | None = Field(default=None, ge=0)


class BacktestMetrics(BaseModel):
    """Summary metrics for a backtest run.

    ``max_drawdown`` and all ``*_r`` fields are expressed in R (risk) units.
    ``sharpe_like`` is **deprecated**: it multiplied the per-trade mean/std ratio
    by ``sqrt(number_of_trades)``, so it mechanically grew with sample size and
    is not comparable across runs. Prefer ``sharpe_per_trade`` (a true mean/std
    ratio) or ``sharpe_annualized`` (with the explicit assumption recorded in
    ``annualization_trades_per_year``).
    """

    win_rate: float
    average_win: float
    average_loss: float
    profit_factor: float
    max_drawdown: float
    expectancy: float
    number_of_trades: int
    sharpe_like: float
    # --- Sample-size-independent and distributional metrics (P2.1) ---
    sharpe_per_trade: float = 0.0
    sharpe_annualized: float = 0.0
    annualization_trades_per_year: float = 252.0
    expectancy_ci_low: float = 0.0
    expectancy_ci_high: float = 0.0
    median_r: float = 0.0
    r_percentile_10: float = 0.0
    r_percentile_25: float = 0.0
    r_percentile_75: float = 0.0
    r_percentile_90: float = 0.0
    max_drawdown_r: float = 0.0


class BacktestResult(BaseModel):
    """Backtest result payload persisted and shown in the UI."""

    run_id: str
    created_at: datetime
    symbols: list[str]
    style: TradingStyle
    setup_filter: SetupFamily | Literal["all"]
    start: datetime
    end: datetime
    metrics: BacktestMetrics
    trades: list[TradeRecord]
    equity_curve: list[tuple[datetime, float]]
    limitations: list[str]
