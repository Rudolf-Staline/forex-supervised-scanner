# Paper Session Bundle Export (read-only, paper/demo only)

The session bundler packages the relevant paper/demo report artifacts from
`reports/` into one auditable zip with a checksummed manifest. It is strictly
read-only:

- it runs **no trading logic**,
- it never imports or calls **MT5**,
- it never calls **`order_send`**,
- it never mutates **`.env`** or the process environment,
- it never modifies the source report files,
- it works fully **offline** from local report files.

## Usage

From `apps/forex-scanner`:

```bash
python scripts/export_paper_session_bundle.py --reports-dir reports --output-dir reports/bundles --session-name paper-session-smoke
```

Options:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--reports-dir` | `reports` | Directory containing report artifacts |
| `--output-dir` | `reports/bundles` | Where the zip and manifests are written |
| `--session-name` | required | Bundle name (letters, digits, `.`, `_`, `-`) |
| `--strict` | off | Exit `1` when the bundle would be empty |

For the richest manifest, run the operator dashboard first so
`reports/operator_dashboard_summary.json` exists:

```bash
python scripts/operator_dashboard.py --reports-dir reports --export-json --export-txt
```

## Outputs

- `reports/bundles/<session-name>.zip` — report files stored under a
  `<session-name>/` folder inside the archive
- `reports/bundles/<session-name>_manifest.json`
- `reports/bundles/<session-name>_manifest.txt`

## Bundled files

All operator-dashboard inputs and outputs plus their TXT/CSV companions when
present: the six required reports (`local_mt5_realtime_validation.json`,
`realtime_command_center_summary.json`, `realtime_paper_supervisor_summary.json`,
`realtime_paper_positions.json`, `realtime_heartbeat.jsonl`,
`autonomous_scenario_suite.json`), the optional autonomous reports
(policy/readiness/evidence/recovery), `operator_dashboard_summary.json`,
`operator_dashboard_report.txt`, and the matching `.txt`/`.csv` exports.
Files that do not exist are listed under `missing_files` instead of failing
the export.

## Manifest fields

- `generated_at`, `session_name`, `reports_dir`
- `included_files` — name, `size_bytes`, `sha256`, `modified_at` per file
- `missing_files`
- `zip_sha256` — checksum of the bundle archive itself
- `final_operator_status` — taken from `operator_dashboard_summary.json` when
  present, otherwise `null` with a warning
- `blocking_reasons` / `warnings` — dashboard blocking/warning summary plus
  bundler warnings (missing required reports, empty bundle, missing dashboard)
- `safety_flags` — paper/demo-only assertions (no live trading, no
  `order_send`, no `.env` mutation, no MT5 requirement)
- `output_paths` — zip and manifest locations

## Exit codes

- `0` — bundle written (even with missing files; check the manifest)
- `1` — `--strict` and the bundle is empty
- `2` — invalid session name

## Testing

```bash
python -m pytest -q tests/test_session_bundle.py
```

The tests run offline, require no MT5, verify sha256 checksums against the
archive contents, and assert that the bundler performs no live trading, no
`order_send`, no source-report modification, and no `.env` mutation.
