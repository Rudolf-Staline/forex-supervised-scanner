from datetime import datetime, timezone

from app.core.types import TradingStyle
from app.execution.demo_bot import DemoBotCycleResult, DemoBotDecision
from app.reporting.signal_journal import append_cycle_signal_journal
from app.risk.daily_limits import DailyRiskSummary


def test_append_cycle_signal_journal_writes_required_fields(tmp_path):
    out = tmp_path / "signal_journal.jsonl"
    result = DemoBotCycleResult(
        cycle_id="c1",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        style=TradingStyle.DAY_TRADING,
        symbols=["EUR/USD"],
        opportunities=1,
        orders_created=0,
        decisions=[DemoBotDecision(symbol="EUR/USD", status="watchlist", setup_subtype="ema50_pullback", accepted=False)],
        logs=[],
        risk_summary=DailyRiskSummary(trades_today=0, open_trades=0, daily_pnl=0.0, daily_loss_percent=0.0, remaining_trade_slots=3, bot_risk_status="ok", consecutive_losses=0),
    )
    written = append_cycle_signal_journal(result, provider="synthetic", broker="paper", mode="paper", watchlist="multi_asset_demo", rejected_records=[], created_orders=[], output_path=out)
    assert written == 1
    row = out.read_text(encoding="utf-8").splitlines()[0]
    assert "timestamp_utc" in row
    assert "logical_symbol" in row
    assert "safety_status" in row
