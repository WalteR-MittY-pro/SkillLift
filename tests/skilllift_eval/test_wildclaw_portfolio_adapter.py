from __future__ import annotations

import json
from pathlib import Path

import pytest

from skilllift_eval.model_endpoints import ModelEndpointProfile
from skilllift_eval.runners.wildclaw import WildClawRunSettings, WildClawRunner

from skilllift.adapters.wildclawbench import WildClawPortfolioAdapter
from skilllift.coordinator import TrialSpec


def _task(root: Path) -> None:
    path = root / "tasks" / "Demo" / "Demo_task_1.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        """---
id: demo_task_1
category: Demo
timeout_seconds: 30
---
## Prompt

Do the public task.

## Workspace Path

workspace

## Automated Checks

SECRET_GRADER_SENTINEL

## Skills

native-skill
""",
        encoding="utf-8",
    )
    native = root / "skills" / "native-skill"
    (native / "scripts").mkdir(parents=True)
    (native / "SKILL.md").write_text("# Native\n", encoding="utf-8")
    (native / "scripts" / "run.py").write_text("print('NATIVE_AUX')\n", encoding="utf-8")


def _endpoint() -> ModelEndpointProfile:
    return ModelEndpointProfile(
        model_endpoint_id="test",
        model_label="test",
        provider="openai-completions",
        provider_model_id="test-model",
        base_url="https://example.test/v1",
        api_key="secret",
    )


def test_wildclaw_adapter_uses_public_view_and_complete_native_seed(tmp_path: Path) -> None:
    wildclaw_root = tmp_path / "WildClawBench"
    _task(wildclaw_root)

    def fake_task_runner(**kwargs):
        return {
            "task_id": kwargs["task"]["task_id"],
            "scores": {"overall_score": 0.75},
            "usage": {"total_tokens": 2},
            "error": None,
            "output_dir": str(tmp_path / "output"),
        }

    def settings_factory(run_root: Path) -> WildClawRunSettings:
        return WildClawRunSettings(
            run_root=run_root,
            wildclaw_root=wildclaw_root,
            model_endpoint=_endpoint(),
            matrix_cell_id="cell",
            model_label="test",
            endpoint_config_hash="endpoint",
            algorithm_param_hash="algorithm",
            benchmark_source_hash="benchmark",
            task_set_hash="tasks",
            run_config_hash="run",
            evaluation_mode="native_end_to_end",
            context_budget_profile="native",
            visible_feedback_policy_id="public",
            oracle_call_budget="bounded",
            task_sample_policy_id="single_task_portfolio",
            round_budget="three_rounds",
            token_budget="tracked",
            tasks_mode="single",
            task_filter="demo_task_1",
        )

    adapter = WildClawPortfolioAdapter(
        wildclaw_root=wildclaw_root,
        run_root=tmp_path / "run",
        task_filter="demo_task_1",
        settings_factory=settings_factory,
        runner=WildClawRunner(task_runner=fake_task_runner),
        config_hash="cfg",
    )

    public = adapter.public_task("demo_task_1")
    seed = adapter.seed_portfolio("demo_task_1")
    evaluation = adapter.evaluate("demo_task_1", seed, TrialSpec("trial-1", "anchor", 0))

    assert "SECRET_GRADER_SENTINEL" not in public.text
    assert seed.curated_skill_names == frozenset({"native-skill"})
    assert (seed.root / "native-skill" / "scripts" / "run.py").read_text(encoding="utf-8") == "print('NATIVE_AUX')\n"
    assert evaluation.is_valid and evaluation.reward == 0.75
    adapter.records.clear()
    assert adapter.recover("demo_task_1", seed, TrialSpec("trial-1", "anchor", 0)).reward == 0.75
    assert len(adapter.records) == 1
    assert (wildclaw_root / "skills" / "native-skill" / "scripts" / "run.py").is_file()


def test_wildclaw_adapter_rejects_tasks_without_preprovisioned_skills(tmp_path: Path) -> None:
    wildclaw_root = tmp_path / "WildClawBench"
    _task(wildclaw_root)
    task_path = wildclaw_root / "tasks" / "Demo" / "Demo_task_1.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace("native-skill\n", ""),
        encoding="utf-8",
    )

    adapter = object.__new__(WildClawPortfolioAdapter)
    adapter.wildclaw_root = wildclaw_root.resolve()
    adapter.run_root = (tmp_path / "run").resolve()
    adapter.task_filter = "demo_task_1"
    adapter.config_hash = "cfg"

    with pytest.raises(ValueError, match="pre-provisioned skill"):
        adapter.seed_portfolio("demo_task_1")


def test_wildclaw_recovery_rejects_stale_portfolio_record(tmp_path: Path) -> None:
    wildclaw_root = tmp_path / "WildClawBench"
    _task(wildclaw_root)

    def fake_task_runner(**kwargs):
        return {
            "task_id": kwargs["task"]["task_id"],
            "scores": {"overall_score": 0.75},
            "usage": {"total_tokens": 2},
            "error": None,
            "output_dir": str(tmp_path / "output"),
        }

    def settings_factory(run_root: Path) -> WildClawRunSettings:
        return WildClawRunSettings(
            run_root=run_root,
            wildclaw_root=wildclaw_root,
            model_endpoint=_endpoint(),
            matrix_cell_id="cell",
            model_label="test",
            endpoint_config_hash="endpoint",
            algorithm_param_hash="algorithm",
            benchmark_source_hash="benchmark",
            task_set_hash="tasks",
            run_config_hash="run",
            evaluation_mode="native_end_to_end",
            context_budget_profile="native",
            visible_feedback_policy_id="public",
            oracle_call_budget="bounded",
            task_sample_policy_id="single_task_portfolio",
            round_budget="three_rounds",
            token_budget="tracked",
            tasks_mode="single",
            task_filter="demo_task_1",
        )

    adapter = WildClawPortfolioAdapter(
        wildclaw_root=wildclaw_root,
        run_root=tmp_path / "run",
        task_filter="demo_task_1",
        settings_factory=settings_factory,
        runner=WildClawRunner(task_runner=fake_task_runner),
        config_hash="cfg",
    )
    seed = adapter.seed_portfolio("demo_task_1")
    trial = TrialSpec("trial-1", "anchor", 0)
    evaluation = adapter.evaluate("demo_task_1", seed, trial)
    marker = Path(evaluation.artifact_ref)
    marker.unlink()
    record_path = Path(evaluation.usage_ref)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["skill_hash"] = "stale-portfolio"
    record_path.write_text(json.dumps(record), encoding="utf-8")

    assert adapter.recover("demo_task_1", seed, trial) is None
