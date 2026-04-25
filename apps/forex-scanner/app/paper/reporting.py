"""Portfolio-state reporting for local paper trading."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.core.types import DirectionBias
from app.execution.models import ExecutionOrder, OrderStatus, PaperBlockRecord


TERMINAL_STATUSES = {
    OrderStatus.FULLY_CLOSED,
    OrderStatus.CLOSED,
    OrderStatus.CANCELLED_TRADE,
    OrderStatus.CANCELED,
    OrderStatus.MISSED_TRADE,
    OrderStatus.EXPIRED_TRADE,
    OrderStatus.REJECTED,
}


def generate_paper_portfolio_report(
    orders: list[ExecutionOrder],
    blocks: list[PaperBlockRecord],
    output_dir: Path,
) -> dict[str, Path]:
    """Write paper portfolio CSV, JSON, and Markdown reports."""

    output_dir.mkdir(parents=True, exist_ok=True)
    orders_frame = _orders_frame(orders)
    blocks_frame = _blocks_frame(blocks)
    exposure_currency = _exposure_frame(_currency_exposure([order for order in orders if order.is_open]), "currency")
    exposure_symbol = _count_frame([order.request.symbol for order in orders if order.is_open], "symbol")
    exposure_subtype = _count_frame([order.request.setup_subtype.value for order in orders if order.is_open], "setup_subtype")
    exposure_session = _count_frame([order.request.session or "unknown" for order in orders if order.is_open], "session")
    guardrail_triggers = _guardrail_trigger_frame(blocks)
    daily_summary = _period_summary_frame(orders, "D")
    weekly_summary = _period_summary_frame(orders, "W")
    score_vs_realized = _score_vs_realized_frame(orders)
    status_realized = _status_realized_frame(orders)
    summary = _summary_payload(orders, blocks)

    outputs = {
        "summary": output_dir / "summary.md",
        "summary_json": output_dir / "summary.json",
        "orders": output_dir / "orders.csv",
        "blocked": output_dir / "blocked.csv",
        "exposure_by_currency": output_dir / "exposure_by_currency.csv",
        "exposure_by_symbol": output_dir / "exposure_by_symbol.csv",
        "exposure_by_subtype": output_dir / "exposure_by_subtype.csv",
        "exposure_by_session": output_dir / "exposure_by_session.csv",
        "guardrail_triggers": output_dir / "guardrail_triggers.csv",
        "daily_summary": output_dir / "daily_summary.csv",
        "weekly_summary": output_dir / "weekly_summary.csv",
        "score_vs_realized": output_dir / "score_vs_realized.csv",
        "status_realized": output_dir / "status_realized.csv",
    }
    orders_frame.to_csv(outputs["orders"], index=False)
    blocks_frame.to_csv(outputs["blocked"], index=False)
    exposure_currency.to_csv(outputs["exposure_by_currency"], index=False)
    exposure_symbol.to_csv(outputs["exposure_by_symbol"], index=False)
    exposure_subtype.to_csv(outputs["exposure_by_subtype"], index=False)
    exposure_session.to_csv(outputs["exposure_by_session"], index=False)
    guardrail_triggers.to_csv(outputs["guardrail_triggers"], index=False)
    daily_summary.to_csv(outputs["daily_summary"], index=False)
    weekly_summary.to_csv(outputs["weekly_summary"], index=False)
    score_vs_realized.to_csv(outputs["score_vs_realized"], index=False)
    status_realized.to_csv(outputs["status_realized"], index=False)
    outputs["summary_json"].write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs["summary"].write_text(_summary_markdown(summary), encoding="utf-8")
    return outputs


def _orders_frame(orders: list[ExecutionOrder]) -> pd.DataFrame:
    rows = [
        {
            "order_id": order.order_id,
            "symbol": order.request.symbol,
            "status": order.status.value,
            "direction": order.request.direction.value,
            "setup_family": order.request.setup_family.value,
            "setup_subtype": order.request.setup_subtype.value,
            "final_score": order.request.final_score,
            "source_status": order.request.source_status,
            "provider": order.request.provider,
            "session": order.request.session,
            "entry": order.request.entry_price,
            "simulated_entry": order.simulated_entry,
            "stop_loss": order.request.stop_loss,
            "tp1": order.request.tp1,
            "tp2": order.request.tp2,
            "tp3": order.request.tp3,
            "remaining_fraction": order.remaining_fraction,
            "realized_r": order.realized_r,
            "realized_pnl": order.realized_pnl,
            "mae": order.mae,
            "mfe": order.mfe,
            "bars_to_activation": order.bars_to_activation,
            "bars_in_trade": order.bars_in_trade,
            "time_in_trade_minutes": order.time_in_trade_minutes,
            "close_reason": order.close_reason.value if order.close_reason else "",
            "events": json.dumps([event.event_type.value for event in order.events]),
            "stop_movements": json.dumps([movement.model_dump(mode="json") for movement in order.stop_movements]),
        }
        for order in orders
    ]
    return pd.DataFrame(rows)


def _blocks_frame(blocks: list[PaperBlockRecord]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "block_id": block.block_id,
                "symbol": block.symbol,
                "status": block.status,
                "setup_family": block.setup_family,
                "setup_subtype": block.setup_subtype,
                "direction": block.direction,
                "final_score": block.final_score,
                "reasons": "; ".join(block.reasons),
                "event_count": len(block.events),
            }
            for block in blocks
        ]
    )


def _summary_payload(orders: list[ExecutionOrder], blocks: list[PaperBlockRecord]) -> dict[str, float | int]:
    closed = [order for order in orders if order.status in {OrderStatus.FULLY_CLOSED, OrderStatus.CLOSED} and order.realized_r is not None]
    r_values = [order.realized_r or 0.0 for order in closed]
    wins = [value for value in r_values if value > 0.0]
    losses = [value for value in r_values if value < 0.0]
    equity = []
    cumulative = 0.0
    for value in r_values:
        cumulative += value
        equity.append(cumulative)
    max_drawdown = _max_drawdown(equity)
    return {
        "open_positions": sum(1 for order in orders if order.status in {OrderStatus.OPEN_TRADE, OrderStatus.PARTIALLY_CLOSED, OrderStatus.ACTIVE}),
        "pending_opportunities": sum(1 for order in orders if order.status in {OrderStatus.PENDING_OPPORTUNITY, OrderStatus.PENDING}),
        "closed_trades": len(closed),
        "blocked_trades": len(blocks),
        "missed_trades": sum(1 for order in orders if order.status == OrderStatus.MISSED_TRADE),
        "expired_trades": sum(1 for order in orders if order.status == OrderStatus.EXPIRED_TRADE),
        "cancelled_trades": sum(1 for order in orders if order.status in {OrderStatus.CANCELLED_TRADE, OrderStatus.CANCELED}),
        "realized_pnl": round(sum(order.realized_pnl or 0.0 for order in closed), 4),
        "unrealized_pnl": 0.0,
        "realized_r": round(sum(r_values), 4),
        "win_rate": round((len(wins) / len(r_values) * 100.0), 2) if r_values else 0.0,
        "expectancy": round((sum(r_values) / len(r_values)), 4) if r_values else 0.0,
        "average_r": round((sum(r_values) / len(r_values)), 4) if r_values else 0.0,
        "median_r": round(_median(r_values), 4) if r_values else 0.0,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses else round(sum(wins), 4),
        "max_drawdown": round(max_drawdown, 4),
    }


def _summary_markdown(summary: dict[str, float | int]) -> str:
    lines = [
        "# Paper Portfolio Report",
        "",
        f"Open positions: {summary['open_positions']}",
        f"Pending opportunities: {summary['pending_opportunities']}",
        f"Closed trades: {summary['closed_trades']}",
        f"Blocked trades: {summary['blocked_trades']}",
        f"Missed trades: {summary['missed_trades']}",
        f"Expired trades: {summary['expired_trades']}",
        f"Realized PnL: {summary['realized_pnl']}",
        f"Unrealized PnL: {summary['unrealized_pnl']}",
        f"Realized R: {summary['realized_r']}",
        f"Win rate: {summary['win_rate']}%",
        f"Expectancy: {summary['expectancy']} R",
        f"Median R: {summary['median_r']} R",
        f"Profit factor: {summary['profit_factor']}",
        f"Max drawdown: {summary['max_drawdown']} R",
        "",
    ]
    return "\n".join(lines)


def _currency_exposure(orders: list[ExecutionOrder]) -> dict[str, int]:
    exposure: dict[str, int] = {}
    for order in orders:
        parts = order.request.symbol.split("/")
        if len(parts) != 2 or order.request.direction not in {DirectionBias.LONG, DirectionBias.SHORT}:
            continue
        base, quote = parts
        base_exposure = 1 if order.request.direction == DirectionBias.LONG else -1
        quote_exposure = -base_exposure
        exposure[base] = exposure.get(base, 0) + base_exposure
        exposure[quote] = exposure.get(quote, 0) + quote_exposure
    return exposure


def _exposure_frame(exposure: dict[str, int], key: str) -> pd.DataFrame:
    return pd.DataFrame([{key: item_key, "exposure": value} for item_key, value in sorted(exposure.items())])


def _count_frame(values: list[str], key: str) -> pd.DataFrame:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return pd.DataFrame([{key: item_key, "open_count": count} for item_key, count in sorted(counts.items())])


def _guardrail_trigger_frame(blocks: list[PaperBlockRecord]) -> pd.DataFrame:
    rows = []
    for block in blocks:
        for reason in block.reasons:
            rows.append(
                {
                    "block_id": block.block_id,
                    "symbol": block.symbol,
                    "setup_subtype": block.setup_subtype,
                    "status": block.status,
                    "reason": reason,
                    "created_at": block.created_at.isoformat(),
                }
            )
    return pd.DataFrame(rows)


def _period_summary_frame(orders: list[ExecutionOrder], frequency: str) -> pd.DataFrame:
    rows = []
    for order in orders:
        if order.closed_at is None or order.realized_r is None:
            continue
        rows.append(
            {
                "closed_at": order.closed_at,
                "realized_r": order.realized_r,
                "realized_pnl": order.realized_pnl or 0.0,
                "win": order.realized_r > 0.0,
            }
        )
    if not rows:
        return pd.DataFrame(columns=["period", "trades", "realized_r", "realized_pnl", "win_rate", "expectancy"])
    frame = pd.DataFrame(rows)
    frame["period"] = pd.to_datetime(frame["closed_at"], utc=True).dt.tz_convert(None).dt.to_period(frequency).astype(str)
    grouped = frame.groupby("period", dropna=False)
    return grouped.agg(
        trades=("realized_r", "count"),
        realized_r=("realized_r", "sum"),
        realized_pnl=("realized_pnl", "sum"),
        win_rate=("win", lambda values: round(float(values.mean() * 100.0), 2)),
        expectancy=("realized_r", "mean"),
    ).reset_index()


def _score_vs_realized_frame(orders: list[ExecutionOrder]) -> pd.DataFrame:
    rows = [
        {
            "score_bucket": _score_bucket(order.request.final_score),
            "final_score": order.request.final_score,
            "realized_r": order.realized_r,
            "win": (order.realized_r or 0.0) > 0.0,
        }
        for order in orders
        if order.request.final_score is not None and order.realized_r is not None
    ]
    if not rows:
        return pd.DataFrame(columns=["score_bucket", "trades", "win_rate", "expectancy"])
    frame = pd.DataFrame(rows)
    grouped = frame.groupby("score_bucket", dropna=False)
    return grouped.agg(
        trades=("realized_r", "count"),
        win_rate=("win", lambda values: round(float(values.mean() * 100.0), 2)),
        expectancy=("realized_r", "mean"),
        average_score=("final_score", "mean"),
    ).reset_index()


def _status_realized_frame(orders: list[ExecutionOrder]) -> pd.DataFrame:
    rows = [
        {
            "source_status": order.request.source_status or "unknown",
            "realized_r": order.realized_r,
            "win": (order.realized_r or 0.0) > 0.0,
        }
        for order in orders
        if order.realized_r is not None
    ]
    if not rows:
        return pd.DataFrame(columns=["source_status", "trades", "win_rate", "expectancy"])
    frame = pd.DataFrame(rows)
    grouped = frame.groupby("source_status", dropna=False)
    return grouped.agg(
        trades=("realized_r", "count"),
        win_rate=("win", lambda values: round(float(values.mean() * 100.0), 2)),
        expectancy=("realized_r", "mean"),
    ).reset_index()


def _score_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    lower = int(value // 10 * 10)
    upper = min(100, lower + 9)
    return f"{lower:02d}-{upper:02d}"


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _max_drawdown(equity: list[float]) -> float:
    peak = 0.0
    drawdown = 0.0
    for value in equity:
        peak = max(peak, value)
        drawdown = min(drawdown, value - peak)
    return abs(drawdown)
