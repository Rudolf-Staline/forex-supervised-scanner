"""Unit tests for the manual Deriv-Demo tiny order probe."""

from __future__ import annotations

from app.execution.mt5_filling import get_supported_filling_modes


def test_get_supported_filling_modes_returns_safe_retry_order() -> None:
    fake_mt5 = _FakeMT5()
    symbol_info = _SymbolInfo()

    modes = get_supported_filling_modes(fake_mt5, symbol_info)

    assert modes == [("IOC", 1), ("FOK", 2), ("RETURN", 3)]
    assert fake_mt5.order_send_called is False


class _FakeMT5:
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_FOK = 2
    ORDER_FILLING_RETURN = 3

    def __init__(self) -> None:
        self.order_send_called = False

    def order_send(self, _payload):
        self.order_send_called = True
        raise AssertionError("get_supported_filling_modes must not send orders")


class _SymbolInfo:
    filling_mode = 2
    trade_execution = 0
    volume_min = 0.01
    volume_step = 0.01
