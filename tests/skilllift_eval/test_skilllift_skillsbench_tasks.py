from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import scripts.skilllift_skillsbench_tasks as skillsbench_script
import skilllift_eval.runners.skilllift_skillsbench_tasks as skillsbench_runner
from scripts.skilllift_skillsbench_tasks import _settings, parse_args
from skilllift_eval.runners.skilllift_skillsbench_tasks import (
    _benchflow_runtime_hash,
    _git_worktree_hash,
    build_shard_manifest,
    load_task_universe,
    merge_shards,
    select_domain_task_ids,
    select_task_ids,
    task_universe_hash,
)
from skilllift_eval.skillsbench_cli import load_and_validate_split


ROOT = Path(__file__).resolve().parents[2]
SPLIT_PATH = ROOT / "skilllift_eval" / "benchmarks" / "skillsbench_split_v1.json"
TASKS_ROOT = ROOT / "skillsbench" / "tasks"


def test_skillsbench_launcher_dry_runs_one_model_domain_cell() -> None:
    completed = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "run_skillsbench.sh"),
            "--model",
            "glm",
            "--domain",
            "media-content-production",
            "--dry-run",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "CELL model=glm domain=media-content-production" in completed.stdout
    assert "runs/skillsbench/glm/media-content-production" in completed.stdout


def test_skillsbench_launcher_dry_runs_all_24_cells() -> None:
    completed = subprocess.run(
        ["bash", str(ROOT / "scripts" / "run_skillsbench.sh"), "--all", "--dry-run"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    cells = [line for line in completed.stdout.splitlines() if line.startswith("CELL ")]
    assert len(cells) == 24
    assert len(set(cells)) == 24
    assert {f"model={model}" for model in ("gpt", "glm", "deepseek")} <= {
        field for line in cells for field in line.split()
    }


def test_main_loads_root_dotenv_before_resolving_model_endpoint(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        'MODEL_A_BASE_URL="https://dotenv.example/v1"\n'
        "MODEL_A_API_KEY='dotenv-key'\n"
        "PREEXISTING=from-dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(skillsbench_script, "ROOT", tmp_path)
    monkeypatch.delenv("MODEL_A_BASE_URL", raising=False)
    monkeypatch.delenv("MODEL_A_API_KEY", raising=False)
    monkeypatch.setenv("PREEXISTING", "from-shell")
    observed: dict[str, str | None] = {}

    class StopAfterSettings(Exception):
        pass

    def capture_settings(*args, **kwargs):
        observed.update(
            base_url=os.environ.get("MODEL_A_BASE_URL"),
            api_key=os.environ.get("MODEL_A_API_KEY"),
            preexisting=os.environ.get("PREEXISTING"),
        )
        raise StopAfterSettings

    monkeypatch.setattr(skillsbench_script, "_settings", capture_settings)

    with pytest.raises(StopAfterSettings):
        skillsbench_script.main(["--config", str(config_path), "--phase", "check"])

    assert observed == {
        "base_url": "https://dotenv.example/v1",
        "api_key": "dotenv-key",
        "preexisting": "from-shell",
    }


def test_task_ranges_are_one_based_disjoint_and_cover_frozen_87() -> None:
    universe = load_task_universe(SPLIT_PATH, TASKS_ROOT)

    first = select_task_ids(universe, task_range="1-47")
    second = select_task_ids(universe, task_range="48-87")

    assert len(universe) == 87
    assert len(first) == 47
    assert len(second) == 40
    assert set(first).isdisjoint(second)
    assert tuple(sorted((*first, *second))) == universe


def test_explicit_tasks_are_canonical_and_invalid_selection_fails() -> None:
    universe = ("a", "b", "c")

    assert select_task_ids(universe, tasks=("c", "a")) == ("a", "c")
    with pytest.raises(ValueError, match="unknown"):
        select_task_ids(universe, tasks=("missing",))
    with pytest.raises(ValueError, match="cannot combine"):
        select_task_ids(universe, task_range="1-2", tasks=("a",))
    with pytest.raises(ValueError, match="range"):
        select_task_ids(universe, task_range="0-2")


def test_domain_selection_uses_frozen_split_and_composes_with_task_range() -> None:
    split = load_and_validate_split(SPLIT_PATH, TASKS_ROOT)

    selected = select_domain_task_ids(split, ("software-engineering", "cybersecurity"))
    shard = select_task_ids(selected, task_range="1-12")

    assert len(selected) == 23
    assert len(shard) == 12
    assert set(shard) <= set(selected)
    with pytest.raises(ValueError, match="unknown SkillsBench domains"):
        select_domain_task_ids(split, ("missing-domain",))


def test_cli_accepts_model_and_domain_combination() -> None:
    args = parse_args(
        [
            "--model",
            "research-model",
            "--domain",
            "software-engineering",
            "cybersecurity",
            "--task-range",
            "1-12",
        ]
    )

    assert args.model == "research-model"
    assert args.domain == ["software-engineering", "cybersecurity"]
    assert args.task_range == "1-12"


def test_model_label_selects_independent_gateway_key_and_framework_model(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "configs" / "skillsbench.yaml"
    config_path.parent.mkdir()
    config = {
        "project_root": "..",
        "skillsbench": {
            "split_path": "split.json",
            "tasks_root": "tasks",
            "python_executable": "venv/bin/python",
            "prebuilt_image_template": "prewarm/{task_id}:prewarm",
            "default_model": "model-a",
            "agent": "openhands",
            "model_endpoints": {
                "model-a": {
                    "provider": "vllm",
                    "model_env": "MODEL_A_MODEL",
                    "base_url_env": "MODEL_A_BASE_URL",
                    "api_key_env": "MODEL_A_API_KEY",
                },
                "model-b": {
                    "provider": "openai",
                    "model_env": "MODEL_B_MODEL",
                    "framework_model_env": "MODEL_B_FRAMEWORK_MODEL",
                    "base_url": "https://model-b.example/v1",
                    "api_key_env": "MODEL_B_API_KEY",
                    "agent": "openhands-sse",
                    "stream": True,
                },
            },
        },
    }
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setenv("MODEL_A_BASE_URL", "https://model-a.example/v1")
    monkeypatch.setenv("MODEL_A_MODEL", "provider-model-a")
    monkeypatch.setenv("MODEL_A_API_KEY", "key-a")
    monkeypatch.setenv("MODEL_B_MODEL", "provider-model-b")
    monkeypatch.setenv("MODEL_B_FRAMEWORK_MODEL", "provider-model-b-framework")
    monkeypatch.setenv("MODEL_B_API_KEY", "key-b")

    settings = _settings(
        config_path,
        config,
        tmp_path / "run",
        model_override="model-b",
        require_credentials=True,
    )

    assert settings.model == "openai/provider-model-b"
    assert settings.framework_model == "provider-model-b-framework"
    assert settings.base_url == "https://model-b.example/v1"
    assert settings.api_key == "key-b"
    assert settings.agent == "openhands-sse"
    assert settings.use_stream is True
    assert settings.prebuilt_image_template == "prewarm/{task_id}:prewarm"

    default_settings = _settings(
        config_path,
        config,
        tmp_path / "default-run",
        model_override="model-a",
        require_credentials=True,
    )
    assert default_settings.agent == "openhands"

    with pytest.raises(ValueError, match="unknown SkillsBench model endpoint"):
        _settings(
            config_path,
            config,
            tmp_path / "unknown",
            model_override="missing-model",
            require_credentials=False,
        )


def test_run_manifest_omits_config_hash_and_accepts_legacy_config_hash(tmp_path: Path) -> None:
    manifest = build_shard_manifest(("a",), ("a",))

    assert "config_hash" not in manifest
    assert manifest["runner_version"] == "skillsbench_skilllift_runner_v1"

    manifest_path = tmp_path / "run_manifest.json"
    legacy_manifest = {**manifest, "config_hash": "old-config"}
    manifest_path.write_text(json.dumps(legacy_manifest), encoding="utf-8")

    skillsbench_runner._write_once(manifest_path, manifest)


def test_runner_enables_streaming_for_rubricator_and_skill_generator(tmp_path: Path) -> None:
    settings = skillsbench_runner.SkillLiftSkillsBenchTasksSettings(
        project_root=tmp_path,
        run_root=tmp_path / "run",
        tasks_root=tmp_path / "tasks",
        split_path=tmp_path / "split.json",
        python_executable=tmp_path / "venv" / "bin" / "python",
        model="vllm/glm",
        sandbox="docker",
        base_url="https://glm.example/v1",
        api_key="key",
        patch_source=tmp_path / "oh_skill_patch.py",
        agent="openhands-sse",
        use_stream=True,
    )

    runner = skillsbench_runner.SkillLiftSkillsBenchTasksRunner(settings)

    assert runner.adapter.settings.agent == "openhands-sse"
    assert runner.planner.client.use_stream is True
    assert runner.planner.core_evidence_on_overflow is True
    assert runner.generator.client.use_stream is True
    assert runner.generator.__class__.__name__ == "BoundedEditsSkillGenerator"


def test_settings_preserves_virtualenv_python_symlink(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "configs" / "skillsbench.yaml"
    config_path.parent.mkdir()
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(Path(sys.executable))
    config = {
        "project_root": "..",
        "skillsbench": {
            "split_path": "split.json",
            "tasks_root": "tasks",
            "python_executable": ".venv/bin/python",
            "default_model": "model-a",
            "model_endpoints": {
                "model-a": {
                    "provider": "vllm",
                    "model_env": "MODEL_A_MODEL",
                    "base_url_env": "MODEL_A_BASE_URL",
                    "api_key_env": "MODEL_A_API_KEY",
                }
            },
        },
    }
    monkeypatch.setenv("MODEL_A_MODEL", "provider-model-a")
    monkeypatch.setenv("MODEL_A_BASE_URL", "https://model-a.example/v1")
    monkeypatch.setenv("MODEL_A_API_KEY", "key-a")

    settings = _settings(config_path, config, None, require_credentials=True)

    assert settings.python_executable == venv_python
    assert settings.python_executable.is_symlink()


def test_shards_share_universe_hash_but_have_distinct_assignment_hashes() -> None:
    universe = ("a", "b", "c")
    left = build_shard_manifest(universe, ("a", "b"))
    right = build_shard_manifest(universe, ("c",))

    assert left["universe_hash"] == right["universe_hash"]
    assert left["assignment_hash"] != right["assignment_hash"]


def test_universe_hash_binds_public_task_and_complete_curated_portfolio(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    task_root = tasks_root / "a"
    skill_root = task_root / "environment" / "skills" / "alpha"
    skill_root.mkdir(parents=True)
    (task_root / "task.md").write_text("Do A.\n", encoding="utf-8")
    (skill_root / "SKILL.md").write_text("# Alpha\n", encoding="utf-8")

    initial = task_universe_hash(tasks_root, ("a",))
    manifest = build_shard_manifest(("a",), ("a",), universe_hash=initial)
    (skill_root / "reference.md").write_text("changed\n", encoding="utf-8")

    assert manifest["universe_hash"] == initial
    assert task_universe_hash(tasks_root, ("a",)) != initial


def test_git_worktree_hash_binds_head_tracked_diff_and_untracked_bytes(tmp_path: Path) -> None:
    repo = tmp_path / "benchmark"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    source = repo / "task.md"
    source.write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "task.md"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "init"],
        cwd=repo,
        check=True,
    )
    clean = _git_worktree_hash(repo)
    source.write_text("tracked change\n", encoding="utf-8")
    dirty = _git_worktree_hash(repo)
    (repo / "untracked.txt").write_text("extra\n", encoding="utf-8")

    assert dirty != clean
    assert _git_worktree_hash(repo) != dirty


def test_benchflow_runtime_hash_binds_source_and_version_metadata(tmp_path: Path) -> None:
    site_packages = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages"
    runtime = site_packages / "benchflow"
    metadata = site_packages / "benchflow-0.6.3.dist-info" / "METADATA"
    runtime.mkdir(parents=True)
    metadata.parent.mkdir()
    (runtime / "runtime.py").write_text("VERSION = 1\n", encoding="utf-8")
    metadata.write_text("Version: 0.6.3\n", encoding="utf-8")
    initial = _benchflow_runtime_hash(tmp_path)
    (runtime / "runtime.py").write_text("VERSION = 2\n", encoding="utf-8")
    source_changed = _benchflow_runtime_hash(tmp_path)
    metadata.write_text("Version: 0.6.4\n", encoding="utf-8")

    assert source_changed != initial
    assert _benchflow_runtime_hash(tmp_path) != source_changed


def test_merge_requires_matching_config_and_complete_disjoint_coverage(tmp_path: Path) -> None:
    universe = ("a", "b", "c")
    shard_roots = []
    for index, assignment in enumerate((("a", "b"), ("c",))):
        root = tmp_path / f"shard-{index}"
        root.mkdir()
        manifest = build_shard_manifest(universe, assignment)
        manifest["config_hash"] = f"legacy-{index}"
        (root / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        tasks = [
            {
                "task_id": task_id,
                "cohort": "adapted_budget",
                "adaptation_reward": 0.5,
                "final_score": 0.75,
                "logical_cells": 3,
                "physical_attempts": 3,
            }
            for task_id in assignment
        ]
        (root / "summary.json").write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
        shard_roots.append(root)

    merged = merge_shards(shard_roots, tmp_path / "merged")

    assert merged["status"] == "complete"
    assert merged["covered_tasks"] == 3
    assert merged["logical_cells"] == 9
    assert merged["adapted_budget_mean"] == 0.75
    assert (tmp_path / "merged" / "aggregate.json").is_file()

    duplicate = tmp_path / "duplicate"
    duplicate.mkdir()
    (duplicate / "run_manifest.json").write_text(
        json.dumps(build_shard_manifest(universe, ("a",))),
        encoding="utf-8",
    )
    (duplicate / "summary.json").write_text(json.dumps({"tasks": [{"task_id": "a"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        merge_shards([*shard_roots, duplicate], tmp_path / "bad-merge")
