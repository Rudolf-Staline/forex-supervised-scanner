"""Export local operational metrics in Prometheus textfile format."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import load_settings
from app.reporting.monitoring import build_prometheus_text, write_prometheus_textfile
from app.storage.database import Database


REQUIRED_METRICS = {
    "forex_scanner_execution_mode",
    "forex_scanner_broker_connected",
    "forex_scanner_account_sync_fresh",
    "forex_scanner_position_sync_fresh",
    "forex_scanner_reconciliation_fresh",
    "forex_scanner_operational_alerts_active",
    "forex_scanner_operational_incidents_active",
    "forex_scanner_metric_export_info",
}


def main() -> None:
    """Export or inspect Prometheus-compatible operational metrics."""

    settings = load_settings()
    parser = argparse.ArgumentParser(description="Export Forex scanner operational metrics in Prometheus textfile format.")
    parser.add_argument("--db", default=str(settings.database_absolute_path), help="SQLite database path.")
    parser.add_argument("--out", default=settings.monitoring.metrics_export_path, help="Metrics textfile output path.")
    parser.add_argument("--stdout", action="store_true", help="Print metrics text to stdout as well as writing the file.")
    parser.add_argument("--check", action="store_true", help="Validate that expected core metric names are present.")
    args = parser.parse_args()

    database = Database(Path(args.db))
    payload = {
        "snapshots": database.load_broker_health_snapshots(),
        "metrics": database.load_operational_metrics(),
        "alerts": database.load_operational_alerts(),
        "incidents": database.load_broker_incidents(),
        "anomalies": database.load_reconciliation_anomalies(),
        "orders": database.load_broker_orders(),
        "operator_controls": database.load_operator_controls(),
    }
    output_path = write_prometheus_textfile(Path(args.out), **payload)
    text = build_prometheus_text(**payload)
    if args.check:
        missing = sorted(name for name in REQUIRED_METRICS if name not in text)
        if missing:
            raise SystemExit(f"metrics_export=failed missing={','.join(missing)}")
    if args.stdout:
        print(text)
    print(f"metrics_export=ok path={output_path} bytes={output_path.stat().st_size} snapshots={len(payload['snapshots'])} metrics={len(payload['metrics'])}")


if __name__ == "__main__":
    main()
