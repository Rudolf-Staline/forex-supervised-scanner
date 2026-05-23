"""Paper-only forward-test report tests."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import forward_test_paper as forward_test  # noqa: E402
from app.core.types import TradingStyle  # noqa: E402
from app.execution.demo_bot import DemoBotCycleResult, DemoBotDecision  # noqa: E402
from app.risk.daily_limits import DailyRiskSummary  # noqa: E402


def test_forward_near_miss_detects_scores_patterns_and_watchlist() -> None:
    assert forward_test.is_forward_near_miss({"score": "56", "pattern_score": "0", "status": "rejected", "setup": "none"})
    assert forward_test.is_forward_near_miss({"score": "12", "pattern_score": "3", "status": "rejected", "setup": "none"})
    assert forward_test.is_forward_near_miss({"score": "12", "pattern_score": "0", "status": "watchlist", "setup": "none"})
    assert not forward_test.is_forward_near_miss({"score": "12", "pattern_score": "0", "status": "rejected", "setup": "none"})


def test_forward_summary_counts_requested_fields() -> None:
    rows = [
        _row("EUR/USD", "forex", "london", score=72.0, status="watchlist", near_miss=True, reasons="spread high; score low"),
        _row("NAS100", "indices", "us_open", score=64.0, status="rejected", near_miss=True, reasons="scan_only"),
        _row("GBP/USD", "forex", "london", score=82.0, status="approved", near_miss=False, reasons=""),
    ]

    summary = forward_test.build_forward_summary(rows, total_cycles=2)

    assert summary["total_cycles"] == 2
    assert summary["total_signals"] == 3
    assert summary["approved_like_signals"] == 1
    assert summary["near_miss_signals"] == 2
    assert summary["best_asset_class"].startswith("forex")
    assert summary["safety_status"] == "demo_only=true live_trading_disabled=true broker=paper notifications_read_only=true no_trade_execution_command=true"
    assert summary["most_common_rejection_reasons"]["spread high"] == 1


def test_export_forward_reports(tmp_path: Path) -> None:
    rows = [_row("EUR/USD", "forex", "london", score=72.0, status="watchlist", near_miss=True, reasons="score low")]
    summary = forward_test.build_forward_summary(rows, total_cycles=1)

    csv_path = forward_test.export_forward_rows(rows, tmp_path / "forward.csv")
    json_path = forward_test.export_forward_summary(summary, tmp_path / "summary.json")

    assert csv_path.read_text(encoding="utf-8").startswith("timestamp,cycle_id")
    assert '"total_cycles": 1' in json_path.read_text(encoding="utf-8")


def test_run_forward_test_uses_paper_runtime_and_one_cycle(settings, monkeypatch, tmp_path: Path) -> None:
    calls: dict[str, object] = {}

    class FakeProvider:
        name = "mt5"

    class FakeDatabase:
        def load_paper_orders(self):
            return []

    class FakeService:
        def __init__(self, settings_arg, provider_arg, database_arg) -> None:
            calls["service_provider"] = provider_arg.name

        def run_cycle(self, style, symbols, watchlist=None):
            calls["cycle"] = {"style": style.value, "symbols": symbols, "watchlist": watchlist}
            return _cycle_result()

    def fake_load_runtime(context, *, provider_name, broker_mode, debug_market_data=False):
        calls["runtime"] = {"context": context, "provider": provider_name, "broker": broker_mode}
        return settings, FakeDatabase(), FakeProvider()

    monkeypatch.setattr(forward_test, "load_demo_runtime", fake_load_runtime)
    monkeypatch.setattr(forward_test, "filter_unhealthy_symbols_if_requested", lambda symbols, enabled, provider: symbols)
    monkeypatch.setattr(forward_test, "filter_tradable_session_symbols_if_requested", lambda symbols, enabled, broker_mode="paper": symbols[:1])
    monkeypatch.setattr(forward_test, "print_next_session_windows", lambda symbols: None)
    monkeypatch.setattr(forward_test, "DemoBotService", FakeService)
    monkeypatch.setattr(
        forward_test,
        "load_trade_journal",
        lambda: [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "cycle_id": "cycle-1",
                "asset_class": "forex",
                "logical_symbol": "EUR/USD",
                "mt5_symbol": "EURUSD",
                "provider": "mt5",
                "session_name": "london",
                "is_tradable_session": "true",
                "setup": "ema50_pullback",
                "status": "watchlist",
                "score": "66",
                "risk_reward": "1.7",
                "pattern_score": "0",
                "spread_atr": "0.18",
                "decision": "REJECT",
                "rejection_reasons": "score low",
                "created_order": "false",
                "order_id": "",
            }
        ],
    )
    monkeypatch.setattr(forward_test, "FORWARD_TEST_CSV", tmp_path / "forward.csv")
    monkeypatch.setattr(forward_test, "FORWARD_TEST_SUMMARY_JSON", tmp_path / "summary.json")

    summary = forward_test.run_forward_test(
        provider="synthetic",
        duration_days=1,
        interval_seconds=300,
        asset_class="forex",
        style=TradingStyle.DAY_TRADING,
        export_report=True,
        skip_unhealthy_symbols=False,
        only_tradable_session=True,
        show_next_windows=False,
        max_cycles=1,
    )

    assert calls["runtime"] == {"context": "forward_test_paper.py", "provider": "synthetic", "broker": "paper"}
    assert calls["cycle"] == {"style": "day_trading", "symbols": ["EUR/USD"], "watchlist": "multi_asset_demo"}
    assert summary["total_cycles"] == 1
    assert summary["total_signals"] == 1
    assert (tmp_path / "forward.csv").exists()
    assert (tmp_path / "summary.json").exists()


def test_main_rejects_non_paper_broker(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["forward_test_paper.py", "--broker", "mt5_demo"])
    try:
        forward_test.main()
        raise AssertionError("main() should exit for non-paper broker")
    except SystemExit:
        captured = capsys.readouterr()
        assert "broker_must_be_paper=true" in captured.out


def test_provider_mt5_unavailable_message(monkeypatch) -> None:
    def fake_load_runtime(*args, **kwargs):
        raise SystemExit("MetaTrader5 import failed")

    monkeypatch.setattr(forward_test, "load_demo_runtime", fake_load_runtime)
    try:
        forward_test.run_forward_test(
            provider="mt5",
            duration_days=1,
            interval_seconds=300,
            asset_class="forex",
            style=TradingStyle.DAY_TRADING,
            export_report=False,
        )
        raise AssertionError("run_forward_test should exit")
    except SystemExit as exc:
        assert "provider_mt5_unavailable_in_this_environment=true" in str(exc)


def _cycle_result() -> DemoBotCycleResult:
    now = datetime.now(timezone.utc)
    return DemoBotCycleResult(
        cycle_id="cycle-1",
        started_at=now,
        completed_at=now,
        style=TradingStyle.DAY_TRADING,
        symbols=["EUR/USD"],
        opportunities=1,
        orders_created=0,
        decisions=[
            DemoBotDecision(
                symbol="EUR/USD",
                status="watchlist",
                setup_subtype="ema50_pullback",
                accepted=False,
                reasons=["score low"],
                final_score=66.0,
                risk_reward=1.7,
            )
        ],
        logs=[],
        risk_summary=DailyRiskSummary(
            trades_today=0,
            open_trades=0,
            daily_pnl=0.0,
            daily_loss_percent=0.0,
            remaining_trade_slots=3,
            bot_risk_status="ok",
            consecutive_losses=0,
        ),
    )


def _row(
    symbol: str,
    asset_class: str,
    session: str,
    *,
    score: float,
    status: str,
    near_miss: bool,
    reasons: str,
) -> forward_test.ForwardTestRow:
    return forward_test.ForwardTestRow(
        timestamp=datetime.now(timezone.utc).isoformat(),
        cycle_id="cycle",
        asset_class=asset_class,
        logical_symbol=symbol,
        mt5_symbol=symbol.replace("/", ""),
        provider="mt5",
        broker="paper",
        style="day_trading",
        session_name=session,
        is_tradable_session=True,
        setup="ema50_pullback",
        status=status,
        score=score,
        risk_reward=1.8,
        pattern_score=0.0,
        spread_atr=0.18,
        decision="REJECT",
        near_miss=near_miss,
        rejection_reasons=reasons,
        execution_gate_status="blocked",
        execution_gate_reasons="broker=mt5_demo must be explicitly requested",
        created_order=False,
        order_id="",
    )
