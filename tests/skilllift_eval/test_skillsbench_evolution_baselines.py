from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from skilllift.portfolio import PortfolioRef
from skilllift.coordinator import CandidateEvaluation, TrialSpec
from skilllift.schemas import SkillLiftConfig, EvoSkill, SkillKey, TaskSpec
from scripts.skilllift_skillsbench_tasks import _write_evolution_manifest, parse_args
from skilllift_eval.runners.coevoskills_skillsbench import (
    CoEvoSkillsSkillsBenchRunner,
    _ArtifactBackend,
    _OracleBackend,
)
from skilllift_eval.runners.coevoskills_skillsbench import (
    _algorithm_config as coevoskills_config,
)
from skilllift_eval.runners.skillsbench_evolution_adapter import (
    SkillsBenchEvolutionAdapter,
    SkillsBenchEvolutionEvaluation,
    SkillsBenchEvolutionSettings,
    build_evolution_adapter,
)


ROOT = Path(__file__).resolve().parents[2]


def _settings(tmp_path: Path) -> SkillsBenchEvolutionSettings:
    return SkillsBenchEvolutionSettings(
        project_root=tmp_path,
        run_root=tmp_path / "run",
        tasks_root=tmp_path / "tasks",
        split_path=tmp_path / "split.json",
        python_executable=tmp_path / "python",
        model="vllm/test-model",
        sandbox="docker",
        base_url="https://example.invalid",
        api_key="test",
        patch_source=tmp_path / "patch.py",
        agent="openhands",
        framework_model="test-model",
        prebuilt_image_template="prewarm/{task_id}:prewarm",
    )


def test_evolution_adapter_uses_shared_prebuilt_image_template(tmp_path: Path) -> None:
    adapter = build_evolution_adapter(_settings(tmp_path), tmp_path / "task-run")

    assert adapter.adapter.settings.tasks_root == tmp_path / "tasks"
    assert (
        adapter.adapter.settings.prebuilt_image_template
        == "prewarm/{task_id}:prewarm"
    )


class _FakePortfolioAdapter:
    def __init__(self, root: Path) -> None:
        self.settings = SimpleNamespace(run_root=root)
        self.portfolios: list[PortfolioRef] = []
        skill_root = root / "seed" / "curated"
        skill_root.mkdir(parents=True)
        (skill_root / "SKILL.md").write_text(
            "---\nname: curated\ndescription: Curated skill.\ntriggers: [task]\n---\n\n# Curated\n",
            encoding="utf-8",
        )

    def seed_portfolio(self, task_id: str) -> PortfolioRef:
        return PortfolioRef.from_directory(
            task_id=task_id,
            root=self.settings.run_root / "seed",
            config_hash="config",
        )

    def recover(self, task_id: str, portfolio: PortfolioRef, trial: TrialSpec):
        del task_id, portfolio, trial
        return None

    def evaluate(
        self,
        task_id: str,
        portfolio: PortfolioRef,
        trial: TrialSpec,
    ) -> CandidateEvaluation:
        self.portfolios.append(portfolio)
        result_root = self.settings.run_root / "results" / trial.trial_id
        artifacts = result_root / "artifacts"
        artifacts.mkdir(parents=True)
        (artifacts / "answer.txt").write_text("answer\n", encoding="utf-8")
        result_path = result_root / "result.json"
        result_path.write_text(
            json.dumps(
                {
                    "task_name": task_id,
                    "rewards": {"reward": 0.75},
                    "error": "private failure detail",
                    "n_tool_calls": 3,
                }
            ),
            encoding="utf-8",
        )
        marker = result_root / "trial_result.json"
        marker.write_text(
            json.dumps({"result_path": str(result_path)}), encoding="utf-8"
        )
        return CandidateEvaluation.valid(
            0.75,
            result_hash="result-hash",
            artifact_ref=str(marker),
        )


def test_evolution_adapter_adds_overlay_and_hides_oracle_fields(tmp_path: Path) -> None:
    portfolio_adapter = _FakePortfolioAdapter(tmp_path)
    adapter = SkillsBenchEvolutionAdapter(portfolio_adapter)

    result = adapter.evaluate(
        "task-a",
        overlay_name="coevoskills-generated",
        files={"SKILL.md": "# Generated\n", "scripts/helper.py": "print('ok')\n"},
        trial=TrialSpec("trial-1", "oracle", 1),
    )

    assert result.reward == 0.75
    portfolio = portfolio_adapter.portfolios[0]
    assert portfolio.curated_skill_names == frozenset({"curated"})
    assert portfolio.generated_skill_names == frozenset({"coevoskills-generated"})
    assert "name: coevoskills-generated" in (
        portfolio.root / "coevoskills-generated" / "SKILL.md"
    ).read_text()
    assert (
        result.public_artifact_root / "outputs" / "answer.txt"
    ).read_text() == "answer\n"
    public = json.loads((result.public_artifact_root / "run_summary.json").read_text())
    assert public == {"task_name": "task-a", "n_tool_calls": 3}


class _FakeEvolutionAdapter:
    def __init__(self, tmp_path: Path) -> None:
        self.adapter = SimpleNamespace(settings=SimpleNamespace(run_root=tmp_path))
        self.calls: list[TrialSpec] = []
        self.root = tmp_path / "public"
        self.root.mkdir()
        (self.root / "artifact.txt").write_text("ok\n", encoding="utf-8")
        self.result_path = tmp_path / "result.json"
        self.result_path.write_text("{}\n", encoding="utf-8")

    def evaluate(self, task_id: str, *, overlay_name: str, files, trial: TrialSpec):
        del task_id, overlay_name, files
        self.calls.append(trial)
        return SkillsBenchEvolutionEvaluation(
            evaluation=CandidateEvaluation.valid(0.8, result_hash="hash"),
            result_path=self.result_path,
            result_payload={},
            public_artifact_root=self.root,
        )


def test_coevoskills_backends_map_artifacts_and_reward(tmp_path: Path) -> None:
    adapter = _FakeEvolutionAdapter(tmp_path)
    skill = SimpleNamespace(token="s000", files={"SKILL.md": "# Skill\n"})
    task = SimpleNamespace(task_id="task-a")

    snapshot = _ArtifactBackend(adapter, "task-a").rollout(task, skill)
    oracle = _OracleBackend(adapter, "task-a", threshold=0.9).evaluate(task, skill)

    assert snapshot.files[0].path == "artifact.txt"
    assert oracle.score == 0.8
    assert oracle.passed is False
    assert [trial.phase for trial in adapter.calls] == ["surrogate", "oracle"]


def test_skillsbench_coevoskills_config_requires_isolated_surrogate_runtime() -> None:
    config = coevoskills_config({"surrogate_docker_image": "wildclawbench-ubuntu:v1.3"})

    assert config.surrogate_docker_image == "wildclawbench-ubuntu:v1.3"


def test_coevoskills_runner_records_failure_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CoEvoSkillsSkillsBenchRunner(_settings(tmp_path))
    calls: list[str] = []

    def run_task(task_id: str) -> dict[str, object]:
        calls.append(task_id)
        if task_id == "task-a":
            raise RuntimeError("environment unavailable")
        return {
            "task_id": task_id,
            "status": "completed",
            "best_oracle_score": 0.75,
        }

    monkeypatch.setattr(runner, "run_task", run_task)

    summary = runner.run(("task-a", "task-b"))

    assert calls == ["task-a", "task-b"]
    assert summary["status"] == "completed_with_failures"
    assert summary["succeeded_task_count"] == 1
    assert summary["failed_task_count"] == 1
    assert summary["mean_best_oracle_score"] == 0.75
    failure = json.loads(
        (tmp_path / "run" / "tasks" / "task-a" / "summary.json").read_text()
    )
    assert failure == {
        "error": "environment unavailable",
        "error_type": "RuntimeError",
        "status": "failed",
        "task_id": "task-a",
    }


def test_task_cli_routes_baselines_without_changing_default() -> None:
    assert parse_args([]).baseline == "skilllift"
    assert parse_args(["--baseline", "coevoskills"]).baseline == "coevoskills"
    assert parse_args(["--baseline", "coevoskills"]).baseline == "coevoskills"


def test_baseline_config_uses_shared_prewarm_template() -> None:
    config = yaml.safe_load(
        (ROOT / "configs" / "skillsbench" / "tasks.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert (
        config["skillsbench"]["prebuilt_image_template"]
        == "prewarm/{task_id}:prewarm"
    )
    assert config["coevoskills"]["surrogate_docker_image"] == (
        "wildclawbench-ubuntu:v1.3"
    )


def test_launcher_routes_new_baseline_without_changing_default_path() -> None:
    completed = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "run_skillsbench.sh"),
            "--model",
            "glm",
            "--domain",
            "media-content-production",
            "--baseline",
            "coevoskills",
            "--dry-run",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "baseline=coevoskills" in completed.stdout
    assert "runs/skillsbench/coevoskills/glm/media-content-production" in completed.stdout
    assert "--baseline coevoskills" in completed.stdout


def test_evolution_manifest_can_change_only_before_real_progress(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    _write_evolution_manifest(run_root, {"algorithm_params_hash": "old"})

    _write_evolution_manifest(run_root, {"algorithm_params_hash": "new"})

    assert json.loads((run_root / "run_manifest.json").read_text()) == {
        "algorithm_params_hash": "new"
    }
    (run_root / "tasks" / "task-a").mkdir(parents=True)
    (run_root / "tasks" / "task-a" / "checkpoint.json").write_text("{}\n")
    with pytest.raises(ValueError, match="different content"):
        _write_evolution_manifest(run_root, {"algorithm_params_hash": "newer"})
