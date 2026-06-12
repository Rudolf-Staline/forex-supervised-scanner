# Paper Session Review

The Paper Session Review is a read-only post-session operator handoff tool for paper/demo sessions.

It composes the existing offline reporting stack:

```text
Operator Dashboard -> Paper Performance Analytics -> optional Paper Session Bundle Export -> Review Summary
```

It is intended for the end of a paper/demo session, when an operator wants one compact review that points to the generated dashboard, performance, and bundle artifacts.

## Safety scope

The review is strictly offline and paper/demo only:

- no live trading;
- no broker-live execution;
- no `order_send`;
- no MT5 import;
- no `.env` mutation;
- no daemon;
- no infinite loop;
- no strategy execution;
- no broker order submission.

It only reads existing report/order artifacts and writes review outputs under `reports/`.

## CLI

Run from `apps/forex-scanner`:

```bash
python scripts/paper_session_review.py --reports-dir reports --export-json --export-txt
```

To also create an auditable session bundle:

```bash
python scripts/paper_session_review.py --reports-dir reports --export-json --export-txt --export-bundle --session-name paper-session-review
```

Strict mode exits non-zero unless the review status is ready or warning-level:

```bash
python scripts/paper_session_review.py --reports-dir reports --export-json --export-txt --strict
```

## Outputs

When exports are enabled, the review writes:

```text
reports/paper_session_review_summary.json
reports/paper_session_review_report.txt
```

It also refreshes component artifacts that the review depends on:

```text
reports/operator_dashboard_summary.json
reports/operator_dashboard_report.txt
reports/paper_performance_summary.json
reports/paper_performance_report.txt
```

When `--export-bundle` is used, the review additionally writes:

```text
reports/bundles/<session-name>.zip
reports/bundles/<session-name>_manifest.json
reports/bundles/<session-name>_manifest.txt
```

## Review statuses

The final review status is one of:

- `PAPER_SESSION_REVIEW_READY`
- `PAPER_SESSION_REVIEW_WARN`
- `PAPER_SESSION_REVIEW_INCOMPLETE`
- `PAPER_SESSION_REVIEW_BLOCKED`

A review is blocked if unsafe source flags are detected, for example live execution, broker-live order submission, an `order_send` call, or `.env` mutation.

A review is incomplete if required reports are missing or stale.

## Typical workflow

1. Run the local MT5 realtime validation, if working from a local Windows MT5 machine.
2. Run the realtime paper command center.
3. Run the paper position manager or command center position lifecycle step.
4. Run the paper session review.
5. Inspect `paper_session_review_report.txt`.
6. If needed, rerun with `--export-bundle` to archive all review artifacts.

## Example command sequence

```bash
python scripts/operator_dashboard.py --reports-dir reports --export-json --export-txt
python scripts/paper_performance_report.py --reports-dir reports --export-json --export-txt
python scripts/paper_session_review.py --reports-dir reports --export-json --export-txt --export-bundle --session-name paper-session-review
```

The first two commands are optional if the review is run with exports enabled, because it refreshes the dashboard and performance artifacts before producing the review.

## Interpretation

Passing paper/demo review artifacts do not authorize live trading. They are diagnostic evidence for a human operator review only.

If the review is incomplete, regenerate missing/stale reports and rerun the review.

If the review is blocked, resolve the blocking reasons before treating the session as review-ready.
