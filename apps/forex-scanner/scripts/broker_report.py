"""Generate broker execution and reconciliation reports from SQLite."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import load_settings
from app.execution.operations import assess_resume_readiness
from app.reporting.broker import generate_broker_execution_report
from app.reporting.monitoring import write_prometheus_textfile
from app.storage.database import Database


def main() -> None:
    """CLI entry point for broker execution reporting."""

    settings = load_settings()
    parser = argparse.ArgumentParser(description="Generate broker execution reports.")
    parser.add_argument("--db", default=str(settings.database_absolute_path), help="SQLite database path.")
    parser.add_argument("--out", default="reports/broker", help="Output directory.")
    args = parser.parse_args()

    database = Database(Path(args.db))
    snapshots = database.load_broker_health_snapshots()
    controls = database.load_operator_controls()
    readiness = (
        assess_resume_readiness(
            snapshots[-1],
            database.load_broker_incidents(),
            database.load_operational_alerts(),
            database.load_reconciliation_anomalies(),
            controls,
            settings,
        )
        if snapshots
        else None
    )
    outputs = generate_broker_execution_report(
        database.load_broker_orders(),
        database.load_reconciliation_anomalies(),
        Path(args.out),
        incidents=database.load_broker_incidents(),
        health_snapshots=snapshots,
        alerts=database.load_operational_alerts(),
        metrics=database.load_operational_metrics(),
        operator_controls=controls,
        resume_readiness=readiness,
    )
    if settings.monitoring.metrics_export_enabled:
        outputs["prometheus_metrics"] = write_prometheus_textfile(
            Path(settings.monitoring.metrics_export_path),
            snapshots=snapshots,
            metrics=database.load_operational_metrics(),
            alerts=database.load_operational_alerts(),
            incidents=database.load_broker_incidents(),
            anomalies=database.load_reconciliation_anomalies(),
            orders=database.load_broker_orders(),
            operator_controls=database.load_operator_controls(),
        )
    print("broker_report=ok")
    for name, path in outputs.items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()
