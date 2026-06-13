"""Phase 1 decision explainability tests (issue #118).

Covers decision traces, the GateResult model, score decomposition exports, and
the min-score policy report. All assertions stay paper/demo only and verify the
safety invariants (no secrets, no ``.env`` values, no ``order_send``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import app.execution.demo_bot as demo_bot_module
from app.config.settings import load_settings
from app.core.types import (
    ConfidenceBucket,
    DataQualityDiagnostic,
    DirectionBias,
    MarketRegime,
    Opportunity,
    OpportunityStatus,
    ScanReport,
    SessionName,
    SetupFamily,
    SetupSubtype,
    Timeframe,
    TradingStyle,
)
from app.execution.demo_bot import DemoBotService
from app.reporting.decision_trace import (
    GateResult,
    build_decision_trace,
    build_min_score_policy_report,
    build_score_decomposition,
    export_score_decomposition,
)
from app.storage.database import Database


class FakeScannerService:
    opportunities: list[Opportunity] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def scan(self, style: TradingStyle, symbols: list[str], timestamp: datetime | None = None) -> ScanReport:
        scan_time = timestamp or datetime.now(timezone.utc)
        opportunities = [
            opportunity.model_copy(update={"timestamp": scan_time, "style": style})
            for opportunity in self.opportunities
            if opportunity.symbol in symbols
        ]
        return ScanReport(timestamp=scan_time, style=style, opportunities=opportunities)


def _opportunity(
    *,
    status: OpportunityStatus = OpportunityStatus.APPROVED,
    score: float = 82.0,
    symbol: str = "EUR/USD",
    **updates: object,
) -> Opportunity:
    approved = status in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM}
    payload: dict[str, object] = {
        "timestamp": datetime.now(timezone.utc),
        "symbol": symbol,
        "style": TradingStyle.DAY_TRADING,
        "setup_family": SetupFamily.TREND_CONTINUATION,
        "setup_subtype": SetupSubtype.SHALLOW_EMA20_PULLBACK,
        "regime": MarketRegime.TRENDING_UP,
        "direction": DirectionBias.LONG,
        "score": score,
        "confidence": ConfidenceBucket.HIGH,
        "entry": 1.1000,
        "stop_loss": 1.0950,
        "take_profit": 1.1100,
        "risk_reward": 2.0,
        "explanation": "Approved demo setup for paper execution.",
        "timeframe_higher": Timeframe.H1,
        "timeframe_entry": Timeframe.M15,
        "timeframe_trigger": Timeframe.M5,
        "score_components": {"trend_clarity": score, "structure_quality": score - 5.0},
        "provider": "synthetic",
        "approved": approved,
        "status": status,
        "raw_setup_family": SetupFamily.TREND_CONTINUATION,
        "pre_gate_score": score,
        "technical_score": score,
        "execution_score": 67.0,
        "context_score": 60.0,
        "empirical_score": 55.0,
        "final_score": score,
        "required_min_rr": 1.5,
        "tp1": 1.1050,
        "tp2": 1.1100,
        "tp3": 1.1150,
        "activation_quality": 85.0,
        "invalidation_quality": 85.0,
        "spread": 0.00005,
        "atr": 0.001,
        "session": SessionName.LONDON,
        "htf_regime": MarketRegime.TRENDING_UP,
        "entry_regime": MarketRegime.TRENDING_UP,
        "trigger_regime": MarketRegime.TRENDING_UP,
        "data_quality": DataQualityDiagnostic(
            score=95.0,
            missing_bars=0,
            stale_minutes=0.0,
            spread_available=True,
            resampled=False,
        ),
    }
    payload.update(updates)
    return Opportunity(**payload)


@pytest.fixture
def settings():
    return load_settings()


@pytest.fixture
def database(tmp_path) -> Database:
    return Database(tmp_path / "explainability.sqlite")


# --------------------------------------------------------------------------- #
# Task B — traces integrated into demo bot cycle
# --------------------------------------------------------------------------- #
def test_demo_bot_emits_trace_for_accepted_opportunity(settings, database, monkeypatch):
    FakeScannerService.opportunities = [_opportunity(symbol="EUR/USD")]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)

    result = DemoBotService(settings, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD"])

    assert result.orders_created == 1
    assert len(result.decision_traces) == 1
    trace = result.decision_traces[0]
    assert trace.symbol == "EUR/USD"
    assert trace.accepted is True
    assert trace.order_ids == result.decisions[0].order_ids
    assert trace.order_ids  # accepted signal records the created paper order id
    assert trace.cycle_id == result.cycle_id
    assert trace.primary_rejection_reason is None
    assert any(gate.name == "final score" for gate in trace.gate_results)


def test_demo_bot_emits_trace_for_rejected_opportunity(settings, database, monkeypatch):
    FakeScannerService.opportunities = [_opportunity(symbol="EUR/USD", status=OpportunityStatus.WATCHLIST, score=92.0)]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)

    result = DemoBotService(settings, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD"])

    assert result.orders_created == 0
    assert len(result.decision_traces) == 1
    trace = result.decision_traces[0]
    assert trace.accepted is False
    assert trace.order_ids == []
    assert trace.rejection_reasons
    assert trace.primary_rejection_reason == trace.rejection_reasons[0]
    status_gate = next(gate for gate in trace.gate_results if gate.name == "status allowed")
    assert status_gate.passed is False


def test_traces_do_not_change_demo_bot_behavior(settings, database, monkeypatch):
    """Generating traces must never create extra paper orders."""

    FakeScannerService.opportunities = [_opportunity(symbol="EUR/USD")]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)

    result = DemoBotService(settings, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD"])

    assert result.orders_created == len([d for d in result.decisions if d.accepted])
    assert len(database.load_paper_orders()) == result.orders_created
    # One trace per scanned opportunity, never more orders than accepted decisions.
    assert len(result.decision_traces) == len(result.decisions)


# --------------------------------------------------------------------------- #
# Safety invariants — no secrets, no raw .env values
# --------------------------------------------------------------------------- #
def test_trace_contains_no_secret_or_env_values(settings, monkeypatch):
    monkeypatch.setenv("MT5_PASSWORD", "super-secret-value")
    monkeypatch.setenv("MT5_LOGIN", "1234567")
    trace = build_decision_trace(_opportunity(), settings)
    blob = json.dumps(trace.model_dump(mode="json")).lower()

    assert "super-secret-value" not in blob
    assert "1234567" not in blob
    assert ".env" not in blob
    assert "password" not in blob
    assert trace.safety_flags["live_trading"] is False
    assert trace.safety_flags["order_send_called"] is False


# --------------------------------------------------------------------------- #
# Task F — gate margins computed correctly
# --------------------------------------------------------------------------- #
def test_gate_margins_are_computed_correctly(settings):
    opportunity = _opportunity(final_score=70.0, score=70.0, execution_score=67.0)
    trace = build_decision_trace(opportunity, settings)
    gates = {gate.name: gate for gate in trace.gate_results}

    # final score 70 vs active min 75 -> margin -5, failed.
    final_gate = gates["final score"]
    assert final_gate.passed is False
    assert final_gate.margin == pytest.approx(round(70.0 - trace.active_min_score, 4))

    # execution score 67 vs approval minimum 54 -> margin +13, passed.
    execution_gate = gates["execution score"]
    assert execution_gate.passed is True
    assert execution_gate.margin == pytest.approx(round(67.0 - settings.approval.minimum_execution_score, 4))

    # spread/ATR is a max gate: margin = maximum - value, and stays below the cap.
    spread_gate = gates["spread/ATR"]
    assert spread_gate.maximum is not None
    assert spread_gate.passed is True
    assert spread_gate.margin == pytest.approx(round(spread_gate.maximum - (0.00005 / 0.001), 4))

    # All required gates from Task F are represented.
    required = {
        "final score",
        "risk/reward",
        "execution score",
        "context score",
        "empirical score",
        "data quality",
        "activation quality",
        "invalidation quality",
        "spread/ATR",
        "demo bot min score",
        "status allowed",
        "executable levels present",
        "direction executable",
    }
    assert required.issubset(set(gates))
    assert all(isinstance(gate, GateResult) for gate in trace.gate_results)
    assert {gate.layer for gate in trace.gate_results} >= {"score", "risk", "execution", "context", "empirical", "data", "market", "bot"}


# --------------------------------------------------------------------------- #
# Task D — score decomposition exports
# --------------------------------------------------------------------------- #
def test_score_decomposition_exports_json_and_txt(settings, tmp_path):
    report = build_score_decomposition(_opportunity(), settings, provider="synthetic")
    json_path, txt_path = export_score_decomposition([report], tmp_path)

    assert json_path.exists() and txt_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload
    entry = payload[0]
    for field in (
        "technical_score",
        "execution_score",
        "context_score",
        "empirical_score",
        "pattern_score",
        "final_score",
        "score_components",
        "technical_weights",
        "layer_weights",
        "active_min_score",
        "min_score_source",
        "status",
        "approval_gate_results",
        "bot_gate_results",
        "rejection_reasons",
    ):
        assert field in entry
    text = txt_path.read_text(encoding="utf-8")
    assert "Score Decomposition Report" in text
    assert "layer_weights" in text


# --------------------------------------------------------------------------- #
# Task E — min-score policy report
# --------------------------------------------------------------------------- #
def test_min_score_policy_report_adaptive_disabled(settings):
    settings.adaptive_thresholds.enabled = False
    policy = build_min_score_policy_report("EUR/USD", TradingStyle.DAY_TRADING, settings)

    assert policy.adaptive_enabled is False
    assert policy.instrument_min_score == 75.0
    assert policy.effective_scanner_threshold == 75.0
    assert policy.threshold_source == "instrument/static"
    assert policy.adaptive_effective_min_score == 75.0


def test_min_score_policy_report_adaptive_report_only(settings):
    settings.adaptive_thresholds.enabled = True
    settings.adaptive_thresholds.mode = "report_only"
    policy = build_min_score_policy_report("EUR/USD", TradingStyle.DAY_TRADING, settings)

    assert policy.adaptive_enabled is True
    assert policy.adaptive_mode == "report_only"
    # report_only must NOT relax the scanner threshold below the instrument baseline.
    assert policy.effective_scanner_threshold == policy.instrument_min_score
    assert policy.threshold_source == "instrument/static"


def test_min_score_policy_report_flags_demo_bot_mismatch(settings, monkeypatch):
    monkeypatch.delenv("AUTO_BOT_MIN_SCORE", raising=False)
    settings.demo_bot.min_score = 80.0
    policy = build_min_score_policy_report("EUR/USD", TradingStyle.DAY_TRADING, settings)

    assert policy.demo_bot_min_score == 80.0
    assert policy.effective_scanner_threshold == 75.0
    assert policy.mismatch_warnings


# --------------------------------------------------------------------------- #
# Safety invariant — no order_send in realtime paper supervisor paths
# --------------------------------------------------------------------------- #
def test_no_order_send_in_realtime_paper_supervisor_paths():
    module_path = Path(__file__).resolve().parents[1] / "app" / "execution" / "realtime_paper_supervisor.py"
    source = module_path.read_text(encoding="utf-8")
    assert "order_send(" not in source
