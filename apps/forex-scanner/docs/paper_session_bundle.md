# Paper Session Bundle Export (read-only, paper/demo only)

The paper session bundle exporter packages **existing** paper/demo report files
from `reports/` into a portable audit archive. It is an archive/export feature
only: it does not create reports, does not run trading logic, does not call MT5,
does not call `order_send`, and does not submit broker orders. Passing reports
inside a bundle are evidence for manual review only and **do not authorize live
trading**.

## Command

Run from `apps/forex-scanner`:

```bash
python scripts/export_paper_session_bundle.py --reports-dir reports --output-dir reports/bundles --session-name paper-session-smoke
```

Options:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--reports-dir` | `reports` | Directory containing existing report artifacts to read. |
| `--output-dir` | `reports/bundles` | Directory where the ZIP and manifests are written. |
| `--session-name` | required | Safe bundle name using letters, digits, `.`, `_`, or `-`. |
| `--include-optional` | enabled | Includes optional autonomous companion reports when present and records optional gaps separately. |
| `--strict` | off | Exits non-zero when any required bundle report is missing. |

The exporter is bounded and one-shot; it is not a daemon and has no infinite
loop. It writes only these generated outputs and never mutates source reports or
`.env`:

- `reports/bundles/<session-name>.zip`
- `reports/bundles/<session-name>_manifest.json`
- `reports/bundles/<session-name>_manifest.txt`

## Bundle contents

Required paper/demo artifacts are included when present and listed in
`missing_files` when absent:

- `operator_dashboard_summary.json`
- `operator_dashboard_report.txt`
- `local_mt5_realtime_validation.json`
- `local_mt5_realtime_validation.txt`
- `local_mt5_realtime_samples.csv`
- `realtime_command_center_summary.json`
- `realtime_command_center_report.txt`
- `realtime_paper_supervisor_summary.json`
- `realtime_paper_supervisor_report.txt`
- `realtime_paper_positions.json`
- `realtime_paper_positions.txt`
- `realtime_heartbeat.jsonl`
- `autonomous_scenario_suite.json`
- `autonomous_scenario_suite.txt`

Optional autonomous artifacts are included when present and listed in
`optional_missing_files` when absent:

- `autonomous_policy_report.json`
- `autonomous_policy_report.txt`
- `autonomous_readiness_report.json`
- `autonomous_readiness_report.txt`
- `autonomous_evidence_report.json`
- `autonomous_evidence_report.txt`
- `autonomous_recovery_plan.json`
- `autonomous_recovery_plan.txt`

The ZIP stores report files under `<session-name>/` and also contains
`<session-name>/manifest.json` and `<session-name>/manifest.txt` for offline
review.

## Manifest fields

The JSON and TXT manifests include:

- `generated_at`, `session_name`, `reports_dir`, and `output_dir`
- `bundle_path`, `manifest_json_path`, and `manifest_txt_path`
- `included_files` with archive path, SHA-256 checksum, file size in bytes,
  modified time, and required/optional status
- `missing_files` for required artifacts
- `optional_missing_files` for optional artifacts
- `safety_flags` asserting paper/demo-only, no live trading, no broker-live
  execution, no broker order submission, no `order_send`, no `.env` mutation,
  no terminal API requirement, no daemon, and no infinite loop
- `final_operator_status`, `blocking_reasons`, and dashboard `warnings` when
  `operator_dashboard_summary.json` exists

If the operator dashboard summary is absent, the bundle still exports and the
manifest records that the final operator status is unavailable.

## Recommended review workflow

1. Generate the underlying paper/demo reports.
2. Generate the operator dashboard summary and TXT report.
3. Export the paper session bundle.
4. Share or archive the ZIP plus external manifests.
5. Review the manifest manually. A clean bundle is not go-live approval; live
   trading remains prohibited by project policy.

## Testing

```bash
python -m pytest -q tests/test_session_bundle.py
```
