"""Custom event-style backtester for the rules-based scanner."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import pandas as pd

from app.backtest.metrics import calculate_metrics
from app.backtest.outcomes import evaluate_path
from app.config.settings import AppSettings
from app.core.types import BacktestResult, DirectionBias, MarketRegime, RiskPlan, SessionName, SetupFamily, SetupSubtype, TIMEFRAME_MINUTES, Timeframe, TradeRecord, TradingStyle
from app.data.providers import MarketDataProvider
from app.data.validation import pips_to_price, window_for_bars
from app.indicators.calculations import add_indicators
from app.indicators.levels import find_key_levels
from app.market_regime.regime import MarketRegimeDetector
from app.risk.engine import RiskEngine
from app.scoring.engine import ScoringEngine
from app.setups.detector import detect_setups
from app.storage.database import Database

LOGGER = logging.getLogger(__name__)


class Backtester:
    """Run historical tests using the same setup, risk, and scoring engines as the scanner."""

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

    def run(
        self,
        symbols: list[str],
        style: TradingStyle,
        setup_filter: SetupFamily | Literal["all"],
        start: datetime,
        end: datetime,
    ) -> BacktestResult:
        """Run a backtest for selected symbols, style, setup filter, and date range."""

        all_trades: list[TradeRecord] = []
        limitations = [
            "Signals are evaluated at completed candle closes and are not intrabar forecasts.",
            "If a candle touches both SL and TP, the backtester assumes the stop loss was hit first.",
            "Transaction cost is modeled as a fixed round-trip pip cost from settings.",
        ]
        for symbol in symbols:
            try:
                all_trades.extend(self._run_symbol(symbol, style, setup_filter, start, end))
            except Exception as exc:
                LOGGER.exception("backtest symbol failed", extra={"symbol": symbol, "style": style.value})
                limitations.append(f"{symbol}: skipped because {exc}")

        all_trades.sort(key=lambda trade: trade.exit_time)
        equity_curve: list[tuple[datetime, float]] = [(start, 0.0)]
        cumulative = 0.0
        for trade in all_trades:
            cumulative += trade.net_r
            equity_curve.append((trade.exit_time, round(cumulative, 4)))

        result = BacktestResult(
            run_id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc),
            symbols=symbols,
            style=style,
            setup_filter=setup_filter,
            start=start,
            end=end,
            metrics=calculate_metrics(all_trades),
            trades=all_trades,
            equity_curve=equity_curve,
            limitations=limitations,
        )
        if self.database is not None:
            self.database.save_backtest_result(result)
        return result

    def _run_symbol(
        self,
        symbol: str,
        style: TradingStyle,
        setup_filter: SetupFamily | Literal["all"],
        start: datetime,
        end: datetime,
    ) -> list[TradeRecord]:
        style_settings = self.settings.styles[style]
        higher_tf = style_settings.higher_timeframe
        entry_tf = style_settings.entry_timeframe
        trigger_tf = style_settings.trigger_timeframe
        warmup_start = start - timedelta(minutes=TIMEFRAME_MINUTES[higher_tf] * style_settings.lookback_bars)
        higher = self._fetch(symbol, higher_tf, warmup_start, end)
        entry = self._fetch(symbol, entry_tf, warmup_start, end)
        trigger = self._fetch(symbol, trigger_tf, warmup_start, end)

        evaluation_times = [timestamp for timestamp in entry.loc[start:end].index if timestamp in entry.index]
        trades: list[TradeRecord] = []
        blocked_until = start
        for timestamp in evaluation_times:
            if timestamp <= blocked_until:
                continue
            higher_slice = higher.loc[:timestamp].tail(style_settings.lookback_bars)
            entry_slice = entry.loc[:timestamp].tail(style_settings.lookback_bars)
            trigger_slice = trigger.loc[:timestamp].tail(style_settings.lookback_bars)
            if min(len(higher_slice), len(entry_slice), len(trigger_slice)) < 220:
                continue

            higher_regime = self.regime_detector.analyze(higher_slice)
            entry_regime = self.regime_detector.analyze(entry_slice)
            trigger_regime = self.regime_detector.analyze(trigger_slice)
            levels = find_key_levels(entry_slice, tolerance_atr=self.settings.setups.level_tolerance_atr)
            raw_setups = detect_setups(
                symbol=symbol,
                style=style,
                higher_df=higher_slice,
                entry_df=entry_slice,
                trigger_df=trigger_slice,
                higher_regime=higher_regime,
                entry_regime=entry_regime,
                trigger_regime=trigger_regime,
                levels=levels,
                settings=self.settings,
            )
            if setup_filter != "all":
                raw_setups = [setup for setup in raw_setups if setup.family == setup_filter]

            scored = []
            spread = _latest_spread(entry_slice)
            for setup in raw_setups:
                risk_decision = self.risk_engine.plan(setup, style)
                if risk_decision.plan is None:
                    continue
                score_result = self.scoring_engine.score_detailed(
                    setup,
                    risk_decision.plan,
                    spread,
                    data_quality=entry_slice.attrs.get("data_quality"),
                    timestamp=timestamp.to_pydatetime() if hasattr(timestamp, "to_pydatetime") else timestamp,
                )
                if score_result.final_score >= self.scoring_engine.minimum_score(setup.family):
                    scored.append((score_result.final_score, setup, risk_decision.plan, score_result))
            if not scored:
                continue

            _score, setup, risk_plan, score_result = max(scored, key=lambda item: item[0])
            future = trigger.loc[trigger.index > timestamp]
            max_hold = style_settings.max_hold_bars * max(1, TIMEFRAME_MINUTES[entry_tf] // TIMEFRAME_MINUTES[trigger_tf])
            trade = _simulate_trade(
                symbol=symbol,
                style=style,
                family=setup.family,
                subtype=setup.subtype,
                direction=setup.direction,
                entry_time=timestamp,
                risk_plan=risk_plan,
                future=future.head(max_hold),
                cost_pips=style_settings.transaction_cost_pips,
                session=score_result.session,
                regime=setup.regime,
                technical_score=score_result.technical_score,
                execution_score=score_result.execution_score,
                context_score=score_result.context_score,
                empirical_score=score_result.empirical_score,
                final_score=score_result.final_score,
            )
            trades.append(trade)
            blocked_until = trade.exit_time
        return trades

    def _fetch(self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime) -> pd.DataFrame:
        fallback_window = window_for_bars(timeframe, self.settings.provider.max_bars, end)
        request_start = min(start, fallback_window.start)
        raw = self.provider.get_ohlcv(symbol, timeframe, request_start, end)
        enriched = add_indicators(raw)
        enriched.attrs.update(raw.attrs)
        return enriched


def _simulate_trade(
    symbol: str,
    style: TradingStyle,
    family: SetupFamily,
    subtype: SetupSubtype,
    direction: DirectionBias,
    entry_time: pd.Timestamp,
    risk_plan: RiskPlan,
    future: pd.DataFrame,
    cost_pips: float,
    session: SessionName,
    regime: MarketRegime,
    technical_score: float,
    execution_score: float,
    context_score: float,
    empirical_score: float,
    final_score: float,
) -> TradeRecord:
    exit_reason: Literal["take_profit", "stop_loss", "time_exit", "end_of_data"] = "end_of_data"
    exit_price = float(risk_plan.entry)
    exit_time = entry_time.to_pydatetime() if hasattr(entry_time, "to_pydatetime") else entry_time
    exit_bar_count = 0

    for bar_number, (timestamp, row) in enumerate(future.iterrows(), start=1):
        high = float(row["high"])
        low = float(row["low"])
        if direction == DirectionBias.LONG:
            stop_hit = low <= risk_plan.stop_loss
            target_hit = high >= risk_plan.take_profit
        else:
            stop_hit = high >= risk_plan.stop_loss
            target_hit = low <= risk_plan.take_profit
        if stop_hit:
            exit_reason = "stop_loss"
            exit_price = float(risk_plan.stop_loss)
            exit_time = timestamp.to_pydatetime()
            exit_bar_count = bar_number
            break
        if target_hit:
            exit_reason = "take_profit"
            exit_price = float(risk_plan.take_profit)
            exit_time = timestamp.to_pydatetime()
            exit_bar_count = bar_number
            break
    else:
        if not future.empty:
            last = future.iloc[-1]
            exit_price = float(last["close"])
            exit_time = future.index[-1].to_pydatetime()
            exit_reason = "time_exit"
            exit_bar_count = len(future)

    risk_distance = abs(float(risk_plan.entry) - float(risk_plan.stop_loss))
    if direction == DirectionBias.LONG:
        gross_profit = exit_price - float(risk_plan.entry)
    else:
        gross_profit = float(risk_plan.entry) - exit_price
    cost_price = pips_to_price(symbol, cost_pips)
    gross_r = gross_profit / max(risk_distance, 1e-12)
    net_r = (gross_profit - cost_price) / max(risk_distance, 1e-12)
    path_future = future.head(exit_bar_count) if exit_bar_count else future
    path = evaluate_path(direction, risk_plan, path_future, exit_reason, net_r)

    return TradeRecord(
        symbol=symbol,
        style=style,
        setup_family=family,
        setup_subtype=subtype,
        direction=direction,
        entry_time=entry_time.to_pydatetime() if hasattr(entry_time, "to_pydatetime") else entry_time,
        exit_time=exit_time,
        entry=float(risk_plan.entry),
        stop_loss=float(risk_plan.stop_loss),
        take_profit=float(risk_plan.take_profit),
        exit_price=exit_price,
        gross_r=round(gross_r, 4),
        net_r=round(net_r, 4),
        exit_reason=exit_reason,
        cost_pips=cost_pips,
        session=session,
        regime=regime,
        technical_score=technical_score,
        execution_score=execution_score,
        context_score=context_score,
        empirical_score=empirical_score,
        final_score=final_score,
        outcome=path.outcome,
        tp1_hit=path.tp1_hit,
        tp2_hit=path.tp2_hit,
        tp3_hit=path.tp3_hit,
        mae=path.mae,
        mfe=path.mfe,
        bars_to_activation=path.bars_to_activation,
        bars_to_invalidation=path.bars_to_invalidation,
        bars_to_tp1=path.bars_to_tp1,
        bars_to_tp2=path.bars_to_tp2,
        bars_to_tp3=path.bars_to_tp3,
    )


def _latest_spread(frame: pd.DataFrame) -> float | None:
    if "spread" not in frame:
        return None
    series = frame["spread"].dropna()
    if series.empty:
        return None
    return float(series.iloc[-1])
