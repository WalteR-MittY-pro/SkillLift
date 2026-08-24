from __future__ import annotations

import json
from pathlib import Path

import pytest

from skilllift_eval.runners.skillsbench_adapter import SkillsBenchCommand
from skilllift_eval.skillsbench_cli import build_execution_plan, load_and_validate_split


ROOT = Path(__file__).resolve().parents[2]
SPLIT_PATH = ROOT / "skilllift_eval" / "benchmarks" / "skillsbench_split_v1.json"
TASKS_ROOT = ROOT / "skillsbench" / "tasks"


def test_frozen_split_covers_all_default_tasks() -> None:
    split = load_and_validate_split(SPLIT_PATH, TASKS_ROOT)

    assert len(split.domains) == 8
    assert sum(len(domain.batches) for domain in split.domains.values()) == 9
    assert len(split.train_task_ids) == 36
    assert len(split.test_task_ids) == 51
    assert split.train_task_ids.isdisjoint(split.test_task_ids)
    assert split.train_task_ids | split.test_task_ids == {
        path.parent.name for path in TASKS_ROOT.glob("*/task.md")
    }
    assert all(
        len(batch.task_ids) == 4
        for domain in split.domains.values()
        for batch in domain.batches
    )


def test_split_rejects_task_content_drift(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks"
    for task_id in ("task-a", "task-b", "task-c", "task-d"):
        task_dir = task_root / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "task.md").write_text("changed\n", encoding="utf-8")
    manifest = {
        "version": 1,
        "domains": {
            "demo": {
                "batches": [
                    {
                        "batch_id": "b00",
                        "tasks": [
                            {"task_id": task_id, "task_md_sha256": "0" * 64}
                            for task_id in ("task-a", "task-b", "task-c", "task-d")
                        ],
                    }
                ],
                "test": [],
            }
        },
    }
    path = tmp_path / "split.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        load_and_validate_split(path, task_root, expected_counts=None)


def test_check_plan_uses_isolated_jobs_and_expected_outputs(tmp_path: Path) -> None:
    split = load_and_validate_split(SPLIT_PATH, TASKS_ROOT)
    plan = build_execution_plan(
        split=split,
        categories=list(split.domains),
        tasks_root=TASKS_ROOT,
        run_root=tmp_path / "run",
        bench_executable=ROOT / "skillsbench" / ".venv" / "bin" / "bench",
        model="vllm/test-model",
        sandbox="docker",
    )

    assert plan["summary"] == {
        "domains": 8,
        "train_batches": 9,
        "train_tasks": 36,
        "test_tasks": 51,
        "overlap": 0,
        "missing": 0,
        "status": "ready",
    }
    assert len(plan["train"]) == 9
    assert len(plan["test"]) == 51
    commands = [
        SkillsBenchCommand.from_dict(command)
        for item in plan["train"]
        for command in item["commands"]
    ] + [SkillsBenchCommand.from_dict(item["command"]) for item in plan["test"]]
    assert sum(len(item["commands"]) for item in plan["train"]) == 9 * 16
    jobs_dirs = {command.jobs_dir for command in commands}
    assert len(jobs_dirs) == len(commands)
    assert all(command.skills_dir.is_absolute() for command in commands)
    assert all(command.jobs_dir.is_absolute() for command in commands)
    assert all(command.tasks_dir == TASKS_ROOT.resolve() for command in commands)
    assert all(item["output_path"].endswith(("round_result.json", "score.json")) for item in plan["train"] + plan["test"])


def test_execution_plan_preserves_virtualenv_python_symlink(tmp_path: Path) -> None:
    executable = tmp_path / "venv" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(Path("/usr/bin/python3"))
    split = load_and_validate_split(SPLIT_PATH, TASKS_ROOT)

    plan = build_execution_plan(
        split=split,
        categories=["media-content-production"],
        tasks_root=TASKS_ROOT,
        run_root=tmp_path / "run",
        bench_executable=executable,
        model="vllm/test-model",
        sandbox="docker",
    )

    assert plan["train"][0]["commands"][0]["bench_executable"] == str(executable)
