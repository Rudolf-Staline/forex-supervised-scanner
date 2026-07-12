"""Portfolio-constrained aggregation for canonical walk-forward OOS candidates.

The ordinary walk-forward report remains the audit source for per-fold threshold
selection and de-duplicated candidate generation. This module applies the
portfolio allocator only after that canonical OOS sample has been assembled, so
portfolio constraints cannot influence in-sample tuning or fold construction.

Paper/research only. Nothing here sends orders.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.backtest.metrics import calculate_metrics
from app.backtest.portfolio import (
    PortfolioConstraints,
    PortfolioRejection,
    PortfolioSimulation,
    simulate_portfolio,
)
from app.backtest.walk_forward import (
    WalkForwardReport,
    deduplicated_oos_trades,
    raw_oos_trade_count,
    report_to_dict as candidate_report_to_dict,
)
from app.core.types import TradeRecord

_TRADE_FIELDNAMES = ["pair", "timestamp", "score", "gross_r", "net_r", "exit_reason"]
_REJECTION_FIELDNAMES = [
    "pair",
    "timestamp",
    "score",
    "reason",
    "detail",
    "active_symbols",
    "currency_exposure",
    "realized_day_r",
]


@dataclass(frozen=True)
class PortfolioWalkForwardReport:
    """Canonical OOS candidates plus their attainable portfolio allocation."""

    candidate_report: WalkForwardReport
    canonical_candidates: list[TradeRecord]
    portfolio: PortfolioSimulation

    @property
    def aggregate_metrics(self):  # noqa: ANN201 - mirrors WalkForwardReport ergonomics
        """Headline metrics after portfolio constraints."""

        return self.portfolio.metrics

    @property
    def oos_equity_curve(self) -> list[tuple[datetime, float]]:
        """Realized portfolio equity, including the zero starting point."""

        return [(self.candidate_report.start, 0.0), *self.portfolio.equity_curve]


def apply_portfolio_constraints(
    report: WalkForwardReport,
    constraints: PortfolioConstraints | None = None,
) -> PortfolioWalkForwardReport:
    """Apply portfolio rules to the de-duplicated OOS candidate stream.

    Threshold tuning and fold metrics are already fixed inside ``report``. The
    allocator sees only the canonical OOS candidates and therefore cannot leak
    portfolio outcomes back into the signal-selection process.
    """

    candidates = deduplicated_oos_trades(report)
    simulation = simulate_portfolio(candidates, constraints)
    return PortfolioWalkForwardReport(
        candidate_report=report,
        canonical_candidates=candidates,
        portfolio=simulation,
    )


def portfolio_report_to_dict(report: PortfolioWalkForwardReport) -> dict[str, object]:
    """Serialize the portfolio-constrained walk-forward result."""

    payload = candidate_report_to_dict(report.candidate_report)
    raw_records = raw_oos_trade_count(report.candidate_report)
    candidate_count = len(report.canonical_candidates)
    accepted_count = len(report.portfolio.accepted_trades)
    rejected_count = len(report.portfolio.rejected_trades)
    candidate_metrics = calculate_metrics(report.canonical_candidates)

    payload["candidate_out_of_sample"] = payload.pop("out_of_sample")
    payload["out_of_sample"] = {
        "aggregation": "deduplicated_then_portfolio_constrained",
        "raw_fold_trade_records": raw_records,
        "canonical_candidate_trades": candidate_count,
        "duplicates_removed": raw_records - candidate_count,
        "accepted_trades": accepted_count,
        "rejected_trades": rejected_count,
        "rejection_counts": dict(report.portfolio.rejection_counts),
        "candidate_metrics": candidate_metrics.model_dump(mode="json"),
        "metrics": report.portfolio.metrics.model_dump(mode="json"),
        "equity_curve": [
            [timestamp.isoformat(), value] for timestamp, value in report.oos_equity_curve
        ],
    }
    payload["portfolio"] = {
        "constraints": _constraints_to_dict(report.portfolio.constraints),
        "accepted_trade_keys": [_trade_key(trade) for trade in report.portfolio.accepted_trades],
        "rejections": [_rejection_to_dict(item) for item in report.portfolio.rejected_trades],
    }
    payload["artifacts"] = {
        "candidate_registry": "oos_trade_registry.csv",
        "portfolio_registry": "portfolio_trade_registry.csv",
        "portfolio_rejections": "portfolio_rejections.csv",
    }
    return payload


def portfolio_report_to_text(report: PortfolioWalkForwardReport) -> str:
    """Render a compact portfolio-constrained walk-forward summary."""

    source = report.candidate_report
    candidate_metrics = calculate_metrics(report.canonical_candidates)
    constraints = report.portfolio.constraints
    raw_records = raw_oos_trade_count(source)
    candidate_count = len(report.canonical_candidates)
    accepted_count = len(report.portfolio.accepted_trades)
    rejected_count = len(report.portfolio.rejected_trades)
    setup_filter = (
        source.setup_filter if isinstance(source.setup_filter, str) else source.setup_filter.value
    )

    lines = [
        "Portfolio-Constrained Walk-Forward Report (paper-only)",
        "======================================================",
        f"symbols                    : {', '.join(source.symbols)}",
        f"style                      : {source.style.value}",
        f"setup_filter               : {setup_filter}",
        f"range                      : {source.start.date()} -> {source.end.date()}",
        f"folds                      : {len(source.folds)}",
        f"raw OOS fold records       : {raw_records}",
        f"canonical OOS candidates   : {candidate_count}",
        f"overlap duplicates removed : {raw_records - candidate_count}",
        "",
        "Portfolio constraints:",
        f"  max concurrent positions : {constraints.max_concurrent_positions}",
        f"  max same-symbol positions: {constraints.max_same_symbol_positions}",
        f"  max abs currency exposure: {_format_optional(constraints.max_abs_currency_exposure)}",
        f"  daily loss limit          : {_format_optional(constraints.daily_loss_limit_r, suffix=' R')}",
        "",
        "Allocation:",
        f"  accepted trades          : {accepted_count}",
        f"  rejected trades          : {rejected_count}",
        f"  rejection counts         : {_format_counts(report.portfolio.rejection_counts)}",
        "",
        "Candidate sample before portfolio allocation:",
        f"  expectancy/trade         : {candidate_metrics.expectancy:.4f} R",
        f"  profit factor            : {candidate_metrics.profit_factor:.4f}",
        f"  max drawdown             : {candidate_metrics.max_drawdown:.4f} R",
        "",
        "Attainable portfolio OUT-OF-SAMPLE performance:",
        f"  expectancy/trade         : {report.portfolio.metrics.expectancy:.4f} R",
        f"  win rate                 : {report.portfolio.metrics.win_rate:.2f}%",
        f"  profit factor            : {report.portfolio.metrics.profit_factor:.4f}",
        f"  max drawdown             : {report.portfolio.metrics.max_drawdown:.4f} R",
        f"  expectancy 95% CI        : [{report.portfolio.metrics.expectancy_ci_low:.4f}, "
        f"{report.portfolio.metrics.expectancy_ci_high:.4f}] R",
        "",
        "Per-fold candidate diagnostics remain embedded in the JSON payload and are not",
        "portfolio-constrained; only the canonical cross-fold OOS sample is allocated.",
        "Candidate calibration should use oos_trade_registry.csv; attainable portfolio",
        "edge analysis should use portfolio_trade_registry.csv.",
    ]
    return "\n".join(lines) + "\n"


def write_portfolio_walk_forward_reports(
    report: PortfolioWalkForwardReport,
    output_dir: Path,
) -> dict[str, Path]:
    """Write portfolio reports and distinct candidate/accepted/rejection registries."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "portfolio_walk_forward.json"
    text_path = output_dir / "portfolio_walk_forward.txt"
    candidate_registry_path = output_dir / "oos_trade_registry.csv"
    portfolio_registry_path = output_dir / "portfolio_trade_registry.csv"
    rejection_path = output_dir / "portfolio_rejections.csv"

    json_path.write_text(
        json.dumps(portfolio_report_to_dict(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    text_path.write_text(portfolio_report_to_text(report), encoding="utf-8")
    _write_trade_registry(report.canonical_candidates, candidate_registry_path)
    _write_trade_registry(report.portfolio.accepted_trades, portfolio_registry_path)
    _write_rejections(report.portfolio.rejected_trades, rejection_path)

    return {
        "json": json_path,
        "txt": text_path,
        "candidate_registry": candidate_registry_path,
        "portfolio_registry": portfolio_registry_path,
        "rejections": rejection_path,
    }


def _write_trade_registry(trades: list[TradeRecord], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_TRADE_FIELDNAMES)
        writer.writeheader()
        writer.writerows(_trade_rows(trades))
    return path


def _write_rejections(rejections: list[PortfolioRejection], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_REJECTION_FIELDNAMES)
        writer.writeheader()
        writer.writerows(_rejection_rows(rejections))
    return path


def _trade_rows(trades: list[TradeRecord]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for trade in sorted(trades, key=lambda item: (item.entry_time, item.symbol, item.exit_time)):
        rows.append(
            {
                "pair": trade.symbol,
                "timestamp": trade.entry_time.isoformat(),
                "score": "" if trade.final_score is None else round(float(trade.final_score), 6),
                "gross_r": round(float(trade.gross_r), 6),
                "net_r": round(float(trade.net_r), 6),
                "exit_reason": trade.exit_reason,
            }
        )
    return rows


def _rejection_rows(rejections: list[PortfolioRejection]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for rejection in rejections:
        trade = rejection.trade
        rows.append(
            {
                "pair": trade.symbol,
                "timestamp": trade.entry_time.isoformat(),
                "score": "" if trade.final_score is None else round(float(trade.final_score), 6),
                "reason": rejection.reason,
                "detail": rejection.detail,
                "active_symbols": "|".join(rejection.active_symbols),
                "currency_exposure": json.dumps(
                    rejection.currency_exposure, sort_keys=True, separators=(",", ":")
                ),
                "realized_day_r": round(float(rejection.realized_day_r), 6),
            }
        )
    return rows


def _constraints_to_dict(constraints: PortfolioConstraints) -> dict[str, object]:
    return {
        "max_concurrent_positions": constraints.max_concurrent_positions,
        "max_same_symbol_positions": constraints.max_same_symbol_positions,
        "max_abs_currency_exposure": constraints.max_abs_currency_exposure,
        "daily_loss_limit_r": constraints.daily_loss_limit_r,
    }


def _trade_key(trade: TradeRecord) -> dict[str, str]:
    return {
        "pair": trade.symbol,
        "entry_time": trade.entry_time.isoformat(),
        "exit_time": trade.exit_time.isoformat(),
    }


def _rejection_to_dict(rejection: PortfolioRejection) -> dict[str, object]:
    return {
        "trade": _trade_key(rejection.trade),
        "reason": rejection.reason,
        "detail": rejection.detail,
        "active_symbols": list(rejection.active_symbols),
        "currency_exposure": dict(rejection.currency_exposure),
        "realized_day_r": rejection.realized_day_r,
    }


def _format_optional(value: object | None, *, suffix: str = "") -> str:
    return "disabled" if value is None else f"{value}{suffix}"


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
