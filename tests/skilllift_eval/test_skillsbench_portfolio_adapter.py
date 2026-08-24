from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from pathlib import Path

from skilllift_eval.runners.skillsbench_portfolio_adapter import (
    SkillsBenchPortfolioAdapter,
    SkillsBenchPortfolioSettings,
)

from skilllift.coordinator import TrialSpec


def _task(tasks_root: Path) -> None:
    task_root = tasks_root / "task-a"
    skill_root = task_root / "environment" / "skills" / "alpha"
    (skill_root / "scripts").mkdir(parents=True)
    (task_root / "task.md").write_text("# Public Task\n\nDo it.\n", encoding="utf-8")
    (skill_root / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha.\n---\n# Alpha\n",
        encoding="utf-8",
    )
    (skill_root / "scripts" / "run.py").write_text("print('AUX')\n", encoding="utf-8")


class FakeSubprocessAdapter:
    def __init__(
        self, *, corrupt_receipt: bool = False, agent_timeout: bool = False
    ) -> None:
        self.corrupt_receipt = corrupt_receipt
        self.agent_timeout = agent_timeout
        self.calls = []

    def execute(self, command, **kwargs):
        self.calls.append((command, kwargs))
        command.jobs_dir.mkdir(parents=True)
        result_path = command.jobs_dir / "rollout" / "result.json"
        result_path.parent.mkdir()
        payload = {
            "task_name": "task-a",
            "skill_mode": "with-skill",
            "effective_skills_dir": str(command.skills_dir),
            "rewards": {"reward": 0.75},
            "n_tool_calls": 1,
            "n_skill_invocations": 1,
            "usage_tracking": {"status": "enabled", "usage_source": "provider_response"},
            "agent_result": {"total_tokens": 10},
        }
        if self.agent_timeout:
            payload.update(
                error="Agent prompt exceeded wall-clock budget 3600s",
                error_category="timeout",
                partial_trajectory=True,
                agent_timeout_info={
                    "reason": "wall_clock_timeout",
                    "timeout_sec": 3600.0,
                    "n_tool_calls": 116,
                    "pending_tool_call_ids": ["call-pending"],
                },
            )
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        portfolio_root = command.skills_dir
        receipt_root = result_path.parent / "agent" / "skilllift-skills-receipts"
        receipt_root.mkdir(parents=True)
        entries = []
        for path in sorted(item for item in portfolio_root.rglob("*") if item.is_file()):
            relative = path.relative_to(portfolio_root)
            if len(relative.parts) < 2 or relative.parts[0].startswith("."):
                continue
            data = path.read_bytes()
            entries.append(
                {
                    "path": relative.as_posix(),
                    "mode": stat.S_IMODE(path.stat().st_mode),
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        tree_hash = _manifest_tree_hash(portfolio_root, entries)
        if self.corrupt_receipt:
            tree_hash = "0" * 64
        (receipt_root / "deployed_tree_receipt.json").write_text(
            json.dumps({"schema_version": 1, "tree_hash": tree_hash, "files": entries}),
            encoding="utf-8",
        )
        skills = []
        for skill_md in sorted(portfolio_root.glob("*/SKILL.md")):
            content = skill_md.read_text(encoding="utf-8")
            skills.append(
                {
                    "name": skill_md.parent.name,
                    "skill_md_sha256": hashlib.sha256(content.encode()).hexdigest(),
                    "source_chars": len(content),
                    "injected_chars": len(content) + 10,
                }
            )
        (receipt_root / "loaded_skill_receipt.json").write_text(
            json.dumps({"schema_version": 1, "skills": skills}),
            encoding="utf-8",
        )
        cell = {
            "task_id": "task-a",
            "skill_id": kwargs["skill_id"],
            "oracle_result": {"rewards": {"reward": 0.75}, "n_tool_calls": 1, "n_skill_invocations": 1},
        }
        return cell, payload, result_path


def _manifest_tree_hash(root: Path, entries) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        path = root / entry["path"]
        data = path.read_bytes()
        mode = stat.S_IMODE(path.stat().st_mode)
        digest.update(str(entry["path"]).encode())
        digest.update(b"\0")
        digest.update(f"{mode:o}".encode())
        digest.update(b"\0")
        digest.update(str(len(data)).encode())
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def _adapter(
    tmp_path: Path,
    subprocess_adapter: FakeSubprocessAdapter,
    *,
    prebuilt_image_template: str | None = None,
) -> SkillsBenchPortfolioAdapter:
    tasks_root = tmp_path / "tasks"
    _task(tasks_root)
    patch = tmp_path / "oh_skill_patch.py"
    patch.write_text("# patch\n", encoding="utf-8")
    return SkillsBenchPortfolioAdapter(
        SkillsBenchPortfolioSettings(
            project_root=tmp_path,
            tasks_root=tasks_root,
            run_root=tmp_path / "run",
            python_executable=Path("/bin/true"),
            model="vllm/test",
            sandbox="docker",
            base_url="https://example.test/v1",
            api_key="secret",
            patch_source=patch,
            prebuilt_image_template=prebuilt_image_template,
        ),
        subprocess_adapter=subprocess_adapter,
    )


def test_seed_portfolio_preserves_complete_curated_tree(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, FakeSubprocessAdapter())

    seed = adapter.seed_portfolio("task-a")

    assert seed.curated_skill_names == frozenset({"alpha"})
    assert (seed.root / "alpha" / "scripts" / "run.py").read_text(encoding="utf-8") == "print('AUX')\n"
    assert adapter.public_task("task-a").evidence_ref == "task.md"


def test_evaluate_requires_matching_tree_and_loaded_receipts(tmp_path: Path) -> None:
    subprocess_adapter = FakeSubprocessAdapter()
    adapter = _adapter(tmp_path, subprocess_adapter)
    seed = adapter.seed_portfolio("task-a")
    trial = TrialSpec("trial-1", "anchor", 0)

    evaluation = adapter.evaluate("task-a", seed, trial)

    assert evaluation.is_valid
    assert evaluation.reward == 0.75
    assert evaluation.result_hash
    assert Path(evaluation.artifact_ref).name == "trial_result.json"
    command, kwargs = subprocess_adapter.calls[0]
    assert kwargs["exact_result"] is True
    assert (command.skills_dir / "alpha" / "scripts" / "run.py").is_file()
    assert (command.skills_dir / "oh_skill_patch.py").is_file()


def test_evaluate_does_not_copy_runtime_warnings_into_portfolio_state(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, FakeSubprocessAdapter(agent_timeout=True))
    seed = adapter.seed_portfolio("task-a")

    evaluation = adapter.evaluate(
        "task-a", seed, TrialSpec("trial-timeout", "candidate", 0)
    )

    assert evaluation.is_valid
    assert evaluation.reward == 0.75
    assert "runtime_warnings" not in json.loads(
        (tmp_path / "run" / "adapter_artifacts" / "task-a" / "trial-timeout" / "trial_result.json").read_text()
    )
    recovered = adapter.recover(
        "task-a", seed, TrialSpec("trial-timeout", "candidate", 0)
    )
    assert recovered is not None
    assert recovered.to_dict() == evaluation.to_dict()


def test_evaluate_uses_per_task_prebuilt_image_manifest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )
    subprocess_adapter = FakeSubprocessAdapter()
    adapter = _adapter(
        tmp_path,
        subprocess_adapter,
        prebuilt_image_template="prewarm/{task_id}:prewarm",
    )
    seed = adapter.seed_portfolio("task-a")

    evaluation = adapter.evaluate("task-a", seed, TrialSpec("trial-1", "anchor", 0))

    assert evaluation.is_valid
    command, _ = subprocess_adapter.calls[0]
    assert command.environment_manifest == command.jobs_dir.parent / "environment.toml"
    assert command.environment_manifest.read_text(encoding="utf-8") == (
        '[environment]\nname = "task-a-prebuilt"\nimage = "prewarm/task-a:prewarm"\n'
    )


def test_evaluate_builds_missing_prebuilt_image_before_running(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        returncode = 1 if command[:3] == ["docker", "image", "inspect"] else 0
        return subprocess.CompletedProcess(command, returncode, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    subprocess_adapter = FakeSubprocessAdapter()
    adapter = _adapter(
        tmp_path,
        subprocess_adapter,
        prebuilt_image_template="prewarm/{task_id}:prewarm",
    )
    seed = adapter.seed_portfolio("task-a")

    evaluation = adapter.evaluate("task-a", seed, TrialSpec("trial-1", "anchor", 0))

    assert evaluation.is_valid
    assert calls == [
        ["docker", "image", "inspect", "prewarm/task-a:prewarm"],
        [
            "docker",
            "build",
            "--tag",
            "prewarm/task-a:prewarm",
            str((tmp_path / "tasks" / "task-a" / "environment").resolve()),
        ],
    ]


def test_evaluate_reports_missing_prebuilt_build_failure(tmp_path: Path, monkeypatch) -> None:
    def fake_run(command, **kwargs):
        returncode = 1
        return subprocess.CompletedProcess(command, returncode, stdout="", stderr="network failed")

    monkeypatch.setattr(subprocess, "run", fake_run)
    subprocess_adapter = FakeSubprocessAdapter()
    adapter = _adapter(
        tmp_path,
        subprocess_adapter,
        prebuilt_image_template="prewarm/{task_id}:prewarm",
    )
    seed = adapter.seed_portfolio("task-a")

    evaluation = adapter.evaluate("task-a", seed, TrialSpec("trial-1", "anchor", 0))

    assert not evaluation.is_valid
    assert "docker build failed for prewarm/task-a:prewarm" in evaluation.infrastructure_error
    assert subprocess_adapter.calls == []


def test_evaluate_treats_receipt_mismatch_as_infrastructure_failure(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, FakeSubprocessAdapter(corrupt_receipt=True))
    seed = adapter.seed_portfolio("task-a")

    evaluation = adapter.evaluate("task-a", seed, TrialSpec("trial-1", "anchor", 0))

    assert not evaluation.is_valid
    assert "receipt" in evaluation.infrastructure_error


def test_recover_uses_marker_path_and_hash_not_newest_result(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, FakeSubprocessAdapter())
    seed = adapter.seed_portfolio("task-a")
    trial = TrialSpec("trial-1", "anchor", 0)
    original = adapter.evaluate("task-a", seed, trial)
    marker = Path(original.artifact_ref)
    stale = marker.parent / "jobs" / "newer" / "result.json"
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps({"task_name": "task-a", "rewards": {"reward": 1.0}}), encoding="utf-8")

    recovered = adapter.recover("task-a", seed, trial)

    assert recovered is not None
    assert recovered.reward == 0.75
    assert recovered.result_hash == original.result_hash
