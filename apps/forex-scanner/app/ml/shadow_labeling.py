"""Counterfactual historical labels for scanner candidates rejected at decision time.

The labeler replays stored entry, stop, and target levels through the same
quote-aware execution simulator used by historical backtests. It never sends
orders and never overwrites observed paper outcomes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pandas as pd

from app.backtest.execution import simulate_execution
from app.backtest.outcomes import evaluate_path
from app.config.settings import AppSettings
from app.core.types import DirectionBias, RiskPlan, TIMEFRAME_MINUTES, Timeframe, TradingStyle
from app.data.providers import MarketDataProvider

SHADOW_SCHEMA_VERSION = "shadow_decision_labels.v1"
ShadowScope = Literal["rejected_unlabeled", "all_unlabeled"]


@dataclass(frozen=True)
class ShadowLabelConfig:
    """Controls for one deterministic shadow-labeling run."""

    scope: ShadowScope = "rejected_unlabeled"
    not_activated_return_r: float = 0.0
    minimum_future_bars: int = 1
    max_candidates: int | None = None

    def __post_init__(self) -> None:
        if self.scope not in {"rejected_unlabeled", "all_unlabeled"}:
            raise ValueError("unsupported shadow scope")
        if self.minimum_future_bars < 1:
            raise ValueError("minimum_future_bars must be positive")
        if self.max_candidates is not None and self.max_candidates < 1:
            raise ValueError("max_candidates must be positive when provided")


@dataclass(frozen=True)
class ShadowLabelReport:
    labels: list[dict[str, object]]
    failures: list[dict[str, object]]
    summary: dict[str, object]
    manifest: dict[str, object]


@dataclass(frozen=True)
class ShadowMergeResult:
    rows: list[dict[str, object]]
    summary: dict[str, object]


def build_shadow_labels(
    rows: list[dict[str, object]],
    settings: AppSettings,
    provider: MarketDataProvider,
    *,
    config: ShadowLabelConfig | None = None,
) -> ShadowLabelReport:
    """Build counterfactual labels for eligible decisions.

    Candidates are grouped by symbol and trigger timeframe so one provider fetch
    can serve many decisions. Provider or row failures remain explicit audit rows
    and do not abort the complete run.
    """

    rules = config or ShadowLabelConfig()
    selected, failures = _select_candidates(rows, rules)
    groups: dict[tuple[str, Timeframe], list[dict[str, object]]] = {}
    for row in selected:
        try:
            style = TradingStyle(str(row.get("style") or ""))
            timeframe = settings.styles[style].trigger_timeframe
        except Exception as exc:  # noqa: BLE001 - retain row-level audit detail
            failures.append(_failure(row, "invalid_style", str(exc)))
            continue
        groups.setdefault((str(row.get("symbol") or ""), timeframe), []).append(row)

    labels: list[dict[str, object]] = []
    for (symbol, timeframe), candidates in sorted(
        groups.items(), key=lambda item: (item[0][0], item[0][1].value)
    ):
        try:
            frame = _fetch_group_frame(
                symbol=symbol,
                timeframe=timeframe,
                candidates=candidates,
                settings=settings,
                provider=provider,
            )
        except Exception as exc:  # noqa: BLE001 - record provider failures per candidate
            for row in candidates:
                failures.append(_failure(row, "market_data_unavailable", str(exc)))
            continue

        for row in candidates:
            try:
                labels.append(
                    _label_one(
                        row=row,
                        frame=frame,
                        timeframe=timeframe,
                        settings=settings,
                        config=rules,
                        provider_name=str(frame.attrs.get("provider", provider.name)),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - keep the run bounded and auditable
                failures.append(_failure(row, "shadow_simulation_error", str(exc)))

    labels.sort(key=lambda item: (str(item.get("decision_timestamp", "")), str(item["decision_id"])))
    failures.sort(
        key=lambda item: (str(item.get("decision_timestamp", "")), str(item.get("decision_id", "")))
    )
    summary = _build_summary(rows, selected, labels, failures, rules, provider)
    manifest = {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider.name,
        "config": asdict(rules),
        "summary": summary,
        "label_contract": {
            "counterfactual": True,
            "observed_outcomes_overwrite_shadow": False,
            "not_activated_return_r": rules.not_activated_return_r,
            "entry_exit_model": "app.backtest.execution.simulate_execution",
            "intrabar_policy": "stop_first_and_flag_ambiguous",
        },
    }
    return ShadowLabelReport(labels=labels, failures=failures, summary=summary, manifest=manifest)


def merge_shadow_labels(
    decision_rows: list[dict[str, object]],
    shadow_rows: list[dict[str, object]],
) -> ShadowMergeResult:
    """Merge shadow labels into copies of unlabeled decisions.

    Observed return labels always win. Inputs are never mutated.
    """

    labels_by_id = {
        str(label.get("decision_id")): label
        for label in shadow_rows
        if label.get("shadow_status") in {"labeled", "not_activated"}
    }
    merged: list[dict[str, object]] = []
    applied = 0
    preserved_observed = 0
    missing_decisions = set(labels_by_id)

    for source in decision_rows:
        row = _deep_copy(source)
        decision_id = str(row.get("decision_id") or "")
        label = labels_by_id.get(decision_id)
        if label is None:
            merged.append(row)
            continue
        missing_decisions.discard(decision_id)
        if bool(row.get("return_label_available")):
            preserved_observed += 1
            merged.append(row)
            continue

        row.update(
            {
                "order_status": (
                    "shadow_not_activated"
                    if label.get("shadow_status") == "not_activated"
                    else "shadow_closed"
                ),
                "activated": bool(label.get("activated")),
                "activation_label_available": True,
                "return_label_available": True,
                "realized_r": label.get("realized_r"),
                "realized_pnl": None,
                "target_positive_r": label.get("target_positive_r"),
                "target_non_negative_r": label.get("target_non_negative_r"),
                "outcome_status": label.get("outcome_status"),
                "close_reason": label.get("close_reason"),
                "mae": label.get("mae"),
                "mfe": label.get("mfe"),
                "bars_to_activation": label.get("bars_to_activation"),
                "time_in_trade_minutes": label.get("time_in_trade_minutes"),
                "label_source": "shadow_historical",
                "label_timestamp": label.get("label_timestamp"),
                "shadow_label_id": label.get("shadow_label_id"),
                "shadow_counterfactual": True,
                "shadow_execution_model": label.get("execution_model"),
                "shadow_intrabar_ambiguous": label.get("intrabar_ambiguous"),
            }
        )
        sources = [str(item) for item in row.get("source_records", []) if item]
        row["source_records"] = sorted(set([*sources, "shadow_historical"]))
        applied += 1
        merged.append(row)

    merged.sort(key=lambda row: (str(row.get("decision_timestamp", "")), str(row.get("decision_id", ""))))
    return ShadowMergeResult(
        rows=merged,
        summary={
            "decision_rows": len(decision_rows),
            "shadow_rows": len(shadow_rows),
            "shadow_labels_applied": applied,
            "observed_labels_preserved": preserved_observed,
            "shadow_labels_without_decision": len(missing_decisions),
            "trainable_rows_after_merge": sum(
                bool(row.get("is_trainable_candidate")) and bool(row.get("return_label_available"))
                for row in merged
            ),
        },
    )


def write_shadow_label_report(report: ShadowLabelReport, output_dir: Path) -> dict[str, Path]:
    """Write labels and audit artifacts without touching source datasets."""

    output_dir.mkdir(parents=True, exist_ok=True)
    labels_path = output_dir / "shadow_decision_labels.jsonl"
    failures_path = output_dir / "shadow_label_failures.jsonl"
    summary_path = output_dir / "shadow_label_summary.json"
    text_path = output_dir / "shadow_label_summary.txt"
    manifest_path = output_dir / "shadow_label_manifest.json"
    _write_jsonl(labels_path, report.labels)
    _write_jsonl(failures_path, report.failures)
    summary_path.write_text(json.dumps(report.summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    text_path.write_text(_summary_text(report.summary), encoding="utf-8")
    manifest_path.write_text(json.dumps(report.manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "labels": labels_path,
        "failures": failures_path,
        "summary_json": summary_path,
        "summary_txt": text_path,
        "manifest": manifest_path,
    }


def write_shadow_augmented_dataset(result: ShadowMergeResult, output_dir: Path) -> dict[str, Path]:
    """Write copied full/training datasets under new names."""

    output_dir.mkdir(parents=True, exist_ok=True)
    full_path = output_dir / "decision_calibration_with_shadow.jsonl"
    training_path = output_dir / "decision_training_with_shadow.jsonl"
    summary_path = output_dir / "shadow_merge_summary.json"
    _write_jsonl(full_path, result.rows)
    trainable = [
        row
        for row in result.rows
        if bool(row.get("is_trainable_candidate")) and bool(row.get("return_label_available"))
    ]
    _write_jsonl(training_path, trainable)
    summary_path.write_text(json.dumps(result.summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "augmented_dataset": full_path,
        "augmented_training_dataset": training_path,
        "merge_summary": summary_path,
    }


def load_jsonl(path: Path) -> list[dict[str, object]]:
    """Load object-valued JSONL rows, rejecting malformed input loudly."""

    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(payload)
    return rows


def _select_candidates(
    rows: list[dict[str, object]], config: ShadowLabelConfig
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    selected: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    ordered = sorted(
        rows,
        key=lambda row: (str(row.get("decision_timestamp", "")), str(row.get("decision_id", ""))),
    )
    for row in ordered:
        if config.max_candidates is not None and len(selected) >= config.max_candidates:
            break
        if not bool(row.get("is_trainable_candidate")) or bool(row.get("return_label_available")):
            continue
        if config.scope == "rejected_unlabeled" and not bool(row.get("is_rejected")):
            continue
        reason = _candidate_level_error(row)
        if reason is not None:
            failures.append(_failure(row, reason, reason.replace("_", " ")))
            continue
        selected.append(row)
    return selected, failures


def _candidate_level_error(row: dict[str, object]) -> str | None:
    if not str(row.get("decision_id") or ""):
        return "missing_decision_id"
    if _safe_timestamp(row.get("decision_timestamp")) is None:
        return "invalid_decision_timestamp"
    if not str(row.get("symbol") or ""):
        return "missing_symbol"
    direction = str(row.get("direction") or "")
    if direction not in {"long", "short"}:
        return "invalid_direction"
    entry = _number(row.get("entry"))
    stop = _number(row.get("stop_loss"))
    target = _number(row.get("take_profit"))
    if entry is None or stop is None or target is None:
        return "missing_executable_levels"
    if min(entry, stop, target) <= 0.0:
        return "non_positive_executable_levels"
    if direction == "long" and not (stop < entry < target):
        return "invalid_long_levels"
    if direction == "short" and not (target < entry < stop):
        return "invalid_short_levels"
    return None


def _fetch_group_frame(
    *,
    symbol: str,
    timeframe: Timeframe,
    candidates: list[dict[str, object]],
    settings: AppSettings,
    provider: MarketDataProvider,
) -> pd.DataFrame:
    timestamps = [_timestamp(row.get("decision_timestamp")) for row in candidates]
    styles = [TradingStyle(str(row.get("style") or "")) for row in candidates]
    max_hold = max(settings.styles[style].max_hold_bars for style in styles)
    minutes = TIMEFRAME_MINUTES[timeframe]
    warmup_bars = max(220, settings.provider.max_bars)
    start = min(timestamps) - timedelta(minutes=minutes * warmup_bars)
    end = max(timestamps) + timedelta(minutes=minutes * (max_hold + 2))
    frame = provider.get_ohlcv(symbol, timeframe, start, end)
    if frame.empty:
        raise ValueError("provider returned no candles")
    normalized = frame.copy()
    normalized.index = pd.to_datetime(frame.index, utc=True)
    normalized = normalized.sort_index()
    normalized.attrs.update(frame.attrs)
    return normalized


def _label_one(
    *,
    row: dict[str, object],
    frame: pd.DataFrame,
    timeframe: Timeframe,
    settings: AppSettings,
    config: ShadowLabelConfig,
    provider_name: str,
) -> dict[str, object]:
    style = TradingStyle(str(row.get("style") or ""))
    style_settings = settings.styles[style]
    decision_time = _timestamp(row.get("decision_timestamp"))
    future = frame.loc[frame.index > pd.Timestamp(decision_time)].head(style_settings.max_hold_bars)
    if len(future) < config.minimum_future_bars:
        raise ValueError(
            f"insufficient future bars: have={len(future)} need={config.minimum_future_bars}"
        )
    direction = DirectionBias(str(row.get("direction")))
    risk_plan = _risk_plan(row, direction)
    execution = simulate_execution(
        symbol=str(row.get("symbol")),
        direction=direction,
        risk_plan=risk_plan,
        future=future,
        signal_time=decision_time,
        cost_pips=style_settings.transaction_cost_pips,
        spread_price=_number(row.get("spread")),
    )
    base = {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "shadow_label_id": _shadow_id(row, config),
        "decision_id": str(row.get("decision_id")),
        "decision_timestamp": decision_time.isoformat(),
        "feature_snapshot_hash": row.get("feature_snapshot_hash"),
        "symbol": str(row.get("symbol")),
        "normalized_symbol": str(row.get("normalized_symbol") or ""),
        "style": style.value,
        "setup_family": str(row.get("setup_family") or ""),
        "setup_subtype": str(row.get("setup_subtype") or ""),
        "direction": direction.value,
        "scanner_status": row.get("scanner_status"),
        "scanner_approved": bool(row.get("scanner_approved")),
        "counterfactual": True,
        "label_source": "shadow_historical",
        "provider": provider_name,
        "trigger_timeframe": timeframe.value,
        "max_hold_bars": style_settings.max_hold_bars,
        "candidate_final_score": _number(row.get("final_score")),
        "candidate_rejection_category": row.get("rejection_category"),
        "candidate_rejection_reason": row.get("rejection_reason"),
    }
    if execution is None:
        label_timestamp = _timestamp(future.index[-1])
        net_r = round(float(config.not_activated_return_r), 4)
        return {
            **base,
            "shadow_status": "not_activated",
            "activated": False,
            "activation_label_available": True,
            "return_label_available": True,
            "realized_r": net_r,
            "gross_r": net_r,
            "target_positive_r": net_r > 0.0,
            "target_non_negative_r": net_r >= 0.0,
            "outcome_status": "not_activated",
            "close_reason": "entry_not_reached",
            "label_timestamp": label_timestamp.isoformat(),
            "time_in_trade_minutes": round(
                max(0.0, (label_timestamp - decision_time).total_seconds() / 60.0), 4
            ),
            "bars_to_activation": None,
            "bars_in_trade": 0,
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
            "mae": 0.0,
            "mfe": 0.0,
            "cost_pips": 0.0,
            "execution_model": None,
            "intrabar_ambiguous": False,
        }
    path = evaluate_path(
        direction,
        risk_plan,
        execution.path_frame,
        execution.exit_reason,
        execution.net_r,
        bars_to_activation=execution.bars_to_activation,
    )
    label_timestamp = _timestamp(execution.exit_time)
    net_r = round(float(execution.net_r), 4)
    return {
        **base,
        "shadow_status": "labeled",
        "activated": True,
        "activation_label_available": True,
        "return_label_available": True,
        "realized_r": net_r,
        "gross_r": round(float(execution.gross_r), 4),
        "target_positive_r": net_r > 0.0,
        "target_non_negative_r": net_r >= 0.0,
        "outcome_status": path.outcome.value,
        "close_reason": execution.exit_reason,
        "label_timestamp": label_timestamp.isoformat(),
        "time_in_trade_minutes": round(
            max(0.0, (label_timestamp - decision_time).total_seconds() / 60.0), 4
        ),
        "bars_to_activation": path.bars_to_activation,
        "bars_in_trade": execution.exit_bar_count,
        "tp1_hit": path.tp1_hit,
        "tp2_hit": path.tp2_hit,
        "tp3_hit": path.tp3_hit,
        "mae": path.mae,
        "mfe": path.mfe,
        "cost_pips": execution.cost_pips,
        "execution_model": execution.execution_model,
        "intrabar_ambiguous": execution.intrabar_ambiguous,
    }


def _risk_plan(row: dict[str, object], direction: DirectionBias) -> RiskPlan:
    entry = _required_number(row.get("entry"), "entry")
    stop = _required_number(row.get("stop_loss"), "stop_loss")
    target = _required_number(row.get("take_profit"), "take_profit")
    risk = abs(entry - stop)
    if risk <= 0.0:
        raise ValueError("entry and stop_loss must differ")
    tp1 = _number(row.get("tp1"))
    tp2 = _number(row.get("tp2"))
    tp3 = _number(row.get("tp3"))
    if direction == DirectionBias.LONG:
        tp1 = tp1 if tp1 is not None and tp1 > entry else entry + risk
        tp2 = tp2 if tp2 is not None and tp2 > entry else entry + 1.5 * risk
        tp3 = tp3 if tp3 is not None and tp3 > entry else target
    else:
        tp1 = tp1 if tp1 is not None and tp1 < entry else entry - risk
        tp2 = tp2 if tp2 is not None and tp2 < entry else entry - 1.5 * risk
        tp3 = tp3 if tp3 is not None and tp3 < entry else target
    return RiskPlan(
        entry=entry,
        stop_loss=stop,
        take_profit=target,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        risk_reward=abs(target - entry) / risk,
        tp1_risk_reward=abs(tp1 - entry) / risk,
        tp2_risk_reward=abs(tp2 - entry) / risk,
        tp3_risk_reward=abs(tp3 - entry) / risk,
        stop_method="stored_decision",
        target_method="stored_decision",
        target_profile="balanced",
    )


def _build_summary(
    all_rows: list[dict[str, object]],
    selected: list[dict[str, object]],
    labels: list[dict[str, object]],
    failures: list[dict[str, object]],
    config: ShadowLabelConfig,
    provider: MarketDataProvider,
) -> dict[str, object]:
    status_counts: dict[str, int] = {}
    for label in labels:
        status = str(label.get("shadow_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "input_decisions": len(all_rows),
        "eligible_candidates": len(selected),
        "labels_written": len(labels),
        "failures": len(failures),
        "status_counts": dict(sorted(status_counts.items())),
        "positive_labels": sum(label.get("target_positive_r") is True for label in labels),
        "non_positive_labels": sum(label.get("target_positive_r") is False for label in labels),
        "activated_labels": sum(bool(label.get("activated")) for label in labels),
        "not_activated_labels": sum(label.get("shadow_status") == "not_activated" for label in labels),
        "intrabar_ambiguous_labels": sum(bool(label.get("intrabar_ambiguous")) for label in labels),
        "provider": provider.name,
        "scope": config.scope,
        "counterfactual": True,
        "source_dataset_mutated": False,
    }


def _failure(row: dict[str, object], reason: str, detail: str) -> dict[str, object]:
    return {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "decision_id": row.get("decision_id"),
        "decision_timestamp": row.get("decision_timestamp"),
        "symbol": row.get("symbol"),
        "normalized_symbol": row.get("normalized_symbol"),
        "style": row.get("style"),
        "setup_family": row.get("setup_family"),
        "setup_subtype": row.get("setup_subtype"),
        "shadow_status": "error" if reason == "shadow_simulation_error" else "skipped",
        "reason": reason,
        "detail": detail,
        "counterfactual": True,
    }


def _shadow_id(row: dict[str, object], config: ShadowLabelConfig) -> str:
    payload = {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "decision_id": row.get("decision_id"),
        "feature_snapshot_hash": row.get("feature_snapshot_hash"),
        "scope": config.scope,
        "not_activated_return_r": config.not_activated_return_r,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _summary_text(summary: dict[str, object]) -> str:
    counts = summary.get("status_counts") if isinstance(summary.get("status_counts"), dict) else {}
    lines = [
        "Shadow Decision Labels",
        "======================",
        f"input decisions      : {summary.get('input_decisions', 0)}",
        f"eligible candidates  : {summary.get('eligible_candidates', 0)}",
        f"labels written       : {summary.get('labels_written', 0)}",
        f"activated labels     : {summary.get('activated_labels', 0)}",
        f"not activated        : {summary.get('not_activated_labels', 0)}",
        f"positive labels      : {summary.get('positive_labels', 0)}",
        f"non-positive labels  : {summary.get('non_positive_labels', 0)}",
        f"failures             : {summary.get('failures', 0)}",
        f"provider             : {summary.get('provider', '')}",
        f"scope                : {summary.get('scope', '')}",
    ]
    for key, value in sorted(counts.items()):
        lines.append(f"status.{key:<13}: {value}")
    lines.extend(["", "Research-only counterfactual labels. Source decisions were not mutated.", ""])
    return "\n".join(lines)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _deep_copy(value: dict[str, object]) -> dict[str, object]:
    return json.loads(json.dumps(value))


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(result) else result


def _required_number(value: object, name: str) -> float:
    result = _number(value)
    if result is None:
        raise ValueError(f"{name} is required")
    return result


def _safe_timestamp(value: object) -> datetime | None:
    try:
        return _timestamp(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value: object) -> datetime:
    if isinstance(value, pd.Timestamp):
        parsed = value.to_pydatetime()
    elif isinstance(value, datetime):
        parsed = value
    elif value:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        raise ValueError("timestamp is required")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
