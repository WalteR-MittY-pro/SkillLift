from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.skilllift_tau2_run_domain import (
    _existing_fusion_result,
    _final_test_artifact_path,
    _final_train_artifact_path,
    _resume_enabled,
)


def test_domain_resume_uses_only_final_train_artifact(tmp_path: Path) -> None:
    final_path = _final_train_artifact_path(tmp_path, "airline", "airline_book_00", 3)
    middle_path = tmp_path / "round_metrics" / "airline_book_00" / "outer_002.json"
    middle_path.parent.mkdir(parents=True)
    middle_path.write_text("{}", encoding="utf-8")

    assert not final_path.exists()

    final_path.parent.mkdir(parents=True)
    final_path.write_text("{}", encoding="utf-8")

    assert final_path.exists()


def test_domain_resume_skips_fusion_only_when_three_final_files_exist(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "master_skill_fusion" / "airline"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "fusion_artifact.json").write_text("{}", encoding="utf-8")
    (artifact_dir / "MASTER_SKILL.md").write_text("# MASTER_SKILL\n", encoding="utf-8")

    assert _existing_fusion_result(tmp_path, "airline") is None

    (artifact_dir / "best_skill_bundle.json").write_text("{}", encoding="utf-8")

    result = _existing_fusion_result(tmp_path, "airline")
    assert result is not None
    assert result.artifact_path == artifact_dir / "fusion_artifact.json"
    assert result.master_skill_path == artifact_dir / "MASTER_SKILL.md"
    assert result.best_skill_bundle_path == artifact_dir / "best_skill_bundle.json"


def test_domain_resume_uses_final_test_artifact_and_flags(tmp_path: Path) -> None:
    final_path = _final_test_artifact_path(tmp_path, "airline", "airline_cancel_test_00")
    final_path.parent.mkdir(parents=True)
    final_path.write_text(
        json.dumps({"per_task_scores": [{"reward": 1.0}], "heldout_test_score": 1.0}),
        encoding="utf-8",
    )

    assert final_path.exists()
    assert _resume_enabled(argparse.Namespace(no_resume=False, force=False))
    assert not _resume_enabled(argparse.Namespace(no_resume=True, force=False))
    assert not _resume_enabled(argparse.Namespace(no_resume=False, force=True))
