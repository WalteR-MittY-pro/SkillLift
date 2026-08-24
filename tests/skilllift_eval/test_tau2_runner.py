from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from skilllift_eval.model_endpoints import ModelEndpointProfile, endpoint_config_hash
from skilllift_eval.parsers.tau2_scores import parse_tau2_simulation
from skilllift_eval.runners.tau2 import EMPTY_TAU2_PROMPT_HASH, Tau2RunSettings, Tau2Runner
from skilllift_eval.schemas import stable_hash


class FakeTask:
    def __init__(self, task_id: str) -> None:
        self.id = task_id

    def model_dump(self, mode: str = "json") -> dict[str, str]:
        return {"id": self.id, "user_scenario": "public scenario"}


class FakeRewardInfo:
    reward = 0.75

    def model_dump(self, mode: str = "json") -> dict[str, float]:
        return {"reward": self.reward}


class FakeMessage:
    def __init__(self, role: str, content: str, usage: dict[str, int] | None = None) -> None:
        self.role = role
        self.content = content
        self.usage = usage

    def model_dump(self, mode: str = "json") -> dict[str, object]:
        return {"role": self.role, "content": self.content, "usage": self.usage}


class FakeSimulation:
    task_id = "airline-task-1"
    id = "simulation-1"
    agent_cost = 0.01
    user_cost = 0.02
    reward_info = FakeRewardInfo()

    def get_messages(self) -> list[FakeMessage]:
        return [
            FakeMessage("user", "hello"),
            FakeMessage(
                "assistant",
                "I can help.",
                {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            ),
        ]

    def model_dump(self, mode: str = "json") -> dict[str, object]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "reward_info": self.reward_info.model_dump(mode=mode),
            "messages": [m.model_dump(mode=mode) for m in self.get_messages()],
            "agent_cost": self.agent_cost,
            "user_cost": self.user_cost,
        }


def endpoint() -> ModelEndpointProfile:
    return ModelEndpointProfile(
        model_endpoint_id="gpt-5.4",
        model_label="gpt-5.4",
        provider="openai-completions",
        provider_model_id="gpt-5.4",
        base_url="https://example.test/v1",
        api_key="literal-secret-for-test",
    )


def settings(tmp_path: Path) -> Tau2RunSettings:
    ep = endpoint()
    return Tau2RunSettings(
        run_root=tmp_path,
        model_endpoint=ep,
        matrix_cell_id="native_end_to_end__no_skill__tau2__gpt-5_4",
        model_label="gpt-5.4",
        endpoint_config_hash=endpoint_config_hash(ep),
        algorithm_param_hash=stable_hash({"baseline": "no_skill", "profile": "paper_default"}),
        benchmark_source_hash="bench",
        task_set_hash="taskset",
        run_config_hash="runconfig",
        evaluation_mode="native_end_to_end",
        context_budget_profile="native_default",
        visible_feedback_policy_id="public_sanitized_v1",
        oracle_call_budget="native_default",
        task_sample_policy_id="full_native_v1",
        round_budget="paper_default",
        token_budget="native_default",
        domains=("airline",),
        split="base",
        tasks_mode="full",
        prompt_hash=EMPTY_TAU2_PROMPT_HASH,
    )


def test_tau2_score_parser_reads_reward_and_usage() -> None:
    parsed = parse_tau2_simulation(FakeSimulation())

    assert parsed["score"] == 0.75
    assert parsed["usage"]["actual_usage_status"] == "available"
    assert parsed["usage"]["total_tokens"] == 18
    assert parsed["cost_actual"] == 0.03


def test_tau2_runner_writes_task_record_and_sanitized_artifacts(tmp_path: Path) -> None:
    runner = Tau2Runner(
        task_loader=lambda domain, split, task_ids=None: [FakeTask("airline-task-1")],
        task_runner=lambda config, task, seed: FakeSimulation(),
    )

    result = runner.run(settings(tmp_path))

    assert len(result.records) == 1
    record = result.records[0]
    assert record.status == "succeeded"
    assert record.prompt_hash == EMPTY_TAU2_PROMPT_HASH
    assert Path(record.loader_manifest_path).exists()
    assert Path(record.raw_output_path).exists()
    assert Path(record.score_path).exists()
    assert result.summary["mean_score"] == 0.75

    sanitized = json.loads((Path(record.raw_output_path).parent / "sanitized_feedback.json").read_text())
    assert sanitized["score"] == 0.75
    assert sanitized["hidden_fields_removed"] == []

    encoded = json.dumps(json.loads((Path(record.raw_output_path).parent / "run_config.redacted.json").read_text()))
    assert "literal-secret-for-test" not in encoded
    assert '"api_key"' not in encoded


def test_tau2_runner_skips_when_record_hashes_and_artifacts_match(tmp_path: Path) -> None:
    calls = {"count": 0}

    def run_once(config: object, task: FakeTask, seed: int) -> FakeSimulation:
        calls["count"] += 1
        return FakeSimulation()

    runner = Tau2Runner(
        task_loader=lambda domain, split, task_ids=None: [FakeTask("airline-task-1")],
        task_runner=run_once,
    )
    cfg = settings(tmp_path)

    first = runner.run(cfg)
    second = runner.run(cfg)

    assert calls["count"] == 1
    assert first.records[0].status == "succeeded"
    assert second.records[0].status == "succeeded"
    assert second.resume["skipped"] == 1


def test_tau2_runner_reruns_when_prompt_hash_changes(tmp_path: Path) -> None:
    calls = {"count": 0}

    def run_once(config: object, task: FakeTask, seed: int) -> FakeSimulation:
        calls["count"] += 1
        return FakeSimulation()

    runner = Tau2Runner(
        task_loader=lambda domain, split, task_ids=None: [FakeTask("airline-task-1")],
        task_runner=run_once,
    )
    cfg = settings(tmp_path)

    runner.run(cfg)
    runner.run(replace(cfg, prompt_hash="sha256:changed"))

    assert calls["count"] == 2


def test_tau2_runner_reruns_when_required_artifact_is_missing(tmp_path: Path) -> None:
    calls = {"count": 0}

    def run_once(config: object, task: FakeTask, seed: int) -> FakeSimulation:
        calls["count"] += 1
        return FakeSimulation()

    runner = Tau2Runner(
        task_loader=lambda domain, split, task_ids=None: [FakeTask("airline-task-1")],
        task_runner=run_once,
    )
    cfg = settings(tmp_path)

    result = runner.run(cfg)
    Path(result.records[0].score_path).unlink()
    rerun = runner.run(cfg)

    assert calls["count"] == 2
    assert rerun.resume["rerun"] == 1


def test_tau2_runner_records_failed_artifact_when_task_split_is_missing(tmp_path: Path) -> None:
    runner = Tau2Runner(
        task_loader=lambda domain, split, task_ids=None: (_ for _ in ()).throw(ValueError("Invalid split base")),
        task_runner=lambda config, task, seed: FakeSimulation(),
    )

    result = runner.run(settings(tmp_path))

    assert len(result.records) == 1
    record = result.records[0]
    assert record.status == "failed"
    assert record.task_id == "__task_set_resolution__"
    assert "Invalid split base" in json.loads(Path(record.score_path).read_text())["error_summary"]
    assert result.summary["status"] == "FAILED"


def test_tau2_runner_rejects_eval_split_to_avoid_empty_phase3_runs(tmp_path: Path) -> None:
    runner = Tau2Runner(
        task_loader=lambda domain, split, task_ids=None: [FakeTask("airline-task-1")],
        task_runner=lambda config, task, seed: FakeSimulation(),
    )

    try:
        runner.run(replace(settings(tmp_path), split="eval"))
    except ValueError as exc:
        assert "base split" in str(exc)
    else:
        raise AssertionError("eval split should be rejected for Phase 3 tau2 runs")
