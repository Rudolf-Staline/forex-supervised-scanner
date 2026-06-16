"""Decompose a deduplicated OOS trade registry. Reporting only; no orders, no re-backtest.

Consumes the registry written by scripts/walk_forward_report.py
(``<output-dir>/oos_trade_registry.csv``) and emits gross/net expectancy with
GROUPED bootstrap CIs (cluster-by-pair + temporal block), an effective sample
size for correlated majors, per-pair metrics, half-period robustness, and a
score->expectancy calibration with per-pair Spearman CIs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.reporting.edge_decomposition import DEFAULT_BLOCK_SIZE, decompose, load_registry, report_to_text, write_reports


def main() -> None:
    parser = argparse.ArgumentParser(description="OOS registry decomposition. Reporting only; no orders are sent.")
    parser.add_argument("--registry", default=str(PROJECT_ROOT / "reports" / "real" / "oos_trade_registry.csv"))
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "reports" / "real"))
    parser.add_argument("--bootstrap-resamples", type=int, default=3000)
    parser.add_argument("--block-size", type=int, default=DEFAULT_BLOCK_SIZE)
    parser.add_argument("--buckets", type=int, default=10)
    args = parser.parse_args()

    registry_path = Path(args.registry)
    if not registry_path.is_file():
        raise SystemExit(
            f"registry not found: {registry_path}. Run scripts/walk_forward_report.py first "
            f"(it writes oos_trade_registry.csv into --output-dir)."
        )
    df = load_registry(registry_path)
    result = decompose(df, resamples=args.bootstrap_resamples, block_size=args.block_size, n_buckets=args.buckets)
    outputs = write_reports(result, Path(args.output_dir))
    print(report_to_text(result))
    print(f"json_export={outputs['json']}")
    print(f"txt_export={outputs['txt']}")


if __name__ == "__main__":
    main()
