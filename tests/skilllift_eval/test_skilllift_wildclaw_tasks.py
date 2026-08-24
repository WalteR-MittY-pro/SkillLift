from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from skilllift_eval.cli import build_parser
from skilllift_eval.runners.skilllift_wildclaw_tasks import (
    WildClawBatchSettings,
    discover_wildclaw_tasks,
    run_wildclaw_batch,
)


ROOT = Path(__file__).resolve().parents[2]


def _write_task(root: Path, category: int, task_number: int) -> str:
    category_name = f"{category:02d}_Domain_{category}"
    task_id = f"{category_name}_task_{task_number}_fixture"
    path = root / "tasks" / category_name / f"{task_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nid: {task_id}\n---\n\nFixture task.\n", encoding="utf-8")
    return task_id


def test_discover_wildclaw_tasks_selects_requested_categories(tmp_path: Path) -> None:
    first = _write_task(tmp_path, 1, 1)
    second = _write_task(tmp_path, 2, 1)
    _write_task(tmp_path, 3, 1)

    tasks = discover_wildclaw_tasks(tmp_path, (2, 1))

    assert [(task.category, task.task_id) for task in tasks] == [(1, first), (2, second)]


def test_discover_wildclaw_tasks_rejects_a_missing_category(tmp_path: Path) -> None:
    _write_task(tmp_path, 1, 1)

    with pytest.raises(ValueError, match="category 02"):
        discover_wildclaw_tasks(tmp_path, (1, 2))


def test_batch_delegates_each_task_to_single_cli_and_writes_summary(tmp_path: Path, monkeypatch) -> None:
    wildclaw_root = tmp_path / "WildClawBench"
    task_ids = (_write_task(wildclaw_root, 1, 1), _write_task(wildclaw_root, 1, 2))
    config = tmp_path / "config.yaml"
    config.write_text(
        "model_endpoints:\n"
        "  research-model:\n"
        "    provider: openai-completions\n"
        "    provider_model_id: provider/model-v1\n"
        "    base_url: https://example.test/v1\n"
        "    api_key: test-key\n",
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        cell_root = Path(command[command.index("--run-root") + 1])
        task_id = command[command.index("--tasks.filter") + 1]
        cell_root.mkdir(parents=True, exist_ok=True)
        (cell_root / "cell_summary.json").write_text(
            json.dumps(
                {
                    "status": "SUCCEEDED",
                    "mean_score": 1.0,
                    "stop_reason": "anchor_terminal",
                    "winner_portfolio_root": str(cell_root / "tasks" / task_id / "final_portfolio"),
                    "resume": {"skipped": 1, "rerun": 0, "run": 0, "failed": 0},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_root = tmp_path / "run"
    summary = run_wildclaw_batch(
        WildClawBatchSettings(
            project_root=ROOT,
            wildclaw_root=wildclaw_root,
            run_root=run_root,
            config_path=config,
            model="research-model",
            categories=(1,),
        )
    )

    assert summary["status"] == "completed"
    assert [row["task_id"] for row in summary["tasks"]] == list(task_ids)
    assert json.loads((run_root / "batch_summary.json").read_text()) == summary
    assert len(commands) == 2
    for command, task_id in zip(commands, task_ids):
        assert command[command.index("--model") + 1] == "research-model"
        assert command[command.index("--tasks.mode") + 1] == "single"
        assert command[command.index("--tasks.filter") + 1] == task_id
        assert Path(command[command.index("--run-root") + 1]) == run_root / "cells" / task_id


def test_run_parser_accepts_any_model_label_defined_by_config() -> None:
    args = build_parser().parse_args(
        [
            "run",
            "skilllift",
            "--benchmark",
            "wildclawbench",
            "--model",
            "research-model",
            "--config",
            "config.yaml",
            "--tasks.mode",
            "single",
            "--tasks.filter",
            "01-01",
        ]
    )

    assert args.model == "research-model"
