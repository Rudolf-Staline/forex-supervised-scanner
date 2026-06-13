from __future__ import annotations

from datetime import datetime, timezone

from app.config.settings import load_settings
from app.core.pipeline import ScannerService
from app.core.types import TradingStyle
from app.data.providers import SyntheticForexDataProvider
from app.reporting.decision_trace import (
    build_decision_trace,
    build_min_score_policy,
    export_decision_traces,
    export_min_score_policy_report,
)
from app.storage.database import Database


def _first_opportunity(tmp_path):
    settings = load_settings().model_copy(deep=True)
    settings.provider.name = "synthetic"
    settings.provider.max_bars = 260
    settings.styles[TradingStyle.DAY_TRADING].lookback_bars = 220
    database = Database(tmp_path / "trace.sqlite")
    provider = SyntheticForexDataProvider(settings.provider)
    report = ScannerService(settings, provider, database).scan(
        TradingStyle.DAY_TRADING,
        ["EUR/USD"],
        timestamp=datetime(2025, 1, 15, 14, tzinfo=timezone.utc),
    )
    assert report.opportunities
    return settings, report.opportunities[0]


def test_decision_trace_contains_required_scores_and_gate_margins(tmp_path):
    settings, opportunity = _first_opportunity(tmp_path)
    trace = build_decision_trace(opportunity, settings)

    assert trace.symbol == "EUR/USD"
    assert trace.raw_setup["family"]
    assert trace.risk_plan["required_min_rr"] is not None
    assert trace.final_score == opportunity.final_score
    assert trace.technical_score == opportunity.technical_score
    assert trace.execution_score == opportunity.execution_score
    assert trace.context_score == opportunity.context_score
    assert trace.empirical_score == opportunity.empirical_score
    assert "trend_clarity" in trace.score_components or opportunity.score_components == {}
    assert trace.active_min_score >= 0
    assert trace.paper_order_preflight_result["status"] == "not_run"
    gate_names = {gate.name for gate in trace.gate_margin_report}
    for expected in {"final score", "risk/reward", "execution score", "context score", "empirical score", "data quality", "activation quality", "invalidation quality", "spread/ATR", "session", "market regime", "operator controls", "daily risk"}:
        assert expected in gate_names


def test_min_score_policy_reports_mismatch_without_mutating_thresholds(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTO_BOT_MIN_SCORE", raising=False)
    settings, opportunity = _first_opportunity(tmp_path)
    settings.demo_bot.min_score = 80.0
    policy = build_min_score_policy(opportunity, settings)

    assert policy.instrument_min_score == 75.0
    assert policy.demo_bot_min_score == 80.0
    assert policy.effective_scanner_threshold == 75.0
    assert policy.mismatch_warnings


def test_decision_trace_exports_json_and_txt_without_secret_keys(tmp_path):
    settings, opportunity = _first_opportunity(tmp_path)
    trace = build_decision_trace(opportunity, settings)
    json_path, txt_path = export_decision_traces([trace], tmp_path)
    policy_json, policy_txt = export_min_score_policy_report([trace], tmp_path)

    for path in [json_path, txt_path, policy_json, policy_txt]:
        assert path.exists()
        text = path.read_text(encoding="utf-8").lower()
        assert "password" not in text
        assert ".env" not in text
