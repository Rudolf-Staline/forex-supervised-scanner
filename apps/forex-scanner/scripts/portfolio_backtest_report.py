"""Apply portfolio constraints to a JSON trade-candidate export.

Input can be either a JSON list of TradeRecord objects or a dictionary containing
``trades`` / ``accepted_trades``. Reporting only; no broker action is possible.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backtest.portfolio import (  # noqa: E402
    PortfolioConstraints,
    simulation_to_dict,
    simulate_portfolio,
)
from app.core.types import TradeRecord  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Portfolio-level replay of historical trade candidates. No orders are sent."
    )
    parser.add_argument("--input-json", required=True, help="TradeRecord list or payload containing trades.")
    parser.add_argument("--output-json", default=str(PROJECT_ROOT / "reports" / "portfolio_backtest.json"))
    parser.add_argument("--output-txt", default=str(PROJECT_ROOT / "reports" / "portfolio_backtest.txt"))
    parser.add_argument("--max-positions", type=int, default=3)
    parser.add_argument("--max-same-symbol", type=int, default=1)
    parser.add_argument(
        "--max-currency-exposure",
        type=int,
        default=2,
        help="Maximum absolute signed currency units; use 0 to disable.",
    )
    parser.add_argument(
        "--daily-loss-limit-r",
        type=float,
        default=3.0,
        help="Stop accepting new trades after realized daily R reaches this loss; use 0 to disable.",
    )
    args = parser.parse_args()

    candidates = _load_candidates(Path(args.input_json))
    constraints = PortfolioConstraints(
        max_concurrent_positions=args.max_positions,
        max_same_symbol_positions=args.max_same_symbol,
        max_abs_currency_exposure=(
            None if args.max_currency_exposure == 0 else args.max_currency_exposure
        ),
        daily_loss_limit_r=(
            None if args.daily_loss_limit_r == 0.0 else args.daily_loss_limit_r
        ),
    )
    result = simulate_portfolio(candidates, constraints)

    json_path = Path(args.output_json)
    text_path = Path(args.output_txt)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(simulation_to_dict(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    text_path.write_text(_render_text(result), encoding="utf-8")

    print(_render_text(result), end="")
    print(f"json_export={json_path}")
    print(f"txt_export={text_path}")


def _load_candidates(path: Path) -> list[TradeRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("trades")
        if rows is None:
            rows = payload.get("accepted_trades")
        if rows is None:
            raise ValueError("input dictionary must contain 'trades' or 'accepted_trades'")
    else:
        raise ValueError("input JSON must be a list or dictionary")
    if not isinstance(rows, list):
        raise ValueError("trade payload must be a list")
    return [TradeRecord.model_validate(row) for row in rows]


def _render_text(result) -> str:  # noqa: ANN001 - concise CLI renderer
    metrics = result.metrics
    lines = [
        "Portfolio Backtest Report (equal-risk R units; paper-only)",
        "==========================================================",
        f"candidates         : {len(result.accepted_trades) + len(result.rejected_trades)}",
        f"accepted           : {len(result.accepted_trades)}",
        f"rejected           : {len(result.rejected_trades)}",
        f"max positions      : {result.constraints.max_concurrent_positions}",
        f"same symbol limit  : {result.constraints.max_same_symbol_positions}",
        f"currency exposure  : {result.constraints.max_abs_currency_exposure}",
        f"daily loss limit   : {result.constraints.daily_loss_limit_r}",
        "",
        "Accepted-trade performance:",
        f"  expectancy       : {metrics.expectancy:.4f} R",
        f"  trades           : {metrics.number_of_trades}",
        f"  win rate         : {metrics.win_rate:.2f}%",
        f"  profit factor    : {metrics.profit_factor:.4f}",
        f"  max drawdown     : {metrics.max_drawdown:.4f} R",
        "",
        "Rejection counts:",
    ]
    if result.rejection_counts:
        lines.extend(
            f"  {reason:<24}: {count}"
            for reason, count in result.rejection_counts.items()
        )
    else:
        lines.append("  none")
    lines.extend(
        [
            "",
            "Limitations:",
            "  - every accepted trade is treated as one equal R unit",
            "  - currency exposure is directional, not volatility- or notional-weighted",
            "  - margin, slippage, swaps, and partial exits are not modeled here",
            "  - candidates must already contain honest historical entry and exit times",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
