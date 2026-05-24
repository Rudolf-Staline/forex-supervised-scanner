"""Local experiment registry for backtests/forward tests metadata only."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

ALLOWED_STATUS = {"draft", "running", "completed", "discarded"}
SAFETY_STATUS = "paper_only"


@dataclass(slots=True)
class Experiment:
    experiment_id: str
    created_at: str
    updated_at: str
    name: str
    description: str
    tags: list[str]
    status: str
    git_commit_if_available: str | None
    branch_if_available: str | None
    command_examples: list[str]
    attached_reports: list[str]
    notes: list[str]
    safety_status: str


class ExperimentRegistry:
    def __init__(self, reports_dir: Path) -> None:
        self.reports_dir = reports_dir
        self.experiments_dir = reports_dir / "experiments"
        self.experiments_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.experiments_dir / "experiments.jsonl"

    def create_experiment(
        self,
        *,
        name: str,
        description: str,
        tags: list[str],
        status: str,
    ) -> dict:
        if status not in ALLOWED_STATUS:
            raise ValueError(f"invalid status: {status}")
        now = _iso_now()
        experiment_id = f"exp-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
        payload = Experiment(
            experiment_id=experiment_id,
            created_at=now,
            updated_at=now,
            name=name,
            description=description,
            tags=tags,
            status=status,
            git_commit_if_available=_git_value(["rev-parse", "HEAD"]),
            branch_if_available=_git_value(["branch", "--show-current"]),
            command_examples=[
                "python scripts/experiment_registry.py --list",
                f"python scripts/experiment_registry.py --show {experiment_id}",
            ],
            attached_reports=[],
            notes=[],
            safety_status=SAFETY_STATUS,
        )
        data = asdict(payload)
        self._write_experiment(data)
        return data

    def list_experiments(self) -> list[dict]:
        return [self._read_experiment(path.stem) for path in sorted(self.experiments_dir.glob("exp-*.json"))]

    def show_experiment(self, experiment_id: str) -> dict:
        return self._read_experiment(experiment_id)

    def attach_report(self, experiment_id: str, report_path: str) -> dict:
        experiment = self._read_experiment(experiment_id)
        if report_path not in experiment["attached_reports"]:
            experiment["attached_reports"].append(report_path)
        experiment["updated_at"] = _iso_now()
        self._write_experiment(experiment)
        return experiment

    def set_status(self, experiment_id: str, status: str) -> dict:
        if status not in ALLOWED_STATUS:
            raise ValueError(f"invalid status: {status}")
        experiment = self._read_experiment(experiment_id)
        experiment["status"] = status
        experiment["updated_at"] = _iso_now()
        self._write_experiment(experiment)
        return experiment

    def export_summary(self) -> dict:
        items = self.list_experiments()
        counts: dict[str, int] = {state: 0 for state in sorted(ALLOWED_STATUS)}
        for item in items:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        summary = {
            "generated_at": _iso_now(),
            "total": len(items),
            "status_counts": counts,
            "experiments": [
                {
                    "experiment_id": item["experiment_id"],
                    "name": item["name"],
                    "status": item["status"],
                    "attached_reports": len(item["attached_reports"]),
                }
                for item in items
            ],
        }
        self._write_json(self.experiments_dir / "summary.json", summary)
        return summary

    def _read_experiment(self, experiment_id: str) -> dict:
        path = self.experiments_dir / f"{experiment_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"unknown experiment_id={experiment_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_experiment(self, payload: dict) -> None:
        experiment_path = self.experiments_dir / f"{payload['experiment_id']}.json"
        self._write_json(experiment_path, payload)
        line = json.dumps(payload, ensure_ascii=False)
        with self.jsonl_path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _git_value(args: list[str]) -> str | None:
    try:
        output = subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return None
    return output or None
