import pytest
from app.adaptive_thresholds.provider import AdaptiveThresholdProvider
from app.core.types import TradingStyle
from app.config.settings import AdaptiveThresholdSettings

class DummySettings:
    def __init__(self, enabled, mode):
        self.adaptive_thresholds = AdaptiveThresholdSettings(
            enabled=enabled,
            mode=mode
        )

def test_provider_disabled():
    settings = DummySettings(enabled=False, mode="scanner_effective")
    provider = AdaptiveThresholdProvider(settings)
    assert not provider.enabled

    # Should use fallback without calculating
    res = provider.get_threshold("EURUSD", TradingStyle.DAY_TRADING)
    assert res.is_fallback
    assert res.base_min_score == 75.0

    eff = provider.get_effective_min_score("EURUSD", TradingStyle.DAY_TRADING)
    assert eff == 75.0

def test_provider_report_only():
    settings = DummySettings(enabled=True, mode="report_only")
    provider = AdaptiveThresholdProvider(settings)

    # Give it some dummy history
    provider.engine._historical_data = {
        "EURUSD": {
            TradingStyle.DAY_TRADING: {"samples": 50, "wins": 50, "losses": 0, "total_rr": 100.0}
        }
    }
    provider.engine.max_daily_change = 10.0

    res = provider.get_threshold("EURUSD", TradingStyle.DAY_TRADING)
    assert not res.is_fallback
    assert res.recommended_min_score < res.base_min_score

    # But effective should still be base since it's report_only
    eff = provider.get_effective_min_score("EURUSD", TradingStyle.DAY_TRADING)
    assert eff == res.base_min_score

def test_provider_scanner_effective():
    settings = DummySettings(enabled=True, mode="scanner_effective")
    provider = AdaptiveThresholdProvider(settings)

    # Give it some dummy history
    provider.engine._historical_data = {
        "EURUSD": {
            TradingStyle.DAY_TRADING: {"samples": 50, "wins": 50, "losses": 0, "total_rr": 100.0}
        }
    }
    provider.engine.max_daily_change = 10.0

    res = provider.get_threshold("EURUSD", TradingStyle.DAY_TRADING)
    assert not res.is_fallback

    # Effective should match the adaptive calculation
    eff = provider.get_effective_min_score("EURUSD", TradingStyle.DAY_TRADING)
    assert eff == res.effective_min_score
    assert eff < res.base_min_score

def test_provider_fallback_on_error(monkeypatch):
    settings = DummySettings(enabled=True, mode="scanner_effective")
    provider = AdaptiveThresholdProvider(settings)

    # Force engine to crash
    def crash(*args, **kwargs):
        raise ValueError("Engine failure")

    monkeypatch.setattr(provider.engine, "calculate", crash)

    res = provider.get_threshold("EURUSD", TradingStyle.DAY_TRADING)
    assert res.is_fallback
    assert res.effective_min_score == 75.0

    eff = provider.get_effective_min_score("EURUSD", TradingStyle.DAY_TRADING)
    assert eff == 75.0
