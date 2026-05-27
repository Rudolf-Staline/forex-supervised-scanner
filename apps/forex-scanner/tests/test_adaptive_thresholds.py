import pytest
from app.adaptive_thresholds.engine import AdaptiveThresholdEngine
from app.core.types import TradingStyle
from app.config.settings import AppSettings, AdaptiveThresholdSettings
from app.config.instruments import AssetClass

class DummySettings:
    adaptive_thresholds = AdaptiveThresholdSettings(
        enabled=True,
        mode="scanner_effective",
        min_sample_size=30,
        max_daily_change=2.0,
        hard_floor_forex=70.0,
        hard_floor_commodities=78.0,
        hard_floor_indices=80.0,
        hard_cap=92.0
    )


def test_fallback_missing_history(monkeypatch):
    engine = AdaptiveThresholdEngine(DummySettings())
    engine._historical_data = {}
    res = engine.calculate("EURUSD", TradingStyle.DAY_TRADING)
    assert res.sample_size == 0
    assert res.effective_min_score == 75.0 # Base min score for forex
    assert res.history_adjustment == 0.0
    assert "insufficient samples" in res.reason_summary


def test_sample_size_low(monkeypatch):
    engine = AdaptiveThresholdEngine(DummySettings())
    engine._historical_data = {
        "EURUSD": {
            TradingStyle.DAY_TRADING: {"samples": 15, "wins": 10, "losses": 5, "total_rr": 20.0}
        }
    }
    res = engine.calculate("EURUSD", TradingStyle.DAY_TRADING)
    assert res.sample_size == 15
    assert res.effective_min_score == 75.0
    assert "insufficient samples" in res.reason_summary


def test_bad_history_increases_threshold(monkeypatch):
    engine = AdaptiveThresholdEngine(DummySettings())
    engine._historical_data = {
        "EURUSD": {
            TradingStyle.DAY_TRADING: {"samples": 50, "wins": 10, "losses": 40, "total_rr": 10.0}
        }
    }
    res = engine.calculate("EURUSD", TradingStyle.DAY_TRADING)
    assert res.history_adjustment == 2.0
    assert res.effective_min_score == 77.0


def test_good_history_decreases_threshold(monkeypatch):
    engine = AdaptiveThresholdEngine(DummySettings())
    engine._historical_data = {
        "EURUSD": {
            TradingStyle.DAY_TRADING: {"samples": 50, "wins": 30, "losses": 20, "total_rr": 80.0}
        }
    }
    res = engine.calculate("EURUSD", TradingStyle.DAY_TRADING)
    assert res.history_adjustment == -1.0
    assert res.effective_min_score == 74.0


def test_scalping_stricter():
    engine = AdaptiveThresholdEngine(DummySettings())
    engine._historical_data = {
        "EURUSD": {
            TradingStyle.SCALPING: {"samples": 50, "wins": 30, "losses": 20, "total_rr": 80.0}
        }
    }
    res = engine.calculate("EURUSD", TradingStyle.SCALPING)
    assert res.style_adjustment == 2.0
    # base is 75, style +2, hist -1 => 76
    assert res.effective_min_score == 76.0


def test_swing_trading_loose():
    engine = AdaptiveThresholdEngine(DummySettings())
    engine._historical_data = {
        "EURUSD": {
            TradingStyle.SWING_TRADING: {"samples": 50, "wins": 30, "losses": 20, "total_rr": 80.0}
        }
    }
    res = engine.calculate("EURUSD", TradingStyle.SWING_TRADING)
    assert res.style_adjustment == -1.0
    # base 75, style -1, hist -1 => 73
    assert res.effective_min_score == 73.0


def test_swing_trading_needs_good_history():
    engine = AdaptiveThresholdEngine(DummySettings())
    engine._historical_data = {
        "EURUSD": {
            TradingStyle.SWING_TRADING: {"samples": 50, "wins": 10, "losses": 40, "total_rr": 10.0} # bad history
        }
    }
    res = engine.calculate("EURUSD", TradingStyle.SWING_TRADING)
    # The loose threshold for swing is revoked on bad performance
    assert res.style_adjustment == 0.0
    assert res.history_adjustment == 2.0
    assert res.effective_min_score == 77.0


def test_hard_floors_respect():
    engine = AdaptiveThresholdEngine(DummySettings())
    engine._historical_data = {
        "EURUSD": { TradingStyle.DAY_TRADING: {"samples": 50, "wins": 50, "losses": 0, "total_rr": 100.0} },
        "XAU/USD": { TradingStyle.DAY_TRADING: {"samples": 50, "wins": 50, "losses": 0, "total_rr": 100.0} },
        "US500": { TradingStyle.DAY_TRADING: {"samples": 50, "wins": 50, "losses": 0, "total_rr": 100.0} }
    }

    # We tweak max_daily_change so we can reach the floor in one test step
    engine.max_daily_change = 10.0

    res_fx = engine.calculate("EURUSD", TradingStyle.DAY_TRADING)
    assert res_fx.effective_min_score >= 70.0 # Forex floor

    res_com = engine.calculate("XAU/USD", TradingStyle.DAY_TRADING)
    assert res_com.effective_min_score >= 78.0 # Commodities floor

    res_ind = engine.calculate("US500", TradingStyle.DAY_TRADING)
    assert res_ind.effective_min_score >= 80.0 # Indices floor


def test_hard_cap_respect():
    engine = AdaptiveThresholdEngine(DummySettings())
    engine.hard_cap = 76.0 # Set low cap just for testing
    engine.max_daily_change = 10.0
    engine._historical_data = {
        "EURUSD": { TradingStyle.DAY_TRADING: {"samples": 50, "wins": 0, "losses": 50, "total_rr": 0.0} }
    }
    res = engine.calculate("EURUSD", TradingStyle.DAY_TRADING)
    assert res.effective_min_score <= 76.0
