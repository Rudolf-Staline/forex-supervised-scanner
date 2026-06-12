"""Read-only paper/demo performance analytics from local report artifacts.

This module is intentionally analytics-only. It reads existing JSON/JSONL/SQLite
paper artifacts, never imports MT5, never submits broker orders, and never
mutates runtime configuration.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

WARNING_MESSAGE = "Paper performance is not proof of profitability."

STATUS_READY = "PAPER_PERFORMANCE_READY"
STATUS_WARN = "PAPER_PERFORMANCE_WARN"
STATUS_NO_TRADES = "PAPER_PERFORMANCE_NO_TRADES"
STATUS_INCOMPLETE_DATA = "PAPER_PERFORMANCE_INCOMPLETE_DATA"
STATUS_BLOCKED_UNSAFE_FLAGS = "PAPER_PERFORMANCE_BLOCKED_UNSAFE_FLAGS"

DEFAULT_JSON = "paper_performance_summary.json"
DEFAULT_TXT = "paper_performance_report.txt"

INPUT_FILES = {
    "positions": "realtime_paper_positions.json",
    "command_center": "realtime_command_center_summary.json",
    "supervisor": "realtime_paper_supervisor_summary.json",
    "operator_dashboard": "operator_dashboard_summary.json",
    "heartbeat": "realtime_heartbeat.jsonl",
}

PENDING_STATUSES = {"pending", "pending_opportunity", "watchlisted", "new"}
OPEN_STATUSES = {"active", "open", "open_trade", "partially_closed", "partially_closed_trade"}
CLOSED_STATUSES = {"closed", "fully_closed", "fully_closed_trade", "take_profit", "stop_loss"}
CANCELLED_STATUSES = {"cancelled", "canceled", "cancelled_trade", "missed_trade", "expired_trade", "rejected", "invalidated"}
UNSAFE_FLAG_KEYS = {
    "live_trading_enabled",
    "live_execution_allowed",
    "broker_live_execution_allowed",
    "broker_order_submission_allowed",
    "mt5_order_execution_allowed",
    "order_send_called",
    "env_mutation_performed",
}


@dataclass(frozen=True)
class PaperPerformanceConfig:
    reports_dir: Path
    export_json: bool = False
    export_txt: bool = False
    strict: bool = False
    now: datetime | None = None
    stale_after_hours: float = 24.0


@dataclass
class PaperTradePerformance:
    trade_id: str
    status: str
    symbol: str | None = None
    timeframe: str | None = None
    strategy: str | None = None
    source: str | None = None
    realized_r: float | None = None
    realized_pnl: float | None = None
    partial_exit_count: int = 0
    stop_moved_count: int = 0
    breakeven_event_count: int = 0
    trailing_stop_event_count: int = 0
    opened_at: datetime | None = None
    closed_at: datetime | None = None

    @property
    def time_in_trade_seconds(self) -> float | None:
        if self.opened_at and self.closed_at and self.closed_at >= self.opened_at:
            return (self.closed_at - self.opened_at).total_seconds()
        return None


@dataclass
class PaperPerformanceSummary:
    generated_at: str
    reports_dir: str
    status: str
    input_files: dict[str, str]
    missing_input_files: list[str]
    stale_input_files: list[str]
    data_completeness_score: float
    total_paper_trades: int
    pending_count: int
    open_count: int
    closed_count: int
    cancelled_count: int
    win_count: int
    loss_count: int
    breakeven_count: int
    win_rate: float | None
    realized_r_total: float | None
    average_r: float | None
    best_r: float | None
    worst_r: float | None
    realized_pnl_total: float | None
    average_realized_pnl: float | None
    max_drawdown: float | None
    partial_exit_count: int
    stop_moved_count: int
    breakeven_event_count: int
    trailing_stop_event_count: int
    average_time_in_trade_seconds: float | None
    symbols_traded: list[str]
    timeframe_summary: dict[str, int]
    strategy_summary: dict[str, int]
    warnings: list[str]
    blocking_reasons: list[str]
    safety_flags: dict[str, Any]
    output_paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class PaperPerformanceService:
    """Compute paper/demo performance from local artifacts only."""

    def __init__(self, config: PaperPerformanceConfig) -> None:
        self.config = config
        self.reports_dir = config.reports_dir
        self.now = config.now or datetime.now(timezone.utc)

    def build_summary(self) -> PaperPerformanceSummary:
        loaded, missing, stale, warnings = self._load_inputs()
        safety_flags, blocking = self._collect_safety_flags(loaded)
        trades = self._collect_trades(loaded, warnings)
        warnings.extend(self._collect_source_warnings(loaded))
        blocking.extend(self._collect_source_blocking(loaded))

        if self.config.strict and missing:
            warnings.append("strict mode requires all expected paper performance input files")
        if not trades and not missing:
            warnings.append("input artifacts were found but no paper trades/orders were available")
        if missing:
            warnings.append("missing paper performance input files: " + ", ".join(missing))
        if stale:
            warnings.append("stale paper performance input files: " + ", ".join(stale))

        unsafe = [key for key, value in safety_flags.items() if key in UNSAFE_FLAG_KEYS and value is True]
        if unsafe:
            blocking.append("unsafe safety flags detected: " + ", ".join(sorted(unsafe)))

        summary = self._summarize(trades, loaded, missing, stale, warnings, blocking, safety_flags)
        if self.config.export_json or self.config.export_txt:
            self.export(summary)
        return summary

    def export(self, summary: PaperPerformanceSummary) -> dict[str, Path]:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}
        if self.config.export_json:
            path = self.reports_dir / DEFAULT_JSON
            summary.output_paths["json"] = str(path)
            path.write_text(json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
            paths["json"] = path
        if self.config.export_txt:
            path = self.reports_dir / DEFAULT_TXT
            summary.output_paths["txt"] = str(path)
            path.write_text(render_text_report(summary), encoding="utf-8")
            paths["txt"] = path
        if paths and self.config.export_json and "json" in paths:
            # Re-write JSON after txt path is known.
            paths["json"].write_text(json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return paths

    def _load_inputs(self) -> tuple[dict[str, Any], list[str], list[str], list[str]]:
        loaded: dict[str, Any] = {}
        missing: list[str] = []
        stale: list[str] = []
        warnings: list[str] = []
        for key, name in INPUT_FILES.items():
            path = self.reports_dir / name
            if not path.exists():
                missing.append(name)
                continue
            try:
                loaded[key] = _read_jsonl(path) if path.suffix == ".jsonl" else json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                warnings.append(f"could not read {name}: {exc}")
                loaded[key] = None
            ts = _latest_timestamp(loaded.get(key))
            if ts and (self.now - ts).total_seconds() > self.config.stale_after_hours * 3600:
                stale.append(name)
        db_trades, db_warnings = _load_sqlite_paper_orders(self.reports_dir)
        if db_trades:
            loaded["paper_order_store"] = db_trades
        warnings.extend(db_warnings)
        return loaded, missing, stale, warnings

    def _collect_trades(self, loaded: dict[str, Any], warnings: list[str]) -> list[PaperTradePerformance]:
        raw: list[Any] = []
        positions = loaded.get("positions")
        if isinstance(positions, dict):
            raw.extend(_as_list(positions.get("orders")))
            raw.extend(_as_list(positions.get("positions")))
            raw.extend(_as_list(positions.get("trades")))
            raw.extend(_as_list(positions.get("updates")))
            if not raw and any(k in positions for k in ("positions_seen", "pending_orders_seen", "positions_closed")):
                # Summary-only artifact: keep completeness warnings but do not invent trades.
                warnings.append("realtime_paper_positions.json contains aggregate counts but no order/trade records")
        raw.extend(_as_list(loaded.get("paper_order_store")))
        # Command center/supervisor may include local paper orders in some versions.
        for key in ("command_center", "supervisor"):
            item = loaded.get(key)
            if isinstance(item, dict):
                raw.extend(_as_list(item.get("paper_orders")))
                raw.extend(_as_list(item.get("orders")))
                raw.extend(_as_list(item.get("trades")))
        trades: dict[str, PaperTradePerformance] = {}
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            trade = _trade_from_record(item, index)
            if trade.trade_id in trades:
                trades[trade.trade_id] = _merge_trade(trades[trade.trade_id], trade)
            else:
                trades[trade.trade_id] = trade
        return list(trades.values())

    def _summarize(
        self,
        trades: list[PaperTradePerformance],
        loaded: dict[str, Any],
        missing: list[str],
        stale: list[str],
        warnings: list[str],
        blocking: list[str],
        safety_flags: dict[str, Any],
    ) -> PaperPerformanceSummary:
        pending = sum(_status_bucket(t.status) == "pending" for t in trades)
        open_ = sum(_status_bucket(t.status) == "open" for t in trades)
        closed = sum(_status_bucket(t.status) == "closed" for t in trades)
        cancelled = sum(_status_bucket(t.status) == "cancelled" for t in trades)
        closed_r = [t.realized_r for t in trades if _status_bucket(t.status) == "closed" and t.realized_r is not None]
        pnl = [t.realized_pnl for t in trades if _status_bucket(t.status) == "closed" and t.realized_pnl is not None]
        win = sum(1 for r in closed_r if r > 0)
        loss = sum(1 for r in closed_r if r < 0)
        breakeven = sum(1 for r in closed_r if r == 0)
        time_values = [t.time_in_trade_seconds for t in trades if t.time_in_trade_seconds is not None]
        equity_curve = _extract_equity_curve(loaded)
        completeness = _completeness_score(len(INPUT_FILES), len(missing), len(stale), trades, warnings)
        status = self._status(trades, missing, stale, warnings, blocking)
        input_files = {name: str(self.reports_dir / name) for name in INPUT_FILES.values() if (self.reports_dir / name).exists()}
        if "paper_order_store" in loaded:
            input_files["paper_order_store"] = "existing local SQLite paper_orders table"
        return PaperPerformanceSummary(
            generated_at=self.now.isoformat(), reports_dir=str(self.reports_dir), status=status, input_files=input_files,
            missing_input_files=missing, stale_input_files=stale, data_completeness_score=completeness,
            total_paper_trades=len(trades), pending_count=pending, open_count=open_, closed_count=closed, cancelled_count=cancelled,
            win_count=win, loss_count=loss, breakeven_count=breakeven, win_rate=_round(win / len(closed_r)) if closed_r else None,
            realized_r_total=_round(sum(closed_r)) if closed_r else None, average_r=_round(sum(closed_r) / len(closed_r)) if closed_r else None,
            best_r=max(closed_r) if closed_r else None, worst_r=min(closed_r) if closed_r else None,
            realized_pnl_total=_round(sum(pnl)) if pnl else None, average_realized_pnl=_round(sum(pnl) / len(pnl)) if pnl else None,
            max_drawdown=_max_drawdown(equity_curve), partial_exit_count=sum(t.partial_exit_count for t in trades),
            stop_moved_count=sum(t.stop_moved_count for t in trades), breakeven_event_count=sum(t.breakeven_event_count for t in trades),
            trailing_stop_event_count=sum(t.trailing_stop_event_count for t in trades),
            average_time_in_trade_seconds=_round(sum(time_values) / len(time_values)) if time_values else None,
            symbols_traded=sorted({t.symbol for t in trades if t.symbol}), timeframe_summary=dict(Counter(t.timeframe for t in trades if t.timeframe)),
            strategy_summary=dict(Counter((t.strategy or t.source) for t in trades if (t.strategy or t.source))),
            warnings=sorted(set(warnings)), blocking_reasons=sorted(set(blocking)), safety_flags=safety_flags,
        )

    def _status(self, trades: list[PaperTradePerformance], missing: list[str], stale: list[str], warnings: list[str], blocking: list[str]) -> str:
        if any("unsafe" in reason.lower() or "unsafe flag" in reason.lower() for reason in blocking):
            return STATUS_BLOCKED_UNSAFE_FLAGS
        if blocking:
            return STATUS_INCOMPLETE_DATA
        if self.config.strict and (missing or stale or warnings):
            return STATUS_INCOMPLETE_DATA
        if not trades:
            return STATUS_INCOMPLETE_DATA if missing else STATUS_NO_TRADES
        if missing or stale or warnings:
            return STATUS_WARN
        return STATUS_READY

    def _collect_safety_flags(self, loaded: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        flags: dict[str, Any] = {
            "read_only_analytics": True,
            "paper_demo_only": True,
            "live_trading_enabled": False,
            "live_execution_allowed": False,
            "broker_live_execution_allowed": False,
            "broker_order_submission_allowed": False,
            "mt5_required": False,
            "mt5_order_execution_allowed": False,
            "order_send_called": False,
            "env_mutation_performed": False,
        }
        for value in loaded.values():
            for source_flags in _find_dicts_by_key(value, "safety_flags"):
                flags.update(source_flags)
        blocking = [f"source report indicates unsafe flag {key}=true" for key, value in flags.items() if key in UNSAFE_FLAG_KEYS and value is True]
        return flags, blocking

    def _collect_source_warnings(self, loaded: dict[str, Any]) -> list[str]:
        out: list[str] = []
        for value in loaded.values():
            for item in _find_lists_by_key(value, "warnings"):
                out.extend(str(x) for x in item)
        return out

    def _collect_source_blocking(self, loaded: dict[str, Any]) -> list[str]:
        out: list[str] = []
        for value in loaded.values():
            for key in ("blocking_reasons", "blockers"):
                for item in _find_lists_by_key(value, key):
                    out.extend(str(x) for x in item)
        return out


def render_text_report(summary: PaperPerformanceSummary) -> str:
    data = summary.to_dict()
    lines = [
        "paper_performance=diagnostic_only",
        "paper_demo_only=true",
        "live_trading_authorized=false",
        "mt5_required=false",
        f"status={summary.status}",
        f"generated_at={summary.generated_at}",
        f"reports_dir={summary.reports_dir}",
        f"total_paper_trades={summary.total_paper_trades}",
        f"closed_count={summary.closed_count}",
        f"win_rate={summary.win_rate}",
        f"realized_r_total={summary.realized_r_total}",
        f"realized_pnl_total={summary.realized_pnl_total}",
        f"symbols_traded={','.join(summary.symbols_traded)}",
        WARNING_MESSAGE,
    ]
    for warning in summary.warnings:
        lines.append(f"warning={warning}")
    for reason in summary.blocking_reasons:
        lines.append(f"blocking_reason={reason}")
    lines.append("json=" + json.dumps(data, sort_keys=True))
    return "\n".join(lines) + "\n"


def export_summary(report: dict[str, Any], reports_dir: Path, *, export_csv: bool = False, export_txt: bool = False) -> tuple[Path, Path | None]:
    """Compatibility exporter for the earlier paper-performance report API."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / DEFAULT_JSON
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    second: Path | None = None
    if export_txt:
        second = reports_dir / DEFAULT_TXT
        second.write_text("\n".join(f"{k}={v}" for k, v in report.items()) + "\n", encoding="utf-8")
    elif export_csv:
        second = reports_dir / "paper_performance_report.csv"
        with second.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["metric", "value"])
            writer.writeheader()
            for key, value in report.items():
                writer.writerow({"metric": key, "value": json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value})
    return json_path, second


def load_records(reports_dir: Path) -> list[dict[str, Any]]:
    """Compatibility loader for legacy score/cost records."""
    records: list[dict[str, Any]] = []
    for name in ("forward_test_summary.json", "paper_fill_summary.json"):
        records.extend(_as_list(_safe_json(reports_dir / name)))
    for name in ("forward_test_paper.csv", "paper_fill_report.csv"):
        path = reports_dir / name
        if path.exists():
            with path.open("r", encoding="utf-8", newline="") as handle:
                records.extend(csv.DictReader(handle))
    journal = reports_dir / "signal_journal.jsonl"
    if journal.exists():
        records.extend(_read_jsonl(journal))
    return [r for r in records if isinstance(r, dict)]


def build_paper_performance_report(records: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    """Compatibility aggregate retained for existing report tests."""
    asset_class = str(kwargs.get("asset_class", "all"))
    symbol = kwargs.get("symbol")
    session = kwargs.get("session")
    records = [r for r in records if (asset_class == "all" or str(r.get("asset_class", "")).lower() == asset_class.lower()) and (not symbol or str(r.get("symbol", "")).upper() == str(symbol).upper()) and (not session or str(r.get("session", "")).lower() == str(session).lower())]
    simulated = sum(1 for r in records if "paper" in json.dumps(r).lower() or "simulat" in json.dumps(r).lower())
    rejected = [r for r in records if any(x in str(r.get("status", "")).lower() for x in ("reject", "block"))]
    scores = [_to_float(r.get("score") or r.get("final_score")) for r in records]
    avg_score = _avg([x for x in scores if x is not None])
    report = {
        "total_paper_records": len(records), "simulated_orders": simulated, "rejected_paper_orders": len(rejected),
        "average_score": avg_score, "average_risk_reward": _avg(_numbers(records, "risk_reward")),
        "average_spread_atr": _avg(_numbers(records, "spread_atr")), "average_slippage": _avg(_numbers(records, "slippage", "paper_slippage_points")),
        "average_spread_cost": _avg(_numbers(records, "spread_cost", "paper_spread_cost")),
        "average_commission_estimate": _avg(_numbers(records, "commission", "paper_commission_estimate")),
        "safety_warning": WARNING_MESSAGE,
    }
    report["execution_cost_impact"] = _round(report["average_slippage"] + report["average_spread_cost"] + report["average_commission_estimate"])
    report["paper_quality_status"] = "BLOCKED" if simulated == 0 else "HEALTHY"
    report["best_symbols"] = _rank(records, "symbol", True)
    report["weakest_symbols"] = _rank(records, "symbol", False)
    report["best_sessions"] = _rank(records, "session", True)
    report["weakest_sessions"] = _rank(records, "session", False)
    report["rejection_reasons_top"] = []
    report["recommendations"] = ["Use the issue #97 analytics CLI for paper/demo trade performance diagnostics."]
    return report


def _trade_from_record(record: dict[str, Any], index: int) -> PaperTradePerformance:
    request = record.get("request") if isinstance(record.get("request"), dict) else {}
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    status = str(record.get("after_status") or record.get("status") or payload.get("status") or "unknown")
    trade = PaperTradePerformance(
        trade_id=str(record.get("order_id") or record.get("trade_id") or record.get("id") or f"record-{index}"),
        status=status,
        symbol=_first_str(record, request, payload, keys=("symbol",)),
        timeframe=_first_str(record, request, payload, keys=("timeframe", "time_frame")),
        strategy=_first_str(record, request, payload, keys=("strategy", "setup_subtype", "setup", "source_strategy")),
        source=_first_str(record, request, payload, keys=("source", "provider")),
        realized_r=_first_float(record, payload, keys=("realized_r", "r", "r_multiple")),
        realized_pnl=_first_float(record, payload, keys=("realized_pnl", "pnl", "profit", "profit_loss")),
        partial_exit_count=max(len(_as_list(record.get("partial_exits"))), _event_count(record, {"trade_partially_closed", "partial_exit"})),
        stop_moved_count=max(len(_as_list(record.get("stop_movements"))), _event_count(record, {"stop_moved"})),
        breakeven_event_count=_text_event_count(record, "breakeven"),
        trailing_stop_event_count=_text_event_count(record, "trailing"),
        opened_at=_first_dt(record, payload, keys=("entry_timestamp", "activated_at", "opened_at")),
        closed_at=_first_dt(record, payload, keys=("closed_at", "exit_timestamp", "completed_at")),
    )
    if trade.realized_r is None and trade.realized_pnl is not None:
        trade.realized_r = 0.0 if trade.realized_pnl == 0 else None
    return trade


def _merge_trade(left: PaperTradePerformance, right: PaperTradePerformance) -> PaperTradePerformance:
    for name in ("status", "symbol", "timeframe", "strategy", "source", "realized_r", "realized_pnl", "opened_at", "closed_at"):
        if getattr(left, name) in (None, "", "unknown") and getattr(right, name) not in (None, ""):
            setattr(left, name, getattr(right, name))
    left.partial_exit_count += right.partial_exit_count
    left.stop_moved_count += right.stop_moved_count
    left.breakeven_event_count += right.breakeven_event_count
    left.trailing_stop_event_count += right.trailing_stop_event_count
    return left


def _load_sqlite_paper_orders(reports_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    candidates = sorted({*reports_dir.glob("*.sqlite"), *reports_dir.glob("*.sqlite3"), *reports_dir.glob("*.db"), *(reports_dir.parent.glob("*.sqlite") if reports_dir.parent.exists() else [])})
    for path in candidates:
        try:
            with sqlite3.connect(path) as conn:
                names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "paper_orders" not in names:
                    continue
                for (payload,) in conn.execute("SELECT payload_json FROM paper_orders"):
                    records.append(json.loads(payload))
        except (sqlite3.Error, OSError, json.JSONDecodeError) as exc:
            warnings.append(f"could not inspect local paper order store {path.name}: {exc}")
    return records, warnings


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                out.append(value)
    return out


def _safe_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def _status_bucket(status: str) -> str:
    normalized = status.lower().replace("-", "_")
    if normalized in PENDING_STATUSES:
        return "pending"
    if normalized in OPEN_STATUSES:
        return "open"
    if normalized in CLOSED_STATUSES:
        return "closed"
    if normalized in CANCELLED_STATUSES or "cancel" in normalized or "invalid" in normalized or "reject" in normalized:
        return "cancelled"
    return "open" if "open" in normalized else "pending"


def _first_str(*dicts: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for d in dicts:
        for key in keys:
            value = d.get(key)
            if value not in (None, ""):
                return str(value)
    return None


def _first_float(*dicts: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for d in dicts:
        for key in keys:
            value = _to_float(d.get(key))
            if value is not None:
                return value
    return None


def _first_dt(*dicts: dict[str, Any], keys: tuple[str, ...]) -> datetime | None:
    for d in dicts:
        for key in keys:
            value = _parse_dt(d.get(key))
            if value:
                return value
    return None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _event_count(record: dict[str, Any], types: set[str]) -> int:
    count = 0
    for event in _as_list(record.get("events")) + _as_list(record.get("events_created")):
        if isinstance(event, dict) and str(event.get("event_type", "")).lower() in types:
            count += 1
    return count


def _text_event_count(record: dict[str, Any], token: str) -> int:
    text = json.dumps(record, default=str).lower()
    return text.count(token.lower())


def _find_dicts_by_key(value: Any, key: str) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if isinstance(value.get(key), dict):
            yield value[key]
        for child in value.values():
            yield from _find_dicts_by_key(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from _find_dicts_by_key(child, key)


def _find_lists_by_key(value: Any, key: str) -> Iterable[list[Any]]:
    if isinstance(value, dict):
        if isinstance(value.get(key), list):
            yield value[key]
        for child in value.values():
            yield from _find_lists_by_key(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from _find_lists_by_key(child, key)


def _latest_timestamp(value: Any) -> datetime | None:
    timestamps = []
    if isinstance(value, dict):
        for key in ("completed_at", "generated_at", "heartbeat_at", "timestamp", "started_at"):
            ts = _parse_dt(value.get(key))
            if ts:
                timestamps.append(ts)
        for child in value.values():
            ts = _latest_timestamp(child)
            if ts:
                timestamps.append(ts)
    elif isinstance(value, list):
        for child in value:
            ts = _latest_timestamp(child)
            if ts:
                timestamps.append(ts)
    return max(timestamps) if timestamps else None


def _extract_equity_curve(loaded: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for value in loaded.values():
        for key in ("equity_curve", "paper_equity_curve"):
            for found in _find_lists_by_key(value, key):
                for item in found:
                    if isinstance(item, dict):
                        number = _to_float(item.get("equity") or item.get("value"))
                    else:
                        number = _to_float(item)
                    if number is not None:
                        values.append(number)
    return values


def _max_drawdown(equity: list[float]) -> float | None:
    if not equity:
        return None
    peak = equity[0]
    dd = 0.0
    for value in equity:
        peak = max(peak, value)
        dd = min(dd, value - peak)
    return _round(abs(dd))


def _completeness_score(expected: int, missing: int, stale: int, trades: list[PaperTradePerformance], warnings: list[str]) -> float:
    score = 1.0 - (missing / max(expected, 1) * 0.5) - (stale / max(expected, 1) * 0.25)
    if not trades:
        score -= 0.2
    if warnings:
        score -= min(0.2, len(warnings) * 0.03)
    return max(0.0, min(1.0, _round(score)))


def _collect_numbers(records: list[PaperTradePerformance], attr: str) -> list[float]:
    return [getattr(t, attr) for t in records if getattr(t, attr) is not None]


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: float) -> float:
    return round(float(value), 8)


def _avg(values: Iterable[float]) -> float:
    clean = [v for v in values if v is not None]
    return _round(sum(clean) / len(clean)) if clean else 0.0


def _numbers(records: list[dict[str, Any]], *keys: str) -> list[float]:
    out = []
    for record in records:
        for key in keys:
            value = _to_float(record.get(key))
            if value is not None:
                out.append(value)
                break
    return out


def _rank(records: list[dict[str, Any]], key: str, reverse: bool) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for record in records:
        grouped[str(record.get(key) or "unknown")].append(_to_float(record.get("score") or record.get("final_score")) or 0.0)
    rows = sorted(((k, sum(v) / len(v), len(v)) for k, v in grouped.items()), key=lambda x: x[1], reverse=reverse)
    return [{key: k, "average_score": _round(avg), "count": count} for k, avg, count in rows[:10]]
