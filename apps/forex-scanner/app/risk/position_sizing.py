"""Conservative position sizing helpers for demo broker execution."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from math import isclose, isfinite
from typing import Any


@dataclass(frozen=True)
class PositionSizeResult:
    """Detailed sizing result used for broker logs and audit payloads."""

    calculated_volume: float
    final_volume: float
    stop_distance: float
    risk_amount: float
    risk_percent: float
    volume_min: float
    volume_step: float
    volume_max: float | None


def calculate_position_size(
    balance: float,
    risk_percent: float,
    entry_price: float,
    stop_loss: float,
    symbol_info: Any,
    max_volume: float | None = None,
    require_tick_value: bool = False,
) -> PositionSizeResult:
    """Calculate a prudent MT5 lot size from account risk and stop distance.

    The result respects MT5 symbol constraints and floors to the configured
    volume step so rounding never increases exposure beyond the clipped value.
    """

    balance_value = _require_positive("balance", balance)
    risk_percent_value = _require_positive("risk_percent", risk_percent)
    entry_value = _require_positive("entry_price", entry_price)
    stop_value = _require_positive("stop_loss", stop_loss)
    stop_distance = abs(entry_value - stop_value)
    if stop_distance <= 0:
        raise ValueError("stop distance must be greater than zero")

    volume_min = _positive_attr(symbol_info, "volume_min") or 0.01
    volume_step = _positive_attr(symbol_info, "volume_step") or volume_min
    volume_max = _positive_attr(symbol_info, "volume_max")
    max_volume_value = _optional_positive("max_volume", max_volume)
    if volume_max is not None and max_volume_value is not None:
        allowed_max = min(volume_max, max_volume_value)
    else:
        allowed_max = volume_max if volume_max is not None else max_volume_value
    if allowed_max is not None and allowed_max < volume_min:
        raise ValueError("max volume is below MT5 minimum volume")

    risk_amount = balance_value * (risk_percent_value / 100.0)
    risk_per_lot = _risk_per_lot(stop_distance, symbol_info, require_tick_value=require_tick_value)
    if risk_per_lot <= 0:
        raise ValueError("risk per lot must be greater than zero")

    calculated_volume = risk_amount / risk_per_lot
    clipped_volume = max(calculated_volume, volume_min)
    if allowed_max is not None:
        clipped_volume = min(clipped_volume, allowed_max)
    final_volume = _floor_to_step(clipped_volume, volume_step)
    if final_volume < volume_min:
        final_volume = volume_min
    if allowed_max is not None and final_volume > allowed_max:
        final_volume = _floor_to_step(allowed_max, volume_step)
    _validate_final_volume(final_volume, volume_min=volume_min, volume_step=volume_step, allowed_max=allowed_max)

    return PositionSizeResult(
        calculated_volume=calculated_volume,
        final_volume=final_volume,
        stop_distance=stop_distance,
        risk_amount=risk_amount,
        risk_percent=risk_percent_value,
        volume_min=volume_min,
        volume_step=volume_step,
        volume_max=volume_max,
    )


def _risk_per_lot(stop_distance: float, symbol_info: Any, *, require_tick_value: bool = False) -> float:
    tick_size = _positive_attr(symbol_info, "trade_tick_size") or _positive_attr(symbol_info, "point")
    tick_value = _positive_attr(symbol_info, "trade_tick_value")
    if tick_size and tick_value:
        return (stop_distance / tick_size) * tick_value
    if require_tick_value:
        raise ValueError("position_sizing_unavailable: missing coherent tick_value/tick_size")
    contract_size = (
        _positive_attr(symbol_info, "trade_contract_size")
        or _positive_attr(symbol_info, "contract_size")
        or 100_000.0
    )
    return stop_distance * contract_size


def _floor_to_step(value: float, step: float) -> float:
    if not isfinite(value) or not isfinite(step) or step <= 0:
        raise ValueError("volume step rounding received an invalid value")
    decimal_value = Decimal(str(value))
    decimal_step = Decimal(str(step))
    steps = (decimal_value / decimal_step).to_integral_value(rounding=ROUND_FLOOR)
    rounded = steps * decimal_step
    return float(rounded.normalize())


def _require_positive(name: str, value: float | None) -> float:
    if value is None:
        raise ValueError(f"{name} is required")
    result = float(value)
    if not isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return result


def _optional_positive(name: str, value: float | None) -> float | None:
    if value is None:
        return None
    result = float(value)
    if not isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return result


def _positive_attr(source: Any, name: str) -> float | None:
    value = getattr(source, name, None)
    if value is None:
        return None
    result = float(value)
    return result if isfinite(result) and result > 0 else None


def _validate_final_volume(final_volume: float, *, volume_min: float, volume_step: float, allowed_max: float | None) -> None:
    if not isfinite(final_volume) or final_volume <= 0:
        raise ValueError("final volume must be greater than zero")
    if final_volume < volume_min:
        raise ValueError("final volume is below MT5 minimum volume")
    if allowed_max is not None and final_volume > allowed_max:
        raise ValueError("final volume exceeds allowed maximum volume")
    if volume_step <= 0 or not isfinite(volume_step):
        raise ValueError("volume_step must be greater than zero")
    steps = final_volume / volume_step
    if not isclose(steps, round(steps), rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("final volume does not respect MT5 volume_step")
