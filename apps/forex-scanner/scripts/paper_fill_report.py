"""Report realistic paper fill quality. No orders are sent."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.env import load_dotenv
from app.config.settings import load_settings
from app.execution.models import ExecutionOrder, PaperBlockRecord
from app.storage.database import Database


def main() -> None:
    """Print paper fill execution-quality diagnostics."""

    parser = argparse.ArgumentParser(description="Report realistic paper fill quality. No orders are sent.")
    parser.parse_args()
    load_dotenv()
    settings = load_settings()
    database = Database(settings.database_absolute_path)
    report = build_paper_fill_report(database.load_paper_orders(), database.load_paper_blocks())
    print_paper_fill_report(report)


def build_paper_fill_report(orders: list[ExecutionOrder], blocks: list[PaperBlockRecord]) -> dict[str, object]:
    """Aggregate paper fill costs and rejections."""

    fill_orders = [order for order in orders if order.execution_assumptions.get("paper_realistic_fill")]
    rejected_blocks = [
        block
        for block in blocks
        if any("paper fill rejected" in reason for reason in block.reasons)
    ]
    return {
        "average_slippage": _average(_assumption_float(order, "paper_slippage_points") for order in fill_orders),
        "average_spread_cost": _average(_assumption_float(order, "paper_spread_cost") for order in fill_orders),
        "rejected_paper_fills": len(rejected_blocks),
        "symbols_with_worst_execution": _worst_symbols(fill_orders),
        "setups_most_affected_by_costs": _worst_setups(fill_orders),
        "rejection_reasons": dict(Counter(reason for block in rejected_blocks for reason in block.reasons).most_common(10)),
    }


def print_paper_fill_report(report: dict[str, object]) -> None:
    """Print a compact paper-fill report."""

    print("paper_fill_report=no_orders_sent")
    print(f"average_slippage={report['average_slippage']:.8f}")
    print(f"average_spread_cost={report['average_spread_cost']:.8f}")
    print(f"rejected_paper_fills={report['rejected_paper_fills']}")
    print(f"symbols_with_worst_execution={json.dumps(report['symbols_with_worst_execution'], sort_keys=True)}")
    print(f"setups_most_affected_by_costs={json.dumps(report['setups_most_affected_by_costs'], sort_keys=True)}")
    print(f"rejection_reasons={json.dumps(report['rejection_reasons'], sort_keys=True)}")


def _worst_symbols(orders: list[ExecutionOrder]) -> list[dict[str, object]]:
    costs: dict[str, list[float]] = defaultdict(list)
    for order in orders:
        costs[order.request.symbol].append(_total_cost(order))
    ranked = sorted(
        ((symbol, sum(values) / len(values), len(values)) for symbol, values in costs.items() if values),
        key=lambda item: item[1],
        reverse=True,
    )
    return [{"symbol": symbol, "average_cost": round(cost, 8), "orders": count} for symbol, cost, count in ranked[:10]]


def _worst_setups(orders: list[ExecutionOrder]) -> list[dict[str, object]]:
    costs: dict[str, list[float]] = defaultdict(list)
    for order in orders:
        costs[order.request.setup_subtype.value].append(_total_cost(order))
    ranked = sorted(
        ((setup, sum(values) / len(values), len(values)) for setup, values in costs.items() if values),
        key=lambda item: item[1],
        reverse=True,
    )
    return [{"setup": setup, "average_cost": round(cost, 8), "orders": count} for setup, cost, count in ranked[:10]]


def _total_cost(order: ExecutionOrder) -> float:
    return (
        _assumption_float(order, "paper_slippage_points")
        + _assumption_float(order, "paper_spread_cost")
        + _assumption_float(order, "paper_commission_estimate")
    )


def _assumption_float(order: ExecutionOrder, key: str) -> float:
    value = order.execution_assumptions.get(key, 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _average(values) -> float:
    clean = [float(value) for value in values if value is not None]
    return sum(clean) / len(clean) if clean else 0.0


if __name__ == "__main__":
    main()
