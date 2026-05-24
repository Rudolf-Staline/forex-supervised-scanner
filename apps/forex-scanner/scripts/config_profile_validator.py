from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.profile_validator import SUPPORTED_PROFILES, validate_profile

REPORTS_DIR = PROJECT_ROOT / "reports"
JSON_REPORT_PATH = REPORTS_DIR / "config_profile_validation.json"
TXT_REPORT_PATH = REPORTS_DIR / "config_profile_validation.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate safe runtime configuration profiles.")
    parser.add_argument("--profile", required=True, choices=sorted(SUPPORTED_PROFILES))
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--export-txt", action="store_true")
    parser.add_argument("--show-recommendations", action="store_true")
    return parser.parse_args()


def export_json(payload: dict[str, object]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    JSON_REPORT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def export_txt(payload: dict[str, object]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for key, value in payload.items():
        lines.append(f"{key}={json.dumps(value, ensure_ascii=False)}")
    TXT_REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_report(payload: dict[str, object], show_recommendations: bool) -> None:
    print(f"profile={payload['profile']}")
    print(f"status={payload['status']}")
    print(f"variables_ok={len(payload['variables_ok'])}")
    print(f"variables_missing={','.join(payload['variables_missing'])}")
    print(f"variables_wrong={','.join(payload['variables_wrong'].keys())}")
    print(f"dangerous_flags={','.join(payload['dangerous_flags'])}")
    if show_recommendations:
        print("recommendations=")
        for rec in payload["recommendations"]:
            print(f"- {rec}")


def main() -> int:
    args = parse_args()
    report = validate_profile(args.profile)
    payload = report.to_dict()

    print_report(payload, show_recommendations=args.show_recommendations)

    if args.export_json:
        export_json(payload)
    if args.export_txt:
        export_txt(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
