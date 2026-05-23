"""Read-only local dashboard for multi-asset bot monitoring."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from app.dashboard.data_loader import load_dashboard_data, safe_get


READ_ONLY_NOTE = "Read-only dashboard. Trading actions are disabled by design."


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local read-only monitoring dashboard")
    parser.add_argument("--watchlist", default="multi_asset_demo", help="Watchlist name")
    parser.add_argument("--refresh-seconds", type=int, default=10, help="Auto-refresh interval")
    parser.add_argument("--base-dir", default=".", help="Project root containing reports/")
    return parser


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _row(payload: dict[str, Any], key: str, default: str = "No data yet") -> tuple[str, Any]:
    return key, payload.get(key, default)


def run_dashboard(watchlist: str, refresh_seconds: int, base_dir: str) -> None:
    import streamlit as st

    st.set_page_config(page_title="Multi-Asset Bot Dashboard", layout="wide")
    st.title("Multi-Asset Bot Dashboard (Local)")
    st.caption(READ_ONLY_NOTE)
    st.caption(f"watchlist={watchlist} | refresh={refresh_seconds}s")

    data = load_dashboard_data(Path(base_dir))
    summary = data.signal_report_summary
    backtest = data.backtest_summary
    optimizer = data.threshold_optimizer_summary
    journal = data.signal_journal

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Runtime")
        runtime_rows = [
            _row(summary, "current_mode"),
            _row(summary, "broker"),
            _row(summary, "provider"),
            _row(summary, "watchlist", watchlist),
            _row(summary, "safety_status"),
            _row(summary, "ALLOW_LIVE_TRADING"),
            _row(summary, "MT5_DEMO_ONLY"),
            _row(summary, "ALLOW_MULTI_ASSET_DEMO_TRADING"),
            _row(summary, "ENABLE_DEMO_EXECUTION"),
            _row(summary, "next_tradable_sessions"),
            _row(summary, "tradable_symbols_now"),
            _row(summary, "off_hours_symbols"),
        ]
        st.table(runtime_rows)

    with col2:
        st.subheader("Safety status")
        allow_live = str(summary.get("ALLOW_LIVE_TRADING", "false")).lower()
        demo_only = str(summary.get("MT5_DEMO_ONLY", "true")).lower()
        execution_mode = str(summary.get("execution_mode", "paper")).lower()
        scan_only = safe_get(summary, "scan_only", default={})

        st.write(f"- live trading disabled: {'yes' if allow_live != 'true' else 'no'}")
        st.write(f"- demo only: {'yes' if demo_only == 'true' else 'no'}")
        st.write(f"- paper mode: {'yes' if execution_mode == 'paper' else 'no'}")
        st.write(
            f"- scan_only commodities/indices: {scan_only if scan_only else 'No data yet'}"
        )
        daily_limits = summary.get("daily_limits_status", "No data yet")
        st.write(f"- daily limits status: {daily_limits}")

    st.subheader("Signal filters")
    asset_class = st.selectbox("asset_class", ["all"] + sorted({str(x.get('asset_class')) for x in journal if isinstance(x, dict) and x.get('asset_class') is not None}))
    symbol = st.selectbox("symbol", ["all"] + sorted({str(x.get('symbol')) for x in journal if isinstance(x, dict) and x.get('symbol') is not None}))
    setup = st.selectbox("setup", ["all"] + sorted({str(x.get('setup')) for x in journal if isinstance(x, dict) and x.get('setup') is not None}))
    status = st.selectbox("status", ["all"] + sorted({str(x.get('status')) for x in journal if isinstance(x, dict) and x.get('status') is not None}))
    min_score = st.number_input("min_score", min_value=0.0, max_value=1.0, value=0.0, step=0.01)
    session = st.selectbox("session", ["all"] + sorted({str(x.get('session')) for x in journal if isinstance(x, dict) and x.get('session') is not None}))

    filtered = []
    for row in journal:
        if not isinstance(row, dict):
            continue
        score = float(row.get("score", 0.0) or 0.0)
        if asset_class != "all" and str(row.get("asset_class")) != asset_class:
            continue
        if symbol != "all" and str(row.get("symbol")) != symbol:
            continue
        if setup != "all" and str(row.get("setup")) != setup:
            continue
        if status != "all" and str(row.get("status")) != status:
            continue
        if session != "all" and str(row.get("session")) != session:
            continue
        if score < float(min_score):
            continue
        filtered.append(row)

    st.subheader("Latest signals")
    st.dataframe(filtered[-100:] if filtered else [{"message": "No data yet"}], width="stretch")

    st.subheader("Near-miss signals")
    st.json(summary.get("near_miss_signals", "No data yet"))
    st.subheader("Best scores")
    st.json(summary.get("best_scores", "No data yet"))
    st.subheader("Best setups")
    st.json(summary.get("best_setups", "No data yet"))
    st.subheader("Rejection reasons")
    st.json(summary.get("rejection_reasons", "No data yet"))
    st.subheader("Spread/ATR by symbol")
    st.json(summary.get("spread_atr_by_symbol", "No data yet"))

    st.subheader("Backtest summary")
    st.json(backtest or "No data yet")
    st.subheader("Threshold optimizer summary")
    st.json(optimizer or "No data yet")


def main() -> int:
    args = _build_parser().parse_args()
    try:
        run_dashboard(args.watchlist, args.refresh_seconds, args.base_dir)
    except ImportError:
        print("Install streamlit to run the dashboard: pip install streamlit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
