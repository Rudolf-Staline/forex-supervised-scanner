from __future__ import annotations

from pathlib import Path

from app.experiments.experiment_registry import ExperimentRegistry


def test_create_experiment(tmp_path: Path) -> None:
    registry = ExperimentRegistry(reports_dir=tmp_path / "reports")

    created = registry.create_experiment(
        name="Initial paper analysis",
        description="Track paper-mode reports",
        tags=["paper"],
        status="draft",
    )

    assert created["experiment_id"].startswith("exp-")
    assert created["status"] == "draft"
    assert created["safety_status"] == "paper_only"
    assert (tmp_path / "reports" / "experiments" / f"{created['experiment_id']}.json").exists()


def test_listing_and_show(tmp_path: Path) -> None:
    registry = ExperimentRegistry(reports_dir=tmp_path / "reports")
    created = registry.create_experiment(name="A", description="B", tags=["x"], status="draft")

    listing = registry.list_experiments()
    shown = registry.show_experiment(created["experiment_id"])

    assert len(listing) == 1
    assert listing[0]["experiment_id"] == created["experiment_id"]
    assert shown["name"] == "A"


def test_attach_report_and_status_transition(tmp_path: Path) -> None:
    registry = ExperimentRegistry(reports_dir=tmp_path / "reports")
    created = registry.create_experiment(name="A", description="B", tags=[], status="draft")

    attached = registry.attach_report(created["experiment_id"], "reports/paper_run.json")
    running = registry.set_status(created["experiment_id"], "running")
    completed = registry.set_status(created["experiment_id"], "completed")

    assert "reports/paper_run.json" in attached["attached_reports"]
    assert running["status"] == "running"
    assert completed["status"] == "completed"


def test_export_summary(tmp_path: Path) -> None:
    registry = ExperimentRegistry(reports_dir=tmp_path / "reports")
    registry.create_experiment(name="A", description="B", tags=[], status="draft")
    registry.create_experiment(name="C", description="D", tags=[], status="completed")

    summary = registry.export_summary()

    assert summary["total"] == 2
    assert summary["status_counts"]["draft"] == 1
    assert summary["status_counts"]["completed"] == 1
    assert (tmp_path / "reports" / "experiments" / "summary.json").exists()


def test_no_mutation_outside_experiments_dir(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    registry = ExperimentRegistry(reports_dir=reports_dir)

    registry.create_experiment(name="A", description="B", tags=[], status="draft")

    touched = [p for p in reports_dir.rglob("*") if p.is_file()]
    assert touched
    assert all("experiments" in p.parts for p in touched)
