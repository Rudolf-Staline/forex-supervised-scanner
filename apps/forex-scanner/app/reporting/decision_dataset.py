"""Build a leakage-aware calibration dataset from persisted scanner decisions.

The builder treats scanner-time fields as features and lifecycle fields that occur
after the decision as labels. It combines four local evidence sources:

* ``scan_results``: full accepted and rejected opportunity snapshots;
* ``signal_journal.jsonl``: demo-bot cycle identifiers and acceptance decisions;
* ``rejected_signals``: structured bot-level rejection diagnostics;
* ``paper_orders``: activation and realized paper outcomes.

Matching is deterministic and conservative. Exact identifiers are preferred.
Temporal fallback matching is accepted only when the nearest compatible decision
is unique inside the configured window. Ambiguous matches stay unlabeled and are
exported for manual inspection.

Paper/research only. Nothing here sends orders or changes runtime settings.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "decision_calibration_dataset.v1"

FEATURE_COLUMNS = [
    "decision_timestamp",
    "normalized_symbol",
    "style",
    "setup_family",
    "setup_subtype",
    "direction",
    "scanner_status",
    "scanner_approved",
    "confidence",
    "regime",
    "htf_regime",
    "entry_regime",
    "trigger_regime",
    "session",
    "provider",
    "technical_score",
    "execution_score",
    "context_score",
    "empirical_score",
    "final_score",
    "score_band",
    "pattern_score",
    "activation_quality",
    "invalidation_quality",
    "entry",
    "stop_loss",
    "take_profit",
    "tp1",
    "tp2",
    "tp3",
    "risk_reward",
    "required_min_rr",
    "spread",
    "atr",
    "spread_atr_ratio",
    "data_quality_score",
    "score_components",
    "key_level_distances",
    "detected_patterns",
    "gate_breakdown",
    "failed_gates",
    "rejection_category",
    "rejection_reason",
    "missing_conditions",
    "adaptive_threshold_enabled",
    "base_min_score",
    "adaptive_min_score",
    "effective_min_score",
]

LABEL_COLUMNS = [
    "bot_decision",
    "bot_rejection_reasons",
    "order_status",
    "activated",
    "activation_label_available",
    "return_label_available",
    "realized_r",
    "realized_pnl",
    "target_positive_r",
    "target_non_negative_r",
    "outcome_status",
    "close_reason",
    "mae",
    "mfe",
    "bars_to_activation",
    "time_in_trade_minutes",
    "label_source",
]

_TERMINAL_ORDER_STATUSES = {
    "fully_closed_trade",
    "closed",
    "missed_trade",
    "cancelled_trade",
    "canceled",
    "expired_trade",
    "rejected",
}

_WIN_SCAN_OUTCOMES = {"win_clean", "win_messy", "partial_win", "win"}
_LOSS_SCAN_OUTCOMES = {"loss_clean", "loss_fast", "loss"}
_BREAKEVEN_SCAN_OUTCOMES = {"breakeven", "break_even", "flat"}


@dataclass(frozen=True)
class DecisionDatasetConfig:
    """Matching and inclusion controls for one dataset build."""

    match_window_seconds: int = 300
    include_no_trade: bool = True
    include_supplemental_journal_rows: bool = True

    def __post_init__(self) -> None:
        if self.match_window_seconds < 0:
            raise ValueError("match_window_seconds must be non-negative")


@dataclass(frozen=True)
class DecisionDataset:
    """Rows plus audit artifacts produced by one deterministic build."""

    rows: list[dict[str, object]]
    summary: dict[str, object]
    manifest: dict[str, object]
    unmatched_orders: list[dict[str, object]]
    ambiguous_matches: list[dict[str, object]]
    malformed_records: list[dict[str, object]]


def build_decision_dataset(
    database_path: Path,
    *,
    signal_journal_path: Path | None = None,
    config: DecisionDatasetConfig | None = None,
) -> DecisionDataset:
    """Build one row per decision and attach post-decision labels when safe."""

    rules = config or DecisionDatasetConfig()
    malformed: list[dict[str, object]] = []
    decisions = _load_scan_decisions(database_path, malformed)

    if signal_journal_path is not None and signal_journal_path.exists():
        journal_rows = _load_jsonl(signal_journal_path, "signal_journal", malformed)
        _merge_signal_journal(decisions, journal_rows, rules, malformed)

    rejected_rows = _load_table_payloads(database_path, "rejected_signals", malformed)
    _merge_rejected_signals(decisions, rejected_rows, rules)

    orders = _load_paper_orders(database_path, malformed)
    unmatched_orders, ambiguous_matches = _attach_order_labels(decisions, orders, rules)

    if not rules.include_no_trade:
        decisions = [row for row in decisions if not bool(row.get("is_no_trade"))]

    decisions.sort(key=lambda row: (str(row.get("decision_timestamp", "")), str(row["decision_id"])))
    for row in decisions:
        row["feature_snapshot_hash"] = _feature_snapshot_hash(row)

    summary = _build_summary(decisions, unmatched_orders, ambiguous_matches, malformed)
    manifest = _build_manifest(
        database_path=database_path,
        signal_journal_path=signal_journal_path,
        config=rules,
        rows=decisions,
        summary=summary,
    )
    return DecisionDataset(
        rows=decisions,
        summary=summary,
        manifest=manifest,
        unmatched_orders=unmatched_orders,
        ambiguous_matches=ambiguous_matches,
        malformed_records=malformed,
    )


def write_decision_dataset(dataset: DecisionDataset, output_dir: Path) -> dict[str, Path]:
    """Write stable JSONL/CSV datasets and audit summaries."""

    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = output_dir / "decision_calibration_dataset.jsonl"
    training_path = output_dir / "decision_training_dataset.jsonl"
    csv_path = output_dir / "decision_calibration_dataset.csv"
    summary_json = output_dir / "decision_calibration_summary.json"
    summary_text = output_dir / "decision_calibration_summary.txt"
    manifest_path = output_dir / "decision_calibration_manifest.json"
    unmatched_path = output_dir / "unmatched_paper_orders.jsonl"
    ambiguous_path = output_dir / "ambiguous_decision_matches.jsonl"
    malformed_path = output_dir / "malformed_calibration_sources.jsonl"

    _write_jsonl(dataset_path, dataset.rows)
    training_rows = [
        row
        for row in dataset.rows
        if bool(row.get("is_trainable_candidate")) and bool(row.get("return_label_available"))
    ]
    _write_jsonl(training_path, training_rows)
    _write_csv(csv_path, dataset.rows)
    summary_json.write_text(
        json.dumps(dataset.summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary_text.write_text(_summary_to_text(dataset.summary), encoding="utf-8")
    manifest_path.write_text(
        json.dumps(dataset.manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_jsonl(unmatched_path, dataset.unmatched_orders)
    _write_jsonl(ambiguous_path, dataset.ambiguous_matches)
    _write_jsonl(malformed_path, dataset.malformed_records)

    return {
        "dataset": dataset_path,
        "training_dataset": training_path,
        "csv": csv_path,
        "summary_json": summary_json,
        "summary_txt": summary_text,
        "manifest": manifest_path,
        "unmatched_orders": unmatched_path,
        "ambiguous_matches": ambiguous_path,
        "malformed_records": malformed_path,
    }


def score_band(score: object) -> str:
    """Return the stable bands requested by the decision-calibration backlog."""

    value = _number(score)
    if value is None:
        return "missing"
    if value < 50.0:
        return "0-50"
    if value < 60.0:
        return "50-60"
    if value < 70.0:
        return "60-70"
    if value < 75.0:
        return "70-75"
    if value < 80.0:
        return "75-80"
    return "80+"


def normalize_symbol(symbol: object) -> str:
    """Normalize separators and case without guessing broker suffixes."""

    return "".join(character for character in str(symbol or "").upper() if character.isalnum())


def _load_scan_decisions(
    database_path: Path,
    malformed: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not database_path.exists():
        return []
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        if not _table_exists(connection, "scan_results"):
            return []
        rows = connection.execute("SELECT * FROM scan_results ORDER BY created_at, id").fetchall()

    decisions: list[dict[str, object]] = []
    for row in rows:
        raw_payload = row["payload_json"] if "payload_json" in row.keys() else None
        payload = _parse_json_object(raw_payload)
        if payload is None:
            malformed.append(
                {
                    "source": "scan_results",
                    "record_id": str(row["id"]) if "id" in row.keys() else None,
                    "reason": "payload_json is not a valid JSON object; scalar columns used as fallback",
                }
            )
            payload = {}
        decisions.append(_decision_from_scan_row(row, payload))
    return decisions


def _decision_from_scan_row(
    row: sqlite3.Row,
    payload: dict[str, object],
) -> dict[str, object]:
    timestamp = _timestamp(
        _first(payload.get("timestamp"), _row_value(row, "created_at"))
    )
    symbol = str(_first(payload.get("symbol"), _row_value(row, "symbol"), ""))
    setup_family = _enum_value(
        _first(payload.get("setup_family"), _row_value(row, "setup_family"), "")
    )
    raw_family = _enum_value(payload.get("raw_setup_family"))
    direction = _enum_value(
        _first(payload.get("direction"), _row_value(row, "direction"), "")
    )
    status = _enum_value(
        _first(payload.get("status"), _row_value(row, "status"), "")
    )
    final_score = _number(
        _first(
            payload.get("final_score"),
            payload.get("score"),
            _row_value(row, "final_score"),
            _row_value(row, "score"),
        )
    )
    atr = _number(_first(payload.get("atr"), _row_value(row, "atr")))
    spread = _number(_first(payload.get("spread"), _row_value(row, "spread")))
    data_quality = payload.get("data_quality")
    data_quality_score = (
        _number(data_quality.get("score"))
        if isinstance(data_quality, dict)
        else None
    )
    scanner_approved = bool(payload.get("approved", status in {"approved", "premium"}))
    is_no_trade = setup_family == "no_trade" or direction == "no_trade"
    trainable = not is_no_trade and bool(raw_family or setup_family)

    result: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "decision_id": str(_first(_row_value(row, "id"), "")),
        "source_records": ["scan_results"],
        "decision_timestamp": timestamp.isoformat(),
        "symbol": symbol,
        "normalized_symbol": normalize_symbol(symbol),
        "style": _enum_value(_first(payload.get("style"), _row_value(row, "style"), "")),
        "setup_family": setup_family,
        "setup_subtype": _enum_value(
            _first(payload.get("setup_subtype"), _row_value(row, "setup_subtype"), "none")
        ),
        "raw_setup_family": raw_family or None,
        "direction": direction,
        "scanner_status": status,
        "scanner_approved": scanner_approved,
        "is_rejected": not scanner_approved,
        "is_no_trade": is_no_trade,
        "is_trainable_candidate": trainable,
        "confidence": _enum_value(
            _first(payload.get("confidence"), _row_value(row, "confidence"), "")
        ),
        "regime": _enum_value(_first(payload.get("regime"), _row_value(row, "regime"), "")),
        "htf_regime": _enum_value(
            _first(payload.get("htf_regime"), _row_value(row, "htf_regime"), "")
        ),
        "entry_regime": _enum_value(
            _first(payload.get("entry_regime"), _row_value(row, "entry_regime"), "")
        ),
        "trigger_regime": _enum_value(
            _first(payload.get("trigger_regime"), _row_value(row, "trigger_regime"), "")
        ),
        "session": _enum_value(_first(payload.get("session"), _row_value(row, "session"), "")),
        "provider": str(_first(payload.get("provider"), _row_value(row, "provider"), "")),
        "technical_score": _number(
            _first(payload.get("technical_score"), _row_value(row, "technical_score"))
        ),
        "execution_score": _number(
            _first(payload.get("execution_score"), _row_value(row, "execution_score"))
        ),
        "context_score": _number(
            _first(payload.get("context_score"), _row_value(row, "context_score"))
        ),
        "empirical_score": _number(
            _first(payload.get("empirical_score"), _row_value(row, "empirical_score"))
        ),
        "final_score": final_score,
        "score_band": score_band(final_score),
        "pattern_score": _number(payload.get("pattern_score")) or 0.0,
        "activation_quality": _number(payload.get("activation_quality")),
        "invalidation_quality": _number(payload.get("invalidation_quality")),
        "entry": _number(_first(payload.get("entry"), _row_value(row, "entry"))),
        "stop_loss": _number(_first(payload.get("stop_loss"), _row_value(row, "stop_loss"))),
        "take_profit": _number(
            _first(payload.get("take_profit"), _row_value(row, "take_profit"))
        ),
        "tp1": _number(payload.get("tp1")),
        "tp2": _number(payload.get("tp2")),
        "tp3": _number(payload.get("tp3")),
        "risk_reward": _number(
            _first(payload.get("risk_reward"), _row_value(row, "risk_reward"))
        ),
        "required_min_rr": _number(payload.get("required_min_rr")),
        "spread": spread,
        "atr": atr,
        "spread_atr_ratio": (
            round(spread / atr, 8)
            if spread is not None and atr is not None and atr > 0.0
            else None
        ),
        "data_quality_score": data_quality_score,
        "score_components": _dict(payload.get("score_components")),
        "key_level_distances": _dict(payload.get("key_level_distances")),
        "detected_patterns": _list(payload.get("detected_patterns")),
        "gate_breakdown": _dict(payload.get("gate_breakdown")),
        "failed_gates": _list(payload.get("failed_gates")),
        "rejection_category": _enum_value(payload.get("rejection_category")) or None,
        "rejection_reason": _first(
            payload.get("rejection_reason"),
            None,
        ),
        "missing_conditions": _list(payload.get("missing_conditions")),
        "adaptive_threshold_enabled": bool(payload.get("adaptive_threshold_enabled", False)),
        "base_min_score": _number(payload.get("base_min_score")),
        "adaptive_min_score": _number(payload.get("adaptive_min_score")),
        "effective_min_score": _number(payload.get("effective_min_score")),
        "cycle_id": None,
        "signal_journal_timestamp": None,
        "bot_decision": None,
        "bot_rejection_reasons": [],
        "broker": None,
        "mode": None,
        "watchlist": None,
        "linked_order_ids": [],
        "label_order_id": None,
        "order_match_method": None,
        "order_status": None,
        "activated": None,
        "activation_label_available": False,
        "return_label_available": False,
        "realized_r": None,
        "realized_pnl": None,
        "target_positive_r": None,
        "target_non_negative_r": None,
        "outcome_status": None,
        "close_reason": None,
        "mae": _number(_first(payload.get("mae"), _row_value(row, "mae"))),
        "mfe": _number(_first(payload.get("mfe"), _row_value(row, "mfe"))),
        "bars_to_activation": _integer(
            _first(payload.get("bars_to_activation"), _row_value(row, "bars_to_activation"))
        ),
        "time_in_trade_minutes": None,
        "label_source": None,
    }
    _apply_embedded_scan_label(result, payload)
    return result


def _merge_signal_journal(
    decisions: list[dict[str, object]],
    journal_rows: list[dict[str, object]],
    config: DecisionDatasetConfig,
    malformed: list[dict[str, object]],
) -> None:
    for index, journal in enumerate(journal_rows):
        symbol = str(_first(journal.get("logical_symbol"), journal.get("symbol"), ""))
        timestamp = _safe_timestamp(journal.get("timestamp_utc"))
        if not symbol or timestamp is None:
            malformed.append(
                {
                    "source": "signal_journal",
                    "record_index": index,
                    "reason": "missing logical_symbol or valid timestamp_utc",
                }
            )
            continue
        match = _unique_nearest_decision(
            decisions,
            timestamp=timestamp,
            symbol=symbol,
            setup_subtype=str(journal.get("setup") or ""),
            direction=str(journal.get("direction") or ""),
            window_seconds=config.match_window_seconds,
        )
        if match is None and config.include_supplemental_journal_rows:
            match = _supplemental_journal_decision(journal, index)
            decisions.append(match)
        if match is None:
            continue
        _append_source(match, "signal_journal")
        match["cycle_id"] = journal.get("cycle_id") or match.get("cycle_id")
        match["signal_journal_timestamp"] = timestamp.isoformat()
        match["bot_decision"] = journal.get("decision") or match.get("bot_decision")
        match["bot_rejection_reasons"] = _list(journal.get("rejection_reasons"))
        match["broker"] = journal.get("broker")
        match["mode"] = journal.get("mode")
        match["watchlist"] = journal.get("watchlist")
        order_ids = [str(item) for item in _list(journal.get("order_ids")) if item]
        match["linked_order_ids"] = sorted(set([*match.get("linked_order_ids", []), *order_ids]))


def _merge_rejected_signals(
    decisions: list[dict[str, object]],
    records: list[dict[str, object]],
    config: DecisionDatasetConfig,
) -> None:
    for index, payload in enumerate(records):
        timestamp = _safe_timestamp(payload.get("timestamp"))
        symbol = str(payload.get("symbol") or "")
        cycle_id = str(payload.get("cycle_id") or "")
        candidates = [
            row
            for row in decisions
            if cycle_id
            and row.get("cycle_id") == cycle_id
            and row.get("normalized_symbol") == normalize_symbol(symbol)
        ]
        match = candidates[0] if len(candidates) == 1 else None
        if match is None and timestamp is not None:
            match = _unique_nearest_decision(
                decisions,
                timestamp=timestamp,
                symbol=symbol,
                setup_subtype=str(payload.get("setup") or ""),
                direction="",
                window_seconds=config.match_window_seconds,
            )
        if match is None:
            match = _supplemental_rejected_decision(payload, index)
            decisions.append(match)
        _append_source(match, "rejected_signals")
        match["cycle_id"] = cycle_id or match.get("cycle_id")
        match["bot_decision"] = "rejected"
        reasons = [str(item) for item in _list(payload.get("rejection_reasons")) if item]
        match["bot_rejection_reasons"] = sorted(
            set([*match.get("bot_rejection_reasons", []), *reasons])
        )
        match["broker"] = payload.get("broker") or match.get("broker")
        match["watchlist"] = payload.get("watchlist") or match.get("watchlist")


def _load_paper_orders(
    database_path: Path,
    malformed: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not database_path.exists():
        return []
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        if not _table_exists(connection, "paper_orders"):
            return []
        rows = connection.execute("SELECT * FROM paper_orders ORDER BY created_at, id").fetchall()

    orders: list[dict[str, object]] = []
    for row in rows:
        payload = _parse_json_object(_row_value(row, "payload_json"))
        if payload is None:
            malformed.append(
                {
                    "source": "paper_orders",
                    "record_id": str(_row_value(row, "id") or ""),
                    "reason": "payload_json is not a valid JSON object; scalar columns used as fallback",
                }
            )
            payload = {}
        request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
        signal_timestamp = _safe_timestamp(
            _first(
                request.get("signal_timestamp"),
                payload.get("signal_timestamp"),
                _row_value(row, "signal_at"),
                _row_value(row, "created_at"),
            )
        )
        orders.append(
            {
                "order_id": str(_first(payload.get("order_id"), _row_value(row, "id"), "")),
                "source_opportunity_id": request.get("source_opportunity_id"),
                "signal_timestamp": signal_timestamp,
                "symbol": str(_first(request.get("symbol"), _row_value(row, "symbol"), "")),
                "normalized_symbol": normalize_symbol(
                    _first(request.get("symbol"), _row_value(row, "symbol"), "")
                ),
                "style": _enum_value(request.get("style")),
                "setup_family": _enum_value(
                    _first(request.get("setup_family"), _row_value(row, "setup_family"), "")
                ),
                "setup_subtype": _enum_value(
                    _first(request.get("setup_subtype"), _row_value(row, "setup_subtype"), "")
                ),
                "direction": _enum_value(
                    _first(request.get("direction"), _row_value(row, "direction"), "")
                ),
                "status": _enum_value(
                    _first(payload.get("status"), _row_value(row, "status"), "")
                ),
                "activated_at": _safe_timestamp(
                    _first(payload.get("activated_at"), _row_value(row, "activated_at"))
                ),
                "closed_at": _safe_timestamp(
                    _first(payload.get("closed_at"), _row_value(row, "closed_at"))
                ),
                "realized_r": _number(
                    _first(payload.get("realized_r"), _row_value(row, "realized_r"))
                ),
                "realized_pnl": _number(
                    _first(payload.get("realized_pnl"), _row_value(row, "realized_pnl"))
                ),
                "mae": _number(_first(payload.get("mae"), _row_value(row, "mae"))),
                "mfe": _number(_first(payload.get("mfe"), _row_value(row, "mfe"))),
                "bars_to_activation": _integer(payload.get("bars_to_activation")),
                "time_in_trade_minutes": _number(payload.get("time_in_trade_minutes")),
                "close_reason": _enum_value(payload.get("close_reason")) or None,
            }
        )
    return orders


def _attach_order_labels(
    decisions: list[dict[str, object]],
    orders: list[dict[str, object]],
    config: DecisionDatasetConfig,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    matches_by_decision: dict[str, list[tuple[dict[str, object], str]]] = {}
    unmatched: list[dict[str, object]] = []
    ambiguous: list[dict[str, object]] = []

    for order in orders:
        candidates, method = _order_candidates(decisions, order, config)
        if len(candidates) == 1:
            decision_id = str(candidates[0]["decision_id"])
            matches_by_decision.setdefault(decision_id, []).append((order, method))
        elif len(candidates) > 1:
            ambiguous.append(
                {
                    "order_id": order["order_id"],
                    "method": method,
                    "candidate_decision_ids": [row["decision_id"] for row in candidates],
                    "symbol": order["symbol"],
                    "signal_timestamp": _iso(order.get("signal_timestamp")),
                }
            )
        else:
            unmatched.append(_public_order_record(order, reason="no compatible unique decision"))

    by_id = {str(row["decision_id"]): row for row in decisions}
    for decision_id, matched in matches_by_decision.items():
        decision = by_id[decision_id]
        orders_only = [item[0] for item in matched]
        chosen = max(orders_only, key=_order_label_priority)
        method = next(item[1] for item in matched if item[0] is chosen)
        decision["linked_order_ids"] = sorted(
            set([*decision.get("linked_order_ids", []), *(str(item["order_id"]) for item in orders_only)])
        )
        _apply_order_label(decision, chosen, method)
    return unmatched, ambiguous


def _order_candidates(
    decisions: list[dict[str, object]],
    order: dict[str, object],
    config: DecisionDatasetConfig,
) -> tuple[list[dict[str, object]], str]:
    order_id = str(order.get("order_id") or "")
    source_id = str(order.get("source_opportunity_id") or "")
    normalized_symbol = str(order.get("normalized_symbol") or "")

    direct = [
        row
        for row in decisions
        if source_id and str(row.get("decision_id")) == source_id
    ]
    direct = _compatible_order_decisions(direct, order)
    if direct:
        return direct, "source_opportunity_id"

    listed = [
        row
        for row in decisions
        if order_id and order_id in [str(item) for item in row.get("linked_order_ids", [])]
    ]
    listed = _compatible_order_decisions(listed, order)
    if listed:
        return listed, "signal_journal_order_id"

    cycle = [
        row
        for row in decisions
        if source_id
        and str(row.get("cycle_id") or "") == source_id
        and row.get("normalized_symbol") == normalized_symbol
    ]
    cycle = _compatible_order_decisions(cycle, order)
    if cycle:
        return cycle, "cycle_id_and_symbol"

    signal_timestamp = order.get("signal_timestamp")
    if not isinstance(signal_timestamp, datetime):
        return [], "no_signal_timestamp"
    candidates = _nearest_compatible_decisions(
        decisions,
        timestamp=signal_timestamp,
        symbol=str(order.get("symbol") or ""),
        setup_family=str(order.get("setup_family") or ""),
        setup_subtype=str(order.get("setup_subtype") or ""),
        direction=str(order.get("direction") or ""),
        window_seconds=config.match_window_seconds,
    )
    return candidates, "unique_temporal_fallback"


def _compatible_order_decisions(
    rows: list[dict[str, object]],
    order: dict[str, object],
) -> list[dict[str, object]]:
    compatible: list[dict[str, object]] = []
    for row in rows:
        if row.get("bot_decision") == "rejected" or bool(row.get("is_no_trade")):
            continue
        if row.get("normalized_symbol") != order.get("normalized_symbol"):
            continue
        if not _compatible_value(row.get("setup_family"), order.get("setup_family")):
            continue
        if not _compatible_value(row.get("setup_subtype"), order.get("setup_subtype")):
            continue
        if not _compatible_value(row.get("direction"), order.get("direction")):
            continue
        compatible.append(row)
    return compatible


def _nearest_compatible_decisions(
    decisions: list[dict[str, object]],
    *,
    timestamp: datetime,
    symbol: str,
    setup_family: str,
    setup_subtype: str,
    direction: str,
    window_seconds: int,
) -> list[dict[str, object]]:
    distances: list[tuple[float, dict[str, object]]] = []
    normalized_symbol = normalize_symbol(symbol)
    for row in decisions:
        if row.get("normalized_symbol") != normalized_symbol:
            continue
        if row.get("bot_decision") == "rejected" or bool(row.get("is_no_trade")):
            continue
        if not _compatible_value(row.get("setup_family"), setup_family):
            continue
        if not _compatible_value(row.get("setup_subtype"), setup_subtype):
            continue
        if not _compatible_value(row.get("direction"), direction):
            continue
        row_timestamp = _safe_timestamp(row.get("decision_timestamp"))
        if row_timestamp is None:
            continue
        distance = abs((row_timestamp - timestamp).total_seconds())
        if distance <= window_seconds:
            distances.append((distance, row))
    if not distances:
        return []
    distances.sort(key=lambda item: (item[0], str(item[1]["decision_id"])))
    best_distance = distances[0][0]
    return [row for distance, row in distances if distance == best_distance]


def _unique_nearest_decision(
    decisions: list[dict[str, object]],
    *,
    timestamp: datetime,
    symbol: str,
    setup_subtype: str,
    direction: str,
    window_seconds: int,
) -> dict[str, object] | None:
    normalized_symbol = normalize_symbol(symbol)
    distances: list[tuple[float, dict[str, object]]] = []
    for row in decisions:
        if row.get("normalized_symbol") != normalized_symbol:
            continue
        if setup_subtype and not _compatible_value(row.get("setup_subtype"), setup_subtype):
            continue
        if direction and not _compatible_value(row.get("direction"), direction):
            continue
        row_timestamp = _safe_timestamp(row.get("decision_timestamp"))
        if row_timestamp is None:
            continue
        distance = abs((row_timestamp - timestamp).total_seconds())
        if distance <= window_seconds:
            distances.append((distance, row))
    if not distances:
        return None
    distances.sort(key=lambda item: (item[0], str(item[1]["decision_id"])))
    if len(distances) > 1 and distances[0][0] == distances[1][0]:
        return None
    return distances[0][1]


def _apply_order_label(
    decision: dict[str, object],
    order: dict[str, object],
    method: str,
) -> None:
    realized_r = _number(order.get("realized_r"))
    status = str(order.get("status") or "")
    activated = isinstance(order.get("activated_at"), datetime) or status in {
        "open_trade",
        "partially_closed_trade",
        "fully_closed_trade",
        "active",
        "closed",
    }
    terminal = status in _TERMINAL_ORDER_STATUSES
    decision.update(
        {
            "label_order_id": order.get("order_id"),
            "order_match_method": method,
            "order_status": status or None,
            "activated": activated,
            "activation_label_available": bool(activated or terminal),
            "return_label_available": realized_r is not None,
            "realized_r": realized_r,
            "realized_pnl": _number(order.get("realized_pnl")),
            "target_positive_r": (realized_r > 0.0) if realized_r is not None else None,
            "target_non_negative_r": (realized_r >= 0.0) if realized_r is not None else None,
            "outcome_status": _paper_outcome_status(order),
            "close_reason": order.get("close_reason"),
            "mae": _number(order.get("mae")),
            "mfe": _number(order.get("mfe")),
            "bars_to_activation": _integer(order.get("bars_to_activation")),
            "time_in_trade_minutes": _number(order.get("time_in_trade_minutes")),
            "label_source": "paper_order",
        }
    )


def _apply_embedded_scan_label(
    decision: dict[str, object],
    payload: dict[str, object],
) -> None:
    outcome = _enum_value(payload.get("outcome"))
    if not outcome:
        return
    decision["outcome_status"] = outcome
    decision["label_source"] = "scan_payload"
    if outcome in _WIN_SCAN_OUTCOMES:
        decision["return_label_available"] = True
        decision["target_positive_r"] = True
        decision["target_non_negative_r"] = True
    elif outcome in _LOSS_SCAN_OUTCOMES:
        decision["return_label_available"] = True
        decision["target_positive_r"] = False
        decision["target_non_negative_r"] = False
    elif outcome in _BREAKEVEN_SCAN_OUTCOMES:
        decision["return_label_available"] = True
        decision["target_positive_r"] = False
        decision["target_non_negative_r"] = True


def _paper_outcome_status(order: dict[str, object]) -> str:
    realized_r = _number(order.get("realized_r"))
    if realized_r is not None:
        if realized_r > 0.0:
            return "win"
        if realized_r < 0.0:
            return "loss"
        return "breakeven"
    status = str(order.get("status") or "")
    mapping = {
        "missed_trade": "missed",
        "cancelled_trade": "cancelled",
        "canceled": "cancelled",
        "expired_trade": "expired",
        "rejected": "rejected",
        "open_trade": "open",
        "partially_closed_trade": "open",
        "active": "open",
        "pending": "pending",
        "pending_opportunity": "pending",
    }
    return mapping.get(status, status or "unknown")


def _supplemental_journal_decision(
    payload: dict[str, object],
    index: int,
) -> dict[str, object]:
    timestamp = _timestamp(payload.get("timestamp_utc"))
    symbol = str(_first(payload.get("logical_symbol"), payload.get("symbol"), ""))
    score = _number(payload.get("score"))
    family = _enum_value(payload.get("setup_family"))
    direction = _enum_value(payload.get("direction"))
    is_no_trade = family == "no_trade" or direction == "no_trade"
    return {
        "schema_version": SCHEMA_VERSION,
        "decision_id": f"journal:{payload.get('cycle_id') or index}:{normalize_symbol(symbol)}",
        "source_records": ["signal_journal"],
        "decision_timestamp": timestamp.isoformat(),
        "symbol": symbol,
        "normalized_symbol": normalize_symbol(symbol),
        "style": _enum_value(payload.get("style")),
        "setup_family": family,
        "setup_subtype": _enum_value(payload.get("setup")) or "none",
        "raw_setup_family": None,
        "direction": direction,
        "scanner_status": _enum_value(payload.get("status")),
        "scanner_approved": payload.get("decision") == "accepted",
        "is_rejected": payload.get("decision") != "accepted",
        "is_no_trade": is_no_trade,
        "is_trainable_candidate": bool(family) and not is_no_trade,
        "confidence": "",
        "regime": _enum_value(payload.get("market_regime")),
        "htf_regime": "",
        "entry_regime": "",
        "trigger_regime": "",
        "session": _enum_value(payload.get("session_name")),
        "provider": str(payload.get("provider") or ""),
        "technical_score": None,
        "execution_score": None,
        "context_score": None,
        "empirical_score": None,
        "final_score": score,
        "score_band": score_band(score),
        "pattern_score": _number(payload.get("pattern_score")) or 0.0,
        "activation_quality": None,
        "invalidation_quality": None,
        "entry": _number(payload.get("entry")),
        "stop_loss": _number(payload.get("stop_loss")),
        "take_profit": _number(payload.get("take_profit")),
        "tp1": _number(payload.get("tp1")),
        "tp2": _number(payload.get("tp2")),
        "tp3": _number(payload.get("tp3")),
        "risk_reward": _number(payload.get("risk_reward")),
        "required_min_rr": None,
        "spread": None,
        "atr": None,
        "spread_atr_ratio": _number(payload.get("spread_atr")),
        "data_quality_score": None,
        "score_components": {},
        "key_level_distances": {},
        "detected_patterns": _list(payload.get("detected_patterns")),
        "gate_breakdown": {},
        "failed_gates": [],
        "rejection_category": None,
        "rejection_reason": None,
        "missing_conditions": [],
        "adaptive_threshold_enabled": bool(payload.get("adaptive_threshold_enabled", False)),
        "base_min_score": _number(payload.get("base_min_score")),
        "adaptive_min_score": _number(payload.get("adaptive_min_score")),
        "effective_min_score": _number(payload.get("effective_min_score")),
        "cycle_id": payload.get("cycle_id"),
        "signal_journal_timestamp": timestamp.isoformat(),
        "bot_decision": payload.get("decision"),
        "bot_rejection_reasons": _list(payload.get("rejection_reasons")),
        "broker": payload.get("broker"),
        "mode": payload.get("mode"),
        "watchlist": payload.get("watchlist"),
        "linked_order_ids": [str(item) for item in _list(payload.get("order_ids")) if item],
        "label_order_id": None,
        "order_match_method": None,
        "order_status": None,
        "activated": None,
        "activation_label_available": False,
        "return_label_available": False,
        "realized_r": None,
        "realized_pnl": None,
        "target_positive_r": None,
        "target_non_negative_r": None,
        "outcome_status": None,
        "close_reason": None,
        "mae": None,
        "mfe": None,
        "bars_to_activation": None,
        "time_in_trade_minutes": None,
        "label_source": None,
    }


def _supplemental_rejected_decision(
    payload: dict[str, object],
    index: int,
) -> dict[str, object]:
    timestamp = _timestamp(payload.get("timestamp"))
    symbol = str(payload.get("symbol") or "")
    score = _number(payload.get("score"))
    subtype = _enum_value(payload.get("setup")) or "none"
    result = _supplemental_journal_decision(
        {
            "timestamp_utc": timestamp.isoformat(),
            "cycle_id": payload.get("cycle_id"),
            "logical_symbol": symbol,
            "style": payload.get("style"),
            "setup": subtype,
            "status": payload.get("status"),
            "direction": payload.get("direction"),
            "score": score,
            "risk_reward": payload.get("risk_reward"),
            "pattern_score": payload.get("pattern_score"),
            "detected_patterns": payload.get("detected_patterns"),
            "decision": "rejected",
            "rejection_reasons": payload.get("rejection_reasons"),
            "provider": payload.get("provider"),
            "broker": payload.get("broker"),
            "watchlist": payload.get("watchlist"),
            "spread_atr": payload.get("spread_atr"),
            "entry": payload.get("entry"),
            "stop_loss": payload.get("stop_loss"),
            "tp1": payload.get("tp1"),
            "tp2": payload.get("tp2"),
            "tp3": payload.get("tp3"),
        },
        index,
    )
    result["decision_id"] = f"rejected:{payload.get('id') or index}"
    result["source_records"] = ["rejected_signals"]
    result["scanner_approved"] = False
    result["is_rejected"] = True
    return result


def _build_summary(
    rows: list[dict[str, object]],
    unmatched: list[dict[str, object]],
    ambiguous: list[dict[str, object]],
    malformed: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "total_decisions": len(rows),
        "trainable_candidates": sum(bool(row.get("is_trainable_candidate")) for row in rows),
        "scanner_approved": sum(bool(row.get("scanner_approved")) for row in rows),
        "scanner_rejected": sum(not bool(row.get("scanner_approved")) for row in rows),
        "bot_accepted": sum(row.get("bot_decision") == "accepted" for row in rows),
        "bot_rejected": sum(row.get("bot_decision") == "rejected" for row in rows),
        "no_trade_rows": sum(bool(row.get("is_no_trade")) for row in rows),
        "return_labeled_rows": sum(bool(row.get("return_label_available")) for row in rows),
        "activation_labeled_rows": sum(
            bool(row.get("activation_label_available")) for row in rows
        ),
        "positive_r_rows": sum(row.get("target_positive_r") is True for row in rows),
        "non_positive_r_rows": sum(row.get("target_positive_r") is False for row in rows),
        "unmatched_paper_orders": len(unmatched),
        "ambiguous_order_matches": len(ambiguous),
        "malformed_source_records": len(malformed),
        "by_scanner_status": _counts(rows, "scanner_status"),
        "by_bot_decision": _counts(rows, "bot_decision"),
        "by_setup_family": _counts(rows, "setup_family"),
        "by_score_band": _counts(rows, "score_band"),
        "by_label_source": _counts(rows, "label_source"),
        "by_order_match_method": _counts(rows, "order_match_method"),
    }


def _build_manifest(
    *,
    database_path: Path,
    signal_journal_path: Path | None,
    config: DecisionDatasetConfig,
    rows: list[dict[str, object]],
    summary: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_path": str(database_path),
        "database_sha256": _file_sha256(database_path),
        "signal_journal_path": str(signal_journal_path) if signal_journal_path else None,
        "signal_journal_sha256": (
            _file_sha256(signal_journal_path)
            if signal_journal_path is not None
            else None
        ),
        "config": asdict(config),
        "feature_columns": FEATURE_COLUMNS,
        "label_columns": LABEL_COLUMNS,
        "row_count": len(rows),
        "summary": summary,
        "training_guidance": {
            "split_policy": "chronological_or_purged_time_split_only",
            "random_split_allowed": False,
            "feature_timing": "decision_snapshot_only",
            "label_timing": "post_decision_lifecycle_only",
            "recommended_primary_target": "realized_r or target_positive_r",
            "rejected_rows_without_counterfactual_outcomes": (
                "retain for decision diagnostics; do not treat rejection as a loss"
            ),
        },
    }


def _summary_to_text(summary: dict[str, object]) -> str:
    lines = [
        "Decision Calibration Dataset (paper/research only)",
        "===================================================",
        f"total decisions          : {summary['total_decisions']}",
        f"trainable candidates     : {summary['trainable_candidates']}",
        f"scanner approved         : {summary['scanner_approved']}",
        f"scanner rejected         : {summary['scanner_rejected']}",
        f"bot accepted             : {summary['bot_accepted']}",
        f"bot rejected             : {summary['bot_rejected']}",
        f"return-labeled rows      : {summary['return_labeled_rows']}",
        f"activation-labeled rows  : {summary['activation_labeled_rows']}",
        f"positive-R rows          : {summary['positive_r_rows']}",
        f"unmatched paper orders   : {summary['unmatched_paper_orders']}",
        f"ambiguous order matches  : {summary['ambiguous_order_matches']}",
        f"malformed source records : {summary['malformed_source_records']}",
        "",
        f"score bands              : {_format_counts(summary['by_score_band'])}",
        f"setup families           : {_format_counts(summary['by_setup_family'])}",
        f"label sources            : {_format_counts(summary['by_label_source'])}",
        "",
        "Important: rejected decisions without a realized counterfactual outcome are",
        "not losses. Use time-based or purged temporal splits; never random row splits.",
    ]
    return "\n".join(lines) + "\n"


def _write_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "decision_id",
        "decision_timestamp",
        "symbol",
        "normalized_symbol",
        "style",
        "setup_family",
        "setup_subtype",
        "direction",
        "scanner_status",
        "scanner_approved",
        "bot_decision",
        "final_score",
        "score_band",
        "technical_score",
        "execution_score",
        "context_score",
        "empirical_score",
        "risk_reward",
        "spread_atr_ratio",
        "data_quality_score",
        "rejection_category",
        "return_label_available",
        "realized_r",
        "target_positive_r",
        "outcome_status",
        "order_status",
        "order_match_method",
        "label_source",
        "feature_snapshot_hash",
        "source_records",
        "bot_rejection_reasons",
        "detected_patterns",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            flattened = {
                key: _csv_value(row.get(key))
                for key in fieldnames
            }
            writer.writerow(flattened)


def _load_table_payloads(
    database_path: Path,
    table: str,
    malformed: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not database_path.exists():
        return []
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        if not _table_exists(connection, table):
            return []
        rows = connection.execute(f"SELECT * FROM {table}").fetchall()
    payloads: list[dict[str, object]] = []
    for row in rows:
        payload = _parse_json_object(_row_value(row, "payload_json"))
        if payload is None:
            malformed.append(
                {
                    "source": table,
                    "record_id": str(_row_value(row, "id") or ""),
                    "reason": "payload_json is not a valid JSON object",
                }
            )
            continue
        payloads.append(payload)
    return payloads


def _load_jsonl(
    path: Path,
    source: str,
    malformed: list[dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = _parse_json_object(line)
        if payload is None:
            malformed.append(
                {
                    "source": source,
                    "line_number": line_number,
                    "reason": "line is not a valid JSON object",
                }
            )
            continue
        rows.append(payload)
    return rows


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _order_label_priority(order: dict[str, object]) -> tuple[int, int, str]:
    return (
        1 if _number(order.get("realized_r")) is not None else 0,
        1 if str(order.get("status") or "") in _TERMINAL_ORDER_STATUSES else 0,
        _iso(order.get("closed_at")) or "",
    )


def _public_order_record(order: dict[str, object], *, reason: str) -> dict[str, object]:
    return {
        "order_id": order.get("order_id"),
        "source_opportunity_id": order.get("source_opportunity_id"),
        "symbol": order.get("symbol"),
        "signal_timestamp": _iso(order.get("signal_timestamp")),
        "status": order.get("status"),
        "reason": reason,
    }


def _feature_snapshot_hash(row: dict[str, object]) -> str:
    payload = {column: row.get(column) for column in FEATURE_COLUMNS}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _counts(rows: list[dict[str, object]], key: str) -> dict[str, int]:
    counts = Counter(str(row.get(key) or "missing") for row in rows)
    return dict(sorted(counts.items()))


def _format_counts(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return "none"
    return ", ".join(f"{key}={item}" for key, item in sorted(value.items()))


def _append_source(row: dict[str, object], source: str) -> None:
    sources = [str(item) for item in _list(row.get("source_records")) if item]
    if source not in sources:
        sources.append(source)
    row["source_records"] = sources


def _compatible_value(left: object, right: object) -> bool:
    left_value = _enum_value(left)
    right_value = _enum_value(right)
    return not left_value or not right_value or left_value == right_value


def _row_value(row: sqlite3.Row, key: str) -> object:
    return row[key] if key in row.keys() else None


def _parse_json_object(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return value
    if value is None:
        return None
    try:
        parsed = json.loads(str(value))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _timestamp(value: object) -> datetime:
    parsed = _safe_timestamp(value)
    if parsed is None:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return parsed


def _safe_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    parsed = _safe_timestamp(value)
    return parsed.isoformat() if parsed else None


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _enum_value(value: object) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        value = getattr(value, "value")
    return str(value).strip().lower()


def _dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, (list, tuple, set)) else []


def _first(*values: object) -> object:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _csv_value(value: object) -> object:
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value
