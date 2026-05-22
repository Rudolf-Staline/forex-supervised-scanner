"""Position sizing tests for cautious MT5 demo execution."""

from __future__ import annotations

import pytest

from app.risk.position_sizing import calculate_position_size


def test_position_size_respects_max_volume_and_step() -> None:
    result = calculate_position_size(
        balance=100_000.0,
        risk_percent=0.25,
        entry_price=1.1000,
        stop_loss=1.0950,
        symbol_info=_SymbolInfo(),
        max_volume=0.05,
    )

    assert result.calculated_volume == pytest.approx(0.5)
    assert result.final_volume == 0.05
    assert result.stop_distance == pytest.approx(0.005)


def test_position_size_floors_to_mt5_volume_step() -> None:
    result = calculate_position_size(
        balance=1_234.0,
        risk_percent=0.25,
        entry_price=1.1000,
        stop_loss=1.0950,
        symbol_info=_SymbolInfo(),
        max_volume=0.05,
    )

    assert result.final_volume == 0.01


def test_position_size_respects_symbol_volume_max() -> None:
    result = calculate_position_size(
        balance=100_000.0,
        risk_percent=0.25,
        entry_price=1.1000,
        stop_loss=1.0950,
        symbol_info=_SymbolInfo(volume_max=0.03),
        max_volume=0.05,
    )

    assert result.final_volume == 0.03


def test_position_size_rejects_missing_levels() -> None:
    with pytest.raises(ValueError, match="entry_price is required"):
        calculate_position_size(1000.0, 0.25, None, 1.0950, _SymbolInfo())

    with pytest.raises(ValueError, match="stop_loss is required"):
        calculate_position_size(1000.0, 0.25, 1.1000, None, _SymbolInfo())


def test_position_size_rejects_zero_stop_distance() -> None:
    with pytest.raises(ValueError, match="stop distance"):
        calculate_position_size(1000.0, 0.25, 1.1000, 1.1000, _SymbolInfo())


class _SymbolInfo:
    def __init__(self, *, volume_max: float = 100.0) -> None:
        self.volume_min = 0.01
        self.volume_step = 0.01
        self.volume_max = volume_max
        self.trade_tick_size = 0.00001
        self.trade_tick_value = 1.0
        self.trade_contract_size = 100_000.0
