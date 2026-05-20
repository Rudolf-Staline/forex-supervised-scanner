"""MT5 filling-mode fallback helpers shared by demo broker tools."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger(__name__)

UNSUPPORTED_FILLING_RETCODE = 10030


@dataclass(frozen=True)
class MT5FillingAttempt:
    """One MT5 order_send attempt with a specific filling mode."""

    symbol: str
    filling_mode_label: str
    filling_mode: int
    retcode: int | None
    comment: str
    success: bool


@dataclass(frozen=True)
class MT5FillingResolution:
    """Final result after trying compatible MT5 filling modes."""

    result: object | None
    payload: dict[str, object]
    filling_mode_label: str | None
    filling_mode: int | None
    attempts: list[MT5FillingAttempt] = field(default_factory=list)


def get_supported_filling_modes(mt5: object, _symbol_info: object | None = None) -> list[tuple[str, int]]:
    """Return the filling modes to try, in the safe Deriv/MT5 retry order."""

    modes: list[tuple[str, int]] = []
    for label, attr_name in (
        ("IOC", "ORDER_FILLING_IOC"),
        ("FOK", "ORDER_FILLING_FOK"),
        ("RETURN", "ORDER_FILLING_RETURN"),
    ):
        raw = getattr(mt5, attr_name, None)
        if raw is None:
            continue
        try:
            mode = int(raw)
        except (TypeError, ValueError):
            continue
        if (label, mode) not in modes:
            modes.append((label, mode))
    return modes


def resolve_mt5_filling_mode_or_try_fallbacks(
    mt5: object,
    *,
    symbol: str,
    build_payload: Callable[[int], dict[str, object]],
    symbol_info: object | None = None,
    logger: logging.Logger | None = None,
) -> MT5FillingResolution:
    """Try IOC, FOK, then RETURN until MT5 accepts the order or a hard rejection occurs."""

    log = logger or LOGGER
    attempts: list[MT5FillingAttempt] = []
    success_codes = _success_retcodes(mt5)
    last_result: object | None = None
    last_payload: dict[str, object] = {}
    modes = get_supported_filling_modes(mt5, symbol_info)
    if not modes:
        log.warning("No compatible filling mode found for this symbol", extra={"symbol": symbol})
        return MT5FillingResolution(result=None, payload={}, filling_mode_label=None, filling_mode=None, attempts=attempts)

    for label, mode in modes:
        payload = build_payload(mode)
        last_payload = payload
        result = getattr(mt5, "order_send")(payload)
        last_result = result
        retcode = _retcode(result)
        comment = "" if result is None else str(getattr(result, "comment", ""))
        success = retcode in success_codes
        attempts.append(
            MT5FillingAttempt(
                symbol=symbol,
                filling_mode_label=label,
                filling_mode=mode,
                retcode=retcode,
                comment=comment,
                success=success,
            )
        )
        log.info(
            "MT5 filling mode attempted",
            extra={
                "symbol": symbol,
                "filling_mode": label,
                "filling_mode_value": mode,
                "retcode": retcode,
                "comment": comment,
            },
        )
        if success:
            log.info(
                "MT5 filling mode used",
                extra={"symbol": symbol, "filling_mode": label, "filling_mode_value": mode, "retcode": retcode},
            )
            return MT5FillingResolution(result=result, payload=payload, filling_mode_label=label, filling_mode=mode, attempts=attempts)
        if retcode == UNSUPPORTED_FILLING_RETCODE:
            continue
        return MT5FillingResolution(result=result, payload=payload, filling_mode_label=label, filling_mode=mode, attempts=attempts)

    log.warning("No compatible filling mode found for this symbol", extra={"symbol": symbol})
    return MT5FillingResolution(result=last_result, payload=last_payload, filling_mode_label=None, filling_mode=None, attempts=attempts)


def filling_attempts_payload(attempts: list[MT5FillingAttempt]) -> list[dict[str, str | int | bool | None]]:
    """Convert filling attempts to a JSON-safe broker/audit payload."""

    return [
        {
            "symbol": attempt.symbol,
            "filling_mode": attempt.filling_mode_label,
            "filling_mode_value": attempt.filling_mode,
            "retcode": attempt.retcode,
            "comment": attempt.comment,
            "success": attempt.success,
        }
        for attempt in attempts
    ]


def _success_retcodes(mt5: object) -> set[int]:
    values: list[Any] = [
        getattr(mt5, "TRADE_RETCODE_DONE", 10009),
        getattr(mt5, "TRADE_RETCODE_PLACED", 10008),
        getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", None),
    ]
    return {int(value) for value in values if value is not None}


def _retcode(result: object | None) -> int | None:
    if result is None:
        return None
    raw = getattr(result, "retcode", None)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
