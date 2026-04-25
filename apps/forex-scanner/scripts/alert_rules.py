"""Evaluate operational alert rules and optionally route alerts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import load_settings
from app.reporting.alerting import evaluate_alert_rules, generate_alert_report, link_alerts_to_incidents, route_alerts
from app.storage.database import Database


def main() -> None:
    """Evaluate monitoring rules against persisted operational state."""

    settings = load_settings()
    parser = argparse.ArgumentParser(description="Evaluate operational alert rules and write operator alert summaries.")
    parser.add_argument("--db", default=str(settings.database_absolute_path), help="SQLite database path.")
    parser.add_argument("--out", default="reports/alerts", help="Output directory for alert summaries.")
    parser.add_argument("--route", action="store_true", help="Route alerts to local sink and optional webhook.")
    args = parser.parse_args()

    database = Database(Path(args.db))
    previous = database.load_operational_alerts()
    incidents = database.load_broker_incidents()
    bundle = evaluate_alert_rules(
        snapshots=database.load_broker_health_snapshots(),
        metrics=database.load_operational_metrics(),
        previous_alerts=previous,
        incidents=incidents,
        anomalies=database.load_reconciliation_anomalies(),
        orders=database.load_broker_orders(),
        settings=settings,
        operator_controls=database.load_operator_controls(),
    )
    generated = [*bundle.triggered_alerts, *bundle.resolved_alerts]
    if generated:
        database.save_operational_alerts(generated)
        database.save_broker_incidents(link_alerts_to_incidents(incidents, generated))
    deliveries = route_alerts(generated, settings).records if args.route and generated else []
    outputs = generate_alert_report([*previous, *generated], bundle.evaluations, deliveries, Path(args.out))
    print(f"alert_rules=ok triggered={len(bundle.triggered_alerts)} resolved={len(bundle.resolved_alerts)} deliveries={len(deliveries)}")
    for name, path in outputs.items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()
