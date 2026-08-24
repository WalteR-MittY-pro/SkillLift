from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from skilllift_eval.model_endpoints import ModelEndpointProfile, endpoint_config_hash
from skilllift_eval.runners.coevoskills_wildclaw import (
    SOURCE,
    CoEvoSkillsWildClawRunSettings,
    CoEvoSkillsWildClawRunner,
    _framework_model_config,
    _snapshot,
)
from skilllift_eval.runners.wildclaw import ArtifactRolloutRecord
from skilllift_eval.schemas import TaskRunRecord, stable_hash


ROOT = Path(__file__).resolve().parents[2]


def test_framework_model_config_preserves_endpoint_streaming() -> None:
    endpoint = ModelEndpointProfile(
        model_endpoint_id="glm-5.1",
        model_label="glm-5.1",
        provider="openai-completions",
        provider_model_id="glm-5.1",
        base_url="https://example.test/v4",
        api_key="token",
        stream=True,
    )

    config = _framework_model_config(endpoint)

    assert config["providers"]["coevoskills"]["stream"] is True


class FakeGenerator:
    def initialize(self, task):
        from coevoskills.schemas import SkillVersion

        assert "SECRET_GRADING_MARKER" not in task.instruction
        assert task.instruction == "Produce result.txt."
        return SkillVersion(
            0,
            "demo",
            {
                "SKILL.md": "# Demo\n\nUse the helper.\n",
                "scripts/helper.py": "def run():\n    return 'ok'\n",
            },
            "scripts/helper.py",
        )

    def refine(self, task, skill, diagnostic):
        raise AssertionError("surrogate passes in this test")

    def note_oracle_failure(self):
        raise AssertionError("Oracle passes in this test")

    def context_ratio(self):
        return 0.1


class FakeVerifier:
    def initialize_suite(self, task, artifacts):
        from coevoskills.schemas import TestAssertion, TestSuiteVersion

        return TestSuiteVersion(0, "def run(artifact_root):\n    return []\n", [TestAssertion("a1", "ok")])

    def escalate_suite(self, task, artifacts, suite):
        raise AssertionError("Oracle passes in this test")

    def diagnose(self, task, artifacts, suite, result):
        raise AssertionError("surrogate passes in this test")


class FakeRuntime:
    def run(self, suite, artifacts):
        from coevoskills.schemas import AssertionResult, SurrogateResult

        return SurrogateResult(suite.version, [AssertionResult("a1", True)])


class FakeArtifactRunner:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.calls = 0

    def run(self, settings, bundle):
        self.calls += 1
        assert settings.rate_limit_retries == settings.model_endpoint.max_retries
        assert "scripts/helper.py" in bundle.skills[0]["files"]
        root = self.tmp_path / f"artifact-{self.calls}"
        root.mkdir()
        (root / "result.txt").write_text("ok\n", encoding="utf-8")
        return ArtifactRolloutRecord(
            run_id=f"artifact-{self.calls}",
            task_id="01_Productivity_Flow_task_1_demo",
            status="succeeded",
            output_dir=str(root.parent),
            artifact_root=str(root),
            loader_manifest_path=str(root / "loader.json"),
            raw_output_path=str(root / "raw.json"),
            usage={},
        )


class FakeOracleRunner:
    def __init__(
        self,
        tmp_path: Path,
        *,
        fail_once_on: int | None = None,
        scores: list[float] | None = None,
    ) -> None:
        self.tmp_path = tmp_path
        self.calls = 0
        self.fail_once_on = fail_once_on
        self.scores = scores or []

    def run(self, settings, bundle):
        self.calls += 1
        if self.calls == self.fail_once_on:
            self.fail_once_on = None
            raise RuntimeError("injected final evaluation failure")
        assert settings.rate_limit_retries == settings.model_endpoint.max_retries
        root = self.tmp_path / f"oracle-{self.calls}"
        root.mkdir()
        score = root / "score.json"
        raw = root / "raw.json"
        score_value = self.scores[self.calls - 1] if self.calls <= len(self.scores) else 1.0
        score.write_text(json.dumps({"score": score_value}), encoding="utf-8")
        raw.write_text("{}", encoding="utf-8")
        record = TaskRunRecord(
            run_id=f"oracle-{self.calls}",
            matrix_cell_id=settings.matrix_cell_id,
            baseline="coevoskills",
            benchmark="wildclawbench",
            model_label=settings.model_label,
            model_endpoint_id=settings.model_endpoint.model_endpoint_id,
            provider_model_id=settings.model_endpoint.provider_model_id,
            endpoint_config_hash=settings.endpoint_config_hash,
            algorithm_param_hash=settings.algorithm_param_hash,
            benchmark_source_hash=settings.benchmark_source_hash,
            task_set_hash=settings.task_set_hash,
            loader_version="test",
            loader_manifest_schema_version="test",
            score_parser_version="test",
            run_config_hash=settings.run_config_hash,
            task_id="01_Productivity_Flow_task_1_demo",
            skill_hash="test-skill",
            prompt_hash=None,
            loader_manifest_path=str(root / "loader.json"),
            score_path=str(score),
            raw_output_path=str(raw),
            status="succeeded",
            evaluation_mode=settings.evaluation_mode,
            context_budget_profile=settings.context_budget_profile,
            visible_feedback_policy_id=settings.visible_feedback_policy_id,
            oracle_call_budget=settings.oracle_call_budget,
            task_sample_policy_id=settings.task_sample_policy_id,
            round_budget=settings.round_budget,
            token_budget=settings.token_budget,
            error_summary=None,
        )
        return SimpleNamespace(records=[record])


def _settings(tmp_path: Path, wildclaw_root: Path) -> CoEvoSkillsWildClawRunSettings:
    endpoint = ModelEndpointProfile(
        model_endpoint_id="test",
        model_label="test",
        provider="openai-completions",
        provider_model_id="test-model",
        base_url="https://example.test/v1",
        api_key="test-key",
    )
    return CoEvoSkillsWildClawRunSettings(
        run_root=tmp_path / "run",
        project_root=ROOT,
        skilllift_root=ROOT / "skilllift",
        wildclaw_root=wildclaw_root,
        model_endpoint=endpoint,
        matrix_cell_id="native__coevoskills__wildclaw__test",
        model_label="test",
        endpoint_config_hash=endpoint_config_hash(endpoint),
        algorithm_param_hash=stable_hash({"baseline": "coevoskills"}),
        benchmark_source_hash="bench",
        task_set_hash="taskset",
        run_config_hash="runconfig",
        evaluation_mode="native_end_to_end",
        context_budget_profile="native_default",
        visible_feedback_policy_id="public_sanitized_v1",
        oracle_call_budget="native_default",
        round_budget="paper_default",
        token_budget="native_default",
        tasks_mode="single",
        task_filter="01-01",
        algorithm_params={
            "max_oracle_interventions": 5,
            "max_surrogate_retries": 15,
            "evaluation_repeats": 1,
            "surrogate_docker_image": None,
        },
    )


def _write_task(root: Path) -> None:
    path = root / "tasks" / "01_Productivity_Flow" / "01_Productivity_Flow_task_1_demo.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nid: 01_Productivity_Flow_task_1_demo\ntimeout_seconds: 30\n---\n"
        "## Prompt\n\nProduce result.txt.\n\n## Workspace Path\n\nworkspace\n\n"
        "## Grading Criteria\n\nSECRET_GRADING_MARKER\n\n"
        "## Automated Checks\n\nSECRET_GRADING_MARKER\n",
        encoding="utf-8",
    )
    (root / "skills").mkdir()


def test_runner_separates_surrogate_rollout_oracle_and_final_evaluation(tmp_path: Path) -> None:
    wildclaw_root = tmp_path / "WildClawBench"
    _write_task(wildclaw_root)
    stale = tmp_path / "run" / "surrogate_rollouts" / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("old attempt", encoding="utf-8")
    artifact = FakeArtifactRunner(tmp_path)
    oracle = FakeOracleRunner(tmp_path)
    runner = CoEvoSkillsWildClawRunner(
        wildclaw_runner=oracle,
        artifact_runner=artifact,
        generator=FakeGenerator(),
        verifier=FakeVerifier(),
        surrogate_runtime=FakeRuntime(),
    )

    result = runner.run(_settings(tmp_path, wildclaw_root))

    assert result.summary["source"] == SOURCE
    assert result.summary["stop_reason"] == "oracle_passed"
    assert result.summary["surrogate_rollouts"] == 1
    assert result.summary["evaluation_repeats"] == 1
    assert result.summary["mean_score"] == 1.0
    assert artifact.calls == 1
    assert oracle.calls == 2  # one optimization Oracle and one independent evaluation
    assert len(result.artifact_records) == 1
    assert len(result.records) == 2
    assert not stale.exists()


def test_snapshot_keeps_large_binary_metadata_and_later_text_files(tmp_path: Path) -> None:
    import sys

    skilllift_root = ROOT / "skilllift"
    if str(skilllift_root) not in sys.path:
        sys.path.insert(0, str(skilllift_root))
    from coevoskills.schemas import ArtifactSnapshot

    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    pdf = artifact_root / "MAE.pdf"
    pdf.write_bytes(b"%PDF-" + b"x" * 2_100_000)
    (artifact_root / "summary.md").write_text("# MAE summary\n", encoding="utf-8")
    record = ArtifactRolloutRecord(
        run_id="large-artifact",
        task_id="task",
        status="succeeded",
        output_dir=str(tmp_path),
        artifact_root=str(artifact_root),
        loader_manifest_path=str(tmp_path / "loader.json"),
        raw_output_path=str(tmp_path / "raw.json"),
        usage={},
    )

    snapshot = _snapshot(ArtifactSnapshot, record)

    assert [item.path for item in snapshot.files] == ["MAE.pdf", "summary.md"]
    assert snapshot.files[0].size_bytes == pdf.stat().st_size
    assert snapshot.files[0].text_excerpt is None
    assert snapshot.files[1].text_excerpt == "# MAE summary\n"


def test_runner_uses_best_final_evaluation_as_primary_score(tmp_path: Path) -> None:
    wildclaw_root = tmp_path / "WildClawBench"
    _write_task(wildclaw_root)
    settings = _settings(tmp_path, wildclaw_root)
    settings = replace(
        settings,
        algorithm_params={**settings.algorithm_params, "evaluation_repeats": 3},
    )
    runner = CoEvoSkillsWildClawRunner(
        wildclaw_runner=FakeOracleRunner(
            tmp_path,
            scores=[1.0, 0.2, 0.8, 0.4],
        ),
        artifact_runner=FakeArtifactRunner(tmp_path),
        generator=FakeGenerator(),
        verifier=FakeVerifier(),
        surrogate_runtime=FakeRuntime(),
    )

    result = runner.run(settings)

    assert result.summary["mean_score"] == 0.8
    assert result.summary["best_score"] == 0.8
    assert result.summary["evaluation_mean_score"] == pytest.approx(0.4666666667)
    assert result.summary["score_aggregation"] == "max"
    evaluation = json.loads(
        (
            settings.run_root
            / "coevoskills_experiment"
            / "evaluation"
            / "summary.json"
        ).read_text(encoding="utf-8")
    )
    assert evaluation["scores"] == [0.2, 0.8, 0.4]
    assert evaluation["best_score"] == 0.8
    assert evaluation["mean_score"] == pytest.approx(0.4666666667)


def test_runner_resumes_only_missing_final_evaluation_repeat(tmp_path: Path) -> None:
    wildclaw_root = tmp_path / "WildClawBench"
    _write_task(wildclaw_root)
    artifact = FakeArtifactRunner(tmp_path)
    oracle = FakeOracleRunner(tmp_path, fail_once_on=3)
    settings = _settings(tmp_path, wildclaw_root)
    settings = replace(
        settings,
        algorithm_params={**settings.algorithm_params, "evaluation_repeats": 2},
    )

    first_runner = CoEvoSkillsWildClawRunner(
        wildclaw_runner=oracle,
        artifact_runner=artifact,
        generator=FakeGenerator(),
        verifier=FakeVerifier(),
        surrogate_runtime=FakeRuntime(),
    )
    with pytest.raises(RuntimeError, match="injected final evaluation failure"):
        first_runner.run(settings)

    assert artifact.calls == 1
    assert oracle.calls == 3
    assert (
        settings.run_root
        / "coevoskills_experiment"
        / "evaluation"
        / "repeats"
        / "repeat_001.json"
    ).is_file()

    class ResumeGenerator(FakeGenerator):
        def initialize(self, task):
            raise AssertionError("completed evolution must not restart")

    resumed = CoEvoSkillsWildClawRunner(
        wildclaw_runner=oracle,
        artifact_runner=artifact,
        generator=ResumeGenerator(),
        verifier=FakeVerifier(),
        surrogate_runtime=FakeRuntime(),
    ).run(settings)

    assert artifact.calls == 1
    assert oracle.calls == 4
    assert resumed.summary["evaluation_repeats"] == 2
    assert resumed.summary["mean_score"] == 1.0
    assert resumed.resume["rerun"] == 1
