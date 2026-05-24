# Daily Safe Operations Checklist

This feature generates an operational checklist for safe daily usage in paper/read-only contexts.

## Scope
- Generates guidance only.
- Does not execute validation commands.
- Does not call MT5.
- Does not send orders.
- Does not modify `.env`.
- Does not mutate `os.environ`.

## CLI
```bash
python scripts/daily_safe_ops_checklist.py --mode paper --export-json --export-md --export-txt
python scripts/daily_safe_ops_checklist.py --mode mt5-readonly --export-json --export-md --export-txt
python scripts/daily_safe_ops_checklist.py --mode analysis-only --export-json --export-md --export-txt
```

## Exports
- `reports/daily_safe_ops_checklist.json`
- `reports/daily_safe_ops_checklist.md`
- `reports/daily_safe_ops_checklist.txt`

## Safety Banner
The CLI always prints:

`Daily checklist is operational guidance only; it does not authorize order execution.`
