"""Calibration reports for persisted scan and backtest outcomes."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import pandas as pd


WIN_OUTCOMES = {"win_clean", "win_messy", "partial_win"}
LOSS_OUTCOMES = {"loss_clean", "loss_fast"}


def generate_calibration_report(database_path: Path, output_dir: Path, top_k_values: list[int] | None = None) -> dict[str, Path]:
    """Generate CSV tables and a compact Markdown calibration summary."""

    output_dir.mkdir(parents=True, exist_ok=True)
    records = load_calibration_records(database_path)
    paper_orders = load_paper_order_records(database_path)
    paper_blocks = load_paper_block_records(database_path)
    top_k_values = top_k_values or [5, 10, 20]
    if records.empty:
        summary = output_dir / "summary.md"
        summary.write_text("# Calibration Report\n\nNo realized outcome records were found.\n", encoding="utf-8")
        outputs = {"summary": summary}
        if not paper_orders.empty:
            outputs["paper_lifecycle"] = _write_csv(_paper_lifecycle_report(paper_orders), output_dir / "paper_lifecycle.csv")
        if not paper_blocks.empty:
            outputs["paper_blocks"] = _write_csv(_paper_block_report(paper_blocks), output_dir / "paper_blocks.csv")
        return outputs

    records = records.copy()
    records["score_bucket"] = records["final_score"].apply(score_bucket)
    records["r_value"] = records.apply(_r_value, axis=1)
    records["is_win"] = records.apply(_is_win, axis=1)
    records["is_loss"] = records.apply(_is_loss, axis=1)
    records["non_empirical_score"] = records.apply(_non_empirical_score, axis=1)

    outputs: dict[str, Path] = {}
    outputs["source"] = _write_csv(_group_report(records, "source"), output_dir / "by_source.csv")
    outputs["score_buckets"] = _write_csv(_score_bucket_report(records), output_dir / "score_buckets.csv")
    outputs["layer_score_buckets"] = _write_csv(_layer_score_bucket_report(records), output_dir / "layer_score_buckets.csv")
    outputs["layer_predictiveness"] = _write_csv(_layer_predictiveness_report(records), output_dir / "layer_predictiveness.csv")
    outputs["status"] = _write_csv(_group_report(records, "status"), output_dir / "by_status.csv")
    outputs["status_separation"] = _write_csv(_status_separation_report(records), output_dir / "status_separation.csv")
    outputs["family"] = _write_csv(_group_report(records, "setup_family"), output_dir / "by_setup_family.csv")
    outputs["subtype"] = _write_csv(_group_report(records, "setup_subtype"), output_dir / "by_setup_subtype.csv")
    outputs["symbol"] = _write_csv(_group_report(records, "symbol"), output_dir / "by_symbol.csv")
    outputs["session"] = _write_csv(_group_report(records, "session"), output_dir / "by_session.csv")
    outputs["regime"] = _write_csv(_group_report(records, "regime"), output_dir / "by_regime.csv")
    outputs["execution_conditions"] = _write_csv(_group_report(records, "execution_condition"), output_dir / "by_execution_condition.csv")
    outputs["conditional_combinations"] = _write_csv(_conditional_combo_report(records), output_dir / "conditional_combinations.csv")
    outputs["top_k"] = _write_csv(_top_k_report(records, top_k_values), output_dir / "top_k.csv")
    outputs["best_worst"] = _write_csv(_best_worst_report(records), output_dir / "best_worst_combinations.csv")
    outputs["empirical_lift"] = _write_csv(_empirical_lift_report(records, top_k_values), output_dir / "empirical_lift.csv")
    if not paper_orders.empty:
        outputs["paper_lifecycle"] = _write_csv(_paper_lifecycle_report(paper_orders), output_dir / "paper_lifecycle.csv")
        outputs["paper_execution_summary"] = _write_csv(_paper_execution_summary(records, paper_orders, paper_blocks), output_dir / "paper_execution_summary.csv")
    if not paper_blocks.empty:
        outputs["paper_blocks"] = _write_csv(_paper_block_report(paper_blocks), output_dir / "paper_blocks.csv")
    outputs["suggested_layer_weights"] = _write_json(_suggested_layer_weights(records), output_dir / "suggested_layer_weights.json")
    outputs["summary"] = _write_summary(records, outputs, output_dir / "summary.md")
    outputs["summary_json"] = _write_json(_summary_payload(records, paper_orders, paper_blocks), output_dir / "summary.json")
    return outputs


def load_calibration_records(database_path: Path) -> pd.DataFrame:
    """Load persisted scan/backtest payloads into a normalized DataFrame."""

    if not database_path.exists():
        return pd.DataFrame()
    records: list[dict[str, object]] = []
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        if _table_exists(connection, "backtest_runs"):
            for row in connection.execute("SELECT trades_json FROM backtest_runs").fetchall():
                try:
                    trades = json.loads(str(row["trades_json"]))
                except json.JSONDecodeError:
                    continue
                if isinstance(trades, list):
                    for trade in trades:
                        if isinstance(trade, dict):
                            records.append(_normalize_record(trade, "backtest"))
        if _table_exists(connection, "scan_results"):
            for row in connection.execute("SELECT payload_json FROM scan_results").fetchall():
                try:
                    payload = json.loads(str(row["payload_json"]))
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and payload.get("outcome"):
                    records.append(_normalize_record(payload, "scan"))
        if _table_exists(connection, "paper_orders"):
            for row in connection.execute("SELECT payload_json FROM paper_orders WHERE realized_r IS NOT NULL").fetchall():
                try:
                    payload = json.loads(str(row["payload_json"]))
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    records.append(_normalize_paper_order(payload))
    return pd.DataFrame(records)


def load_paper_order_records(database_path: Path) -> pd.DataFrame:
    """Load all paper orders for lifecycle reporting."""

    if not database_path.exists():
        return pd.DataFrame()
    records: list[dict[str, object]] = []
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        if not _table_exists(connection, "paper_orders"):
            return pd.DataFrame()
        for row in connection.execute("SELECT payload_json FROM paper_orders").fetchall():
            try:
                payload = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append(_normalize_paper_order(payload, include_unrealized=True))
    return pd.DataFrame(records)


def load_paper_block_records(database_path: Path) -> pd.DataFrame:
    """Load blocked paper opportunities for guardrail reporting."""

    if not database_path.exists():
        return pd.DataFrame()
    records: list[dict[str, object]] = []
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        if not _table_exists(connection, "paper_blocks"):
            return pd.DataFrame()
        for row in connection.execute("SELECT payload_json FROM paper_blocks").fetchall():
            try:
                payload = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append(
                    {
                        "source": "paper_block",
                        "symbol": payload.get("symbol", ""),
                        "status": payload.get("status", ""),
                        "setup_family": payload.get("setup_family", ""),
                        "setup_subtype": payload.get("setup_subtype", ""),
                        "direction": payload.get("direction", ""),
                        "final_score": _numeric(payload.get("final_score")),
                        "reasons": "; ".join(str(item) for item in payload.get("reasons", []) if item),
                    }
                )
    return pd.DataFrame(records)


def score_bucket(score: float | int | None) -> str:
    """Bucket final score into stable calibration bands."""

    value = float(score or 0.0)
    if not math.isfinite(value):
        return "missing"
    if value < 50.0:
        return "<50"
    if value < 60.0:
        return "50-59"
    if value < 70.0:
        return "60-69"
    if value < 80.0:
        return "70-79"
    return "80+"


def _normalize_record(record: dict[str, object], source: str) -> dict[str, object]:
    return {
        "source": source,
        "symbol": record.get("symbol", ""),
        "style": record.get("style", ""),
        "setup_family": record.get("setup_family", ""),
        "setup_subtype": record.get("setup_subtype", "none"),
        "direction": record.get("direction", ""),
        "status": record.get("status", "approved" if source == "backtest" else ""),
        "execution_condition": record.get("execution_condition", "historical"),
        "provider": record.get("provider", ""),
        "session": record.get("session", ""),
        "regime": record.get("regime", ""),
        "technical_score": _numeric(record.get("technical_score")),
        "execution_score": _numeric(record.get("execution_score")),
        "context_score": _numeric(record.get("context_score")),
        "empirical_score": _numeric(record.get("empirical_score")),
        "final_score": _numeric(record.get("final_score") or record.get("score")),
        "risk_reward": _numeric(record.get("risk_reward")),
        "outcome": record.get("outcome", ""),
        "net_r": _numeric(record.get("net_r")),
        "tp1_hit": bool(record.get("tp1_hit", False)),
        "tp2_hit": bool(record.get("tp2_hit", False)),
        "tp3_hit": bool(record.get("tp3_hit", False)),
        "mae": _numeric(record.get("mae")),
        "mfe": _numeric(record.get("mfe")),
    }


def _normalize_paper_order(record: dict[str, object], include_unrealized: bool = False) -> dict[str, object]:
    request = record.get("request") if isinstance(record.get("request"), dict) else {}
    request_payload: dict[str, object] = request if isinstance(request, dict) else {}
    partials = record.get("partial_exits") if isinstance(record.get("partial_exits"), list) else []
    realized_r = _numeric(record.get("realized_r"))
    if not include_unrealized and not pd.notna(realized_r):
        return {}
    return {
        "source": "paper",
        "symbol": request_payload.get("symbol", ""),
        "style": request_payload.get("style", ""),
        "setup_family": request_payload.get("setup_family", ""),
        "setup_subtype": request_payload.get("setup_subtype", "none"),
        "direction": request_payload.get("direction", ""),
        "status": record.get("status", ""),
        "execution_condition": record.get("status", ""),
        "provider": request_payload.get("provider", ""),
        "session": request_payload.get("session", ""),
        "regime": "",
        "technical_score": float("nan"),
        "execution_score": float("nan"),
        "context_score": float("nan"),
        "empirical_score": float("nan"),
        "final_score": _numeric(request_payload.get("final_score")),
        "risk_reward": float("nan"),
        "outcome": "",
        "net_r": realized_r,
        "tp1_hit": record.get("tp1_exit_price") is not None or any(isinstance(item, dict) and item.get("target") == "tp1" for item in partials),
        "tp2_hit": record.get("tp2_exit_price") is not None or any(isinstance(item, dict) and item.get("target") == "tp2" for item in partials),
        "tp3_hit": record.get("tp3_exit_price") is not None or any(isinstance(item, dict) and item.get("target") == "tp3" for item in partials),
        "mae": _numeric(record.get("mae")),
        "mfe": _numeric(record.get("mfe")),
    }


def _score_bucket_report(records: pd.DataFrame) -> pd.DataFrame:
    grouped = records.groupby("score_bucket", dropna=False)
    report = grouped.apply(_aggregate_group, include_groups=False).reset_index()
    report["bucket_order"] = report["score_bucket"].apply(_bucket_order)
    report = report.sort_values("bucket_order").reset_index(drop=True)
    report["expectancy_delta_from_previous"] = report["expectancy"].diff().fillna(0.0).round(4)
    report["monotonic_expectancy_so_far"] = report["expectancy"].cummax().eq(report["expectancy"])
    return report


def _group_report(records: pd.DataFrame, column: str) -> pd.DataFrame:
    grouped = records.groupby(column, dropna=False)
    return grouped.apply(_aggregate_group, include_groups=False).reset_index()


def _status_separation_report(records: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    comparisons = [
        ("premium", "approved"),
        ("approved", "watchlist"),
        ("approved_or_premium", "watchlist"),
    ]
    for stronger, weaker in comparisons:
        strong_sample = _status_sample(records, stronger)
        weak_sample = _status_sample(records, weaker)
        strong = _aggregate_group(strong_sample) if not strong_sample.empty else _empty_aggregate()
        weak = _aggregate_group(weak_sample) if not weak_sample.empty else _empty_aggregate()
        rows.append(
            {
                "stronger_group": stronger,
                "weaker_group": weaker,
                "stronger_trades": int(strong["trades"]),
                "weaker_trades": int(weak["trades"]),
                "expectancy_delta": round(float(strong["expectancy"] - weak["expectancy"]), 4),
                "win_rate_delta": round(float(strong["win_rate"] - weak["win_rate"]), 2),
                "false_positive_delta": round(float(strong["false_positive_rate"] - weak["false_positive_rate"]), 2),
            }
        )
    return pd.DataFrame(rows)


def _layer_score_bucket_report(records: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for column in ["technical_score", "execution_score", "context_score", "empirical_score", "final_score"]:
        layer_records = records.copy()
        layer_records["score_bucket"] = layer_records[column].apply(score_bucket)
        frame = _score_bucket_report(layer_records)
        frame.insert(0, "layer", column.replace("_score", ""))
        frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _layer_predictiveness_report(records: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for column in ["technical_score", "execution_score", "context_score", "empirical_score", "final_score", "non_empirical_score"]:
        sample = records[[column, "r_value", "is_win"]].dropna()
        if len(sample) < 3 or sample[column].nunique() < 2:
            r_corr = 0.0
            win_corr = 0.0
        else:
            r_corr = float(sample[column].corr(sample["r_value"], method="spearman"))
            win_corr = float(sample[column].corr(sample["is_win"].astype(float), method="spearman"))
            if not math.isfinite(r_corr):
                r_corr = 0.0
            if not math.isfinite(win_corr):
                win_corr = 0.0
        rows.append(
            {
                "score": column,
                "samples": int(len(sample)),
                "spearman_to_expectancy": round(r_corr, 4),
                "spearman_to_win_flag": round(win_corr, 4),
            }
        )
    return pd.DataFrame(rows)


def _aggregate_group(group: pd.DataFrame) -> pd.Series:
    return pd.Series(
        {
            "trades": int(len(group)),
            "win_rate": round(float(group["is_win"].mean() * 100.0), 2) if len(group) else 0.0,
            "expectancy": round(float(group["r_value"].mean()), 4) if len(group) else 0.0,
            "tp1_hit_rate": round(float(group["tp1_hit"].mean() * 100.0), 2) if len(group) else 0.0,
            "tp2_hit_rate": round(float(group["tp2_hit"].mean() * 100.0), 2) if len(group) else 0.0,
            "tp3_hit_rate": round(float(group["tp3_hit"].mean() * 100.0), 2) if len(group) else 0.0,
            "avg_mae": round(float(group["mae"].mean()), 4) if len(group) else 0.0,
            "avg_mfe": round(float(group["mfe"].mean()), 4) if len(group) else 0.0,
            "false_positive_rate": round(float(group["is_loss"].mean() * 100.0), 2) if len(group) else 0.0,
        }
    )


def _top_k_report(records: pd.DataFrame, top_k_values: list[int]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    ranked = records.sort_values("final_score", ascending=False)
    for top_k in top_k_values:
        sample = ranked.head(top_k)
        aggregate = _aggregate_group(sample)
        payload = aggregate.to_dict()
        rows.append(
            {
                "top_k": top_k,
                "precision_at_top_k": payload["win_rate"],
                "expectancy_at_top_k": payload["expectancy"],
                **payload,
            }
        )
    return pd.DataFrame(rows)


def _conditional_combo_report(records: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    combo_specs = {
        "subtype_symbol": ["setup_subtype", "symbol"],
        "subtype_session": ["setup_subtype", "session"],
        "subtype_regime": ["setup_subtype", "regime"],
        "symbol_style": ["symbol", "style"],
    }
    for name, columns in combo_specs.items():
        grouped = records.groupby(columns, dropna=False)
        frame = grouped.apply(_aggregate_group, include_groups=False).reset_index()
        frame.insert(0, "combination", name)
        rows.append(frame)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _paper_lifecycle_report(paper_orders: pd.DataFrame) -> pd.DataFrame:
    if paper_orders.empty:
        return pd.DataFrame()
    grouped = paper_orders.groupby("status", dropna=False)
    rows = grouped.size().reset_index(name="orders")
    realized = paper_orders[pd.notna(paper_orders["net_r"])]
    if realized.empty:
        rows["avg_realized_r"] = 0.0
        return rows
    realized_group = realized.groupby("status", dropna=False)["net_r"].mean().round(4).reset_index(name="avg_realized_r")
    return rows.merge(realized_group, on="status", how="left").fillna({"avg_realized_r": 0.0})


def _paper_block_report(paper_blocks: pd.DataFrame) -> pd.DataFrame:
    if paper_blocks.empty:
        return pd.DataFrame()
    exploded = paper_blocks.assign(reason=paper_blocks["reasons"].str.split("; ")).explode("reason")
    return exploded.groupby(["reason", "symbol"], dropna=False).size().reset_index(name="blocked")


def _paper_execution_summary(records: pd.DataFrame, paper_orders: pd.DataFrame, paper_blocks: pd.DataFrame) -> pd.DataFrame:
    paper_realized = records[records["source"] == "paper"]
    executed = _aggregate_group(paper_realized) if not paper_realized.empty else _empty_aggregate()
    return pd.DataFrame(
        [
            {
                "paper_orders": int(len(paper_orders)),
                "paper_realized_orders": int(len(paper_realized)),
                "paper_blocked": int(len(paper_blocks)),
                "paper_expectancy": executed["expectancy"],
                "paper_precision": executed["win_rate"],
                "paper_false_positive_rate": executed["false_positive_rate"],
            }
        ]
    )


def _best_worst_report(records: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for column in ["setup_subtype", "symbol", "session", "regime"]:
        report = _group_report(records, column)
        report = report[report["trades"] >= 2].copy()
        if report.empty:
            continue
        for rank_kind, ranked in [
            ("best", report.sort_values(["expectancy", "win_rate"], ascending=False).head(5)),
            ("worst", report.sort_values(["expectancy", "win_rate"], ascending=True).head(5)),
        ]:
            for row in ranked.to_dict("records"):
                rows.append({"dimension": column, "value": row[column], "rank_kind": rank_kind, **{key: value for key, value in row.items() if key != column}})
    return pd.DataFrame(rows)


def _empirical_lift_report(records: pd.DataFrame, top_k_values: list[int]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for top_k in top_k_values:
        final_sample = records.sort_values("final_score", ascending=False).head(top_k)
        base_sample = records.sort_values("non_empirical_score", ascending=False).head(top_k)
        final_agg = _aggregate_group(final_sample)
        base_agg = _aggregate_group(base_sample)
        rows.append(
            {
                "top_k": top_k,
                "final_score_expectancy": final_agg["expectancy"],
                "non_empirical_expectancy": base_agg["expectancy"],
                "expectancy_lift": round(float(final_agg["expectancy"] - base_agg["expectancy"]), 4),
                "final_score_win_rate": final_agg["win_rate"],
                "non_empirical_win_rate": base_agg["win_rate"],
                "win_rate_lift": round(float(final_agg["win_rate"] - base_agg["win_rate"]), 2),
                "final_false_positive_rate": final_agg["false_positive_rate"],
                "non_empirical_false_positive_rate": base_agg["false_positive_rate"],
            }
        )
    return pd.DataFrame(rows)


def _write_csv(frame: pd.DataFrame, path: Path) -> Path:
    frame.to_csv(path, index=False)
    return path


def _write_json(payload: dict[str, object], path: Path) -> Path:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_summary(records: pd.DataFrame, outputs: dict[str, Path], path: Path) -> Path:
    approved = records[records["status"].isin(["approved", "premium", ""])]
    overall = _aggregate_group(records)
    approved_agg = _aggregate_group(approved) if not approved.empty else overall
    bucket_report = _score_bucket_report(records)
    monotonic = bool(bucket_report["monotonic_expectancy_so_far"].all()) if not bucket_report.empty else False
    status_report = _group_report(records, "status")
    separation = _status_separation_report(records)
    status_line = "; ".join(
        f"{row['status'] or 'unknown'}: expectancy {row['expectancy']:.4f} R, win {row['win_rate']:.2f}%"
        for row in status_report.to_dict("records")
    )
    separation_line = "; ".join(
        f"{row['stronger_group']} vs {row['weaker_group']}: expectancy delta {row['expectancy_delta']:.4f} R"
        for row in separation.to_dict("records")
    )
    lines = [
        "# Calibration Report",
        "",
        f"Records analyzed: {len(records)}",
        f"Overall win rate: {overall['win_rate']:.2f}%",
        f"Overall expectancy: {overall['expectancy']:.4f} R",
        f"Approved/premium false positive rate: {approved_agg['false_positive_rate']:.2f}%",
        f"Final-score bucket expectancy monotonic: {'yes' if monotonic else 'no'}",
        f"Lifecycle status performance: {status_line}",
        f"Lifecycle separation: {separation_line}",
        "",
        "Generated tables:",
    ]
    lines.extend(f"- {name}: `{file_path.name}`" for name, file_path in outputs.items() if name != "summary")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _summary_payload(records: pd.DataFrame, paper_orders: pd.DataFrame, paper_blocks: pd.DataFrame) -> dict[str, object]:
    """Return a compact JSON-friendly calibration summary."""

    bucket_report = _score_bucket_report(records)
    return {
        "records_analyzed": int(len(records)),
        "overall": _aggregate_group(records).to_dict(),
        "score_bucket_monotonic_expectancy": bool(bucket_report["monotonic_expectancy_so_far"].all()) if not bucket_report.empty else False,
        "status": _group_report(records, "status").to_dict("records"),
        "status_separation": _status_separation_report(records).to_dict("records"),
        "layer_predictiveness": _layer_predictiveness_report(records).to_dict("records"),
        "paper_orders": int(len(paper_orders)),
        "paper_blocks": int(len(paper_blocks)),
        "suggested_layer_weights": _suggested_layer_weights(records),
    }


def _is_win(row: pd.Series) -> bool:
    outcome = str(row.get("outcome", ""))
    if outcome:
        return outcome in WIN_OUTCOMES
    return float(row.get("r_value", 0.0)) > 0.0


def _is_loss(row: pd.Series) -> bool:
    outcome = str(row.get("outcome", ""))
    if outcome:
        return outcome in LOSS_OUTCOMES
    return float(row.get("r_value", 0.0)) < 0.0


def _r_value(row: pd.Series) -> float:
    net_r = row.get("net_r")
    if pd.notna(net_r):
        return float(net_r)
    outcome = str(row.get("outcome", ""))
    if outcome in {"win_clean", "win_messy"}:
        return 1.0
    if outcome == "partial_win":
        return 0.35
    if outcome == "breakeven":
        return 0.0
    if outcome == "timeout":
        return -0.1
    if outcome in LOSS_OUTCOMES:
        return -1.0
    return 0.0


def _non_empirical_score(row: pd.Series) -> float:
    values = [
        float(row.get("technical_score")) if pd.notna(row.get("technical_score")) else None,
        float(row.get("execution_score")) if pd.notna(row.get("execution_score")) else None,
        float(row.get("context_score")) if pd.notna(row.get("context_score")) else None,
    ]
    present = [value for value in values if value is not None]
    if not present:
        final_score = row.get("final_score")
        return float(final_score) if pd.notna(final_score) else 0.0
    if len(present) == 3:
        return round(present[0] * 0.40 + present[1] * 0.35 + present[2] * 0.25, 4)
    return round(sum(present) / len(present), 4)


def _bucket_order(bucket: str) -> int:
    return {"<50": 0, "50-59": 1, "60-69": 2, "70-79": 3, "80+": 4}.get(bucket, -1)


def _status_sample(records: pd.DataFrame, status: str) -> pd.DataFrame:
    if status == "approved_or_premium":
        return records[records["status"].isin(["approved", "premium", ""])]
    return records[records["status"] == status]


def _empty_aggregate() -> pd.Series:
    return pd.Series(
        {
            "trades": 0,
            "win_rate": 0.0,
            "expectancy": 0.0,
            "tp1_hit_rate": 0.0,
            "tp2_hit_rate": 0.0,
            "tp3_hit_rate": 0.0,
            "avg_mae": 0.0,
            "avg_mfe": 0.0,
            "false_positive_rate": 0.0,
        }
    )


def _suggested_layer_weights(records: pd.DataFrame) -> dict[str, object]:
    layer_columns = {
        "technical": "technical_score",
        "execution": "execution_score",
        "context": "context_score",
        "empirical": "empirical_score",
    }
    baseline = {"technical": 0.30, "execution": 0.30, "context": 0.24, "empirical": 0.16}
    if len(records) < 30:
        return {
            "status": "insufficient_samples",
            "minimum_recommended_samples": 30,
            "sample_count": int(len(records)),
            "weights": baseline,
        }
    correlations: dict[str, float] = {}
    for layer, column in layer_columns.items():
        sample = records[[column, "r_value"]].dropna()
        if len(sample) < 10 or sample[column].nunique() < 2:
            correlations[layer] = 0.05
            continue
        correlation = float(sample[column].corr(sample["r_value"], method="spearman"))
        correlations[layer] = max(0.05, correlation if math.isfinite(correlation) else 0.05)
    total = sum(correlations.values())
    raw_weights = {layer: value / total for layer, value in correlations.items()}
    bounded = {
        "technical": min(0.45, max(0.22, raw_weights["technical"])),
        "execution": min(0.42, max(0.22, raw_weights["execution"])),
        "context": min(0.34, max(0.14, raw_weights["context"])),
        "empirical": min(0.28, max(0.10, raw_weights["empirical"])),
    }
    bounded_total = sum(bounded.values())
    weights = {layer: round(value / bounded_total, 4) for layer, value in bounded.items()}
    return {
        "status": "estimated_from_spearman_correlations",
        "sample_count": int(len(records)),
        "correlations": {layer: round(value, 4) for layer, value in correlations.items()},
        "weights": weights,
    }


def _numeric(value: object) -> float:
    try:
        if value is None:
            return float("nan")
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)).fetchone()
    return row is not None
