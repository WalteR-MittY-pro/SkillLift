from __future__ import annotations

import json
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from skilllift.schemas import (
    EvoSkill,
    OracleFeedback,
    OracleScore,
    Receipt,
    RubricCriterion,
    SkillKey,
    TaskSpec,
    VerifierScore,
)
from skilllift_eval.runners.skilllift_tau2 import (
    SkillLiftTau2TrainConfig,
    SkillLiftTau2TrainRunner,
    Tau2SkillBatchResult,
    _build_per_task_rankings,
    _default_tau2_run_single_task,
    _install_tau2_nl_assertion_llm_defaults,
    _patch_tau2_completion_for_noargs,
    _sanitize_noargs_tool_calls,
    build_receipt_revision_input,
    evaluate_tau2_skill_batch,
    load_train_clusters,
    write_train_cluster_artifact,
)


ROOT = Path(__file__).resolve().parents[2]
TAU2_SRC = ROOT / "tau2-bench" / "src"


class FakeRewardInfo:
    def __init__(self, reward: float) -> None:
        self.reward = reward

    def model_dump(self, mode: str = "json") -> dict[str, float]:
        return {"reward": self.reward}


class FakeToolCall:
    def __init__(self, name: str, requestor: str = "assistant") -> None:
        self.name = name
        self.requestor = requestor


class FakeMessage:
    def __init__(self, role: str, content: str, tool_calls: list[FakeToolCall] | None = None) -> None:
        self.role = role
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, mode: str = "json") -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class FakeSimulation:
    def __init__(self, task_id: str, reward: float) -> None:
        self.task_id = task_id
        self.id = f"sim-{task_id}"
        self.reward_info = FakeRewardInfo(reward)

    def get_messages(self) -> list[FakeMessage]:
        return [
            FakeMessage("user", "public request"),
            FakeMessage("assistant", "public response"),
        ]

    def model_dump(self, mode: str = "json") -> dict[str, object]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "reward_info": self.reward_info.model_dump(mode=mode),
            "messages": [message.model_dump(mode=mode) for message in self.get_messages()],
            "evaluation_criteria": "must not leak",
            "gold_action": "must not leak",
            "target_db": "must not leak",
        }


class FakeBehaviorSimulation(FakeSimulation):
    termination_reason = "max_steps"

    def get_messages(self) -> list[FakeMessage]:
        return [
            FakeMessage("assistant", "", [FakeToolCall("check_status")]),
            FakeMessage("user", "", [FakeToolCall("toggle_setting", "user")]),
            FakeMessage("assistant", "Please try again and recheck."),
        ]


class FakeTau2Task:
    def __init__(self, task_id: str) -> None:
        self.id = task_id


class FakeFunctionCall:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class FakeRawToolCall:
    def __init__(self, name: str, arguments: str) -> None:
        self.id = f"call-{name}"
        self.function = FakeFunctionCall(name, arguments)


class FakeAssistantChoice:
    def __init__(self, tool_calls: list[FakeRawToolCall]) -> None:
        self.message = types.SimpleNamespace(tool_calls=tool_calls)


class FakeCompletionResponse:
    def __init__(self, tool_calls: list[FakeRawToolCall]) -> None:
        self.choices = [FakeAssistantChoice(tool_calls)]


def _skill(slot: int, body: str) -> EvoSkill:
    return EvoSkill(
        SkillKey(slot, 1),
        f"guide-{slot}",
        {"SKILL.md": body},
        None,
        {"prompt_hash": f"prompt-{slot}", "source_model": "fake-model"},
    )


def _config(tmp_path: Path) -> SkillLiftTau2TrainConfig:
    return SkillLiftTau2TrainConfig(
        run_root=tmp_path / "run",
        manifest_path=tmp_path / "tau2_train_cluster_manifest.json",
        model="fake-model",
        domain_policy_by_domain={"airline": "Only use public airline policy text."},
        oracle_threshold=0.5,
        token_budget="1000",
        seed=42,
    )


def _receipt() -> Receipt:
    return Receipt(
        1,
        [RubricCriterion("r1", "Outcome", "Completes the task", 1)],
        1,
        0,
    )


def _install_fake_tau2_nl_assertion_modules(monkeypatch):
    tau2_module = types.ModuleType("tau2")
    tau2_module.__path__ = []
    evaluator_package = types.ModuleType("tau2.evaluator")
    evaluator_package.__path__ = []
    tau2_config = types.ModuleType("tau2.config")
    tau2_config.DEFAULT_LLM_NL_ASSERTIONS = "gpt-4.1-2025-04-14"
    tau2_config.DEFAULT_LLM_NL_ASSERTIONS_ARGS = {"temperature": 0.0}
    nl_assertions = types.ModuleType("tau2.evaluator.evaluator_nl_assertions")
    nl_assertions.DEFAULT_LLM_NL_ASSERTIONS = "gpt-4.1-2025-04-14"
    nl_assertions.DEFAULT_LLM_NL_ASSERTIONS_ARGS = {"temperature": 0.0}
    tau2_module.config = tau2_config
    tau2_module.evaluator = evaluator_package
    evaluator_package.evaluator_nl_assertions = nl_assertions
    monkeypatch.setitem(sys.modules, "tau2", tau2_module)
    monkeypatch.setitem(sys.modules, "tau2.config", tau2_config)
    monkeypatch.setitem(sys.modules, "tau2.evaluator", evaluator_package)
    monkeypatch.setitem(sys.modules, "tau2.evaluator.evaluator_nl_assertions", nl_assertions)
    return tau2_config, nl_assertions


def test_train_config_keeps_existing_positional_args_compatible(tmp_path: Path) -> None:
    config = SkillLiftTau2TrainConfig(
        tmp_path / "run",
        tmp_path / "manifest.json",
        "fake-model",
        0.5,
        "1000",
        42,
        12,
    )

    assert config.oracle_threshold == 0.5
    assert config.token_budget == "1000"
    assert config.seed == 42
    assert config.max_steps == 12
    assert config.domain_policy_by_domain == {}


def test_load_train_clusters_keeps_size_one_and_noop_batches(tmp_path: Path) -> None:
    manifest_path = tmp_path / "tau2_train_cluster_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": "v2.4",
                "split": "train",
                "phase": "train_evolve",
                "seed": 42,
                "domains": {
                    "airline": [
                        {
                            "cluster_id": "airline_noop_00",
                            "issue_type": "noop",
                            "semantic_key": "noop",
                            "task_ids": ["noop-task"],
                            "size": 1,
                        },
                        {
                            "cluster_id": "airline_book_00",
                            "issue_type": "book",
                            "semantic_key": "book",
                            "task_ids": ["book-1", "book-2"],
                            "size": 2,
                        },
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    clusters = load_train_clusters(manifest_path)

    assert [cluster.cluster_id for cluster in clusters] == ["airline_noop_00", "airline_book_00"]
    assert clusters[0].task_ids == ["noop-task"]
    assert clusters[0].semantic_key == "noop"


def test_load_train_clusters_rejects_unknown_manifest_version(tmp_path: Path) -> None:
    manifest_path = tmp_path / "tau2_train_cluster_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": "v2.5",
                "split": "train",
                "phase": "train_evolve",
                "domains": {"airline": []},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="manifest version"):
        load_train_clusters(manifest_path)


def test_evaluate_tau2_skill_batch_builds_scores_rankings_and_revision_input(tmp_path: Path) -> None:
    config = _config(tmp_path)
    tasks = [
        {"id": "task-a", "domain": "airline"},
        {"id": "task-b", "domain": "airline"},
    ]
    skills = {
        SkillKey(0, 1): _skill(0, "check before acting"),
        SkillKey(1, 1): _skill(1, "verify after acting"),
    }
    rewards = {
        ("task-a", "s000_v001"): 0.2,
        ("task-a", "s001_v001"): 0.8,
        ("task-b", "s000_v001"): 1.0,
        ("task-b", "s001_v001"): 1.0,
    }
    calls: list[dict[str, object]] = []

    def fake_run_single_task(run_config: dict[str, object], task: dict[str, str], seed: int) -> FakeSimulation:
        calls.append({"config": run_config, "task": task, "seed": seed})
        task_id = str(task["id"])
        skill_key = str(run_config["skilllift_skill_key"])
        return FakeSimulation(task_id, rewards[(task_id, skill_key)])

    result = evaluate_tau2_skill_batch(
        tasks,
        skills,
        config,
        run_single_task=fake_run_single_task,
    )

    assert len(result.task_skill_scores) == 4
    assert len(calls) == 4
    assert all(call["seed"] == 42 for call in calls)
    assert all(call["config"]["agent"] == "skill_injected" for call in calls)
    assert all("tau2_prompt_context" in call["config"] for call in calls)
    assert all(isinstance(call["config"]["tau2_prompt_context"], dict) for call in calls)
    assert all(call["config"]["llm_agent"] == "fake-model" for call in calls)
    first_context = calls[0]["config"]["tau2_prompt_context"]
    assert first_context["kind"] == "tau2_prompt_context"
    first_payload = json.loads(Path(first_context["prompt_payload_path"]).read_text(encoding="utf-8"))
    assert "check before acting" in first_payload["prompt"]
    assert "Only use public airline policy text." in first_payload["prompt"]
    assert "/root/skills" not in first_payload["prompt"]

    score = result.task_skill_scores[("task-a", SkillKey(1, 1))]
    assert score.oracle_score == 0.8
    assert score.oracle_pass == 1
    assert score.feedback.summary == "reward=0.800 pass=1"
    encoded_score = json.dumps(score.to_dict(), ensure_ascii=False)
    assert "messages" not in encoded_score
    assert "transcript" not in encoded_score
    assert "evaluation_criteria" not in encoded_score
    assert "gold_action" not in encoded_score
    assert "target_db" not in encoded_score

    task_a_ranking = result.per_task_rankings[0]
    assert task_a_ranking.task_id == "task-a"
    assert task_a_ranking.has_signal is True
    assert task_a_ranking.oracle_rank == [SkillKey(1, 1), SkillKey(0, 1)]

    task_b_ranking = result.per_task_rankings[1]
    assert task_b_ranking.task_id == "task-b"
    assert task_b_ranking.has_signal is False
    assert task_b_ranking.oracle_rank == []
    assert task_b_ranking.no_signal_reason == "all skills tied on oracle_score/oracle_pass"

    assert result.aggregate_oracle_scores[SkillKey(0, 1)].oracle_score == 0.6
    assert result.aggregate_oracle_scores[SkillKey(1, 1)].oracle_score == 0.9
    assert result.aggregate_oracle_scores[SkillKey(0, 1)].feedback.metadata["pass_aggregation"] == "majority_ge_0.5"
    assert result.aggregate_oracle_rank == [SkillKey(1, 1), SkillKey(0, 1)]

    verifier_scores = {
        SkillKey(0, 1): VerifierScore(SkillKey(0, 1), 1, {"r1": True}, 1, 1.0),
        SkillKey(1, 1): VerifierScore(SkillKey(1, 1), 1, {"r1": False}, 0, 0.0),
    }
    revision_input = build_receipt_revision_input(
        task=TaskSpec("airline_train_cluster", "train cluster"),
        receipt=_receipt(),
        skills=skills,
        verifier_scores=verifier_scores,
        batch_result=result,
    )

    assert revision_input.per_task_rankings is not None
    assert revision_input.oracle_scores[SkillKey(1, 1)].oracle_score == 0.9
    assert revision_input.oracle_rank == [SkillKey(1, 1), SkillKey(0, 1)]
    assert revision_input.rank_alignment == -1.0
    assert revision_input.oracle_contrast["oracle_rank_groups"] == [
        ["s001_v001"],
        ["s000_v001"],
    ]


def test_build_receipt_revision_input_returns_no_alignment_for_all_ties() -> None:
    skills = {
        key: _skill(key.slot, f"skill-{key.slot}")
        for key in (SkillKey(0, 1), SkillKey(1, 1))
    }
    receipt = _receipt()
    verifier_scores = {
        key: VerifierScore(key, 1, {"r1": True}, 1, 1.0)
        for key in skills
    }
    tied_oracle = {
        key: OracleScore(key, 0.5, 0)
        for key in skills
    }
    batch = Tau2SkillBatchResult(
        task_skill_scores={},
        per_task_rankings=[],
        aggregate_oracle_scores=tied_oracle,
        aggregate_oracle_rank=list(skills),
    )

    revision_input = build_receipt_revision_input(
        task=TaskSpec("tau2", "public task"),
        receipt=receipt,
        skills=skills,
        verifier_scores=verifier_scores,
        batch_result=batch,
    )

    assert revision_input.rank_alignment is None
    assert revision_input.oracle_contrast["oracle_has_signal"] is False


def test_evaluate_tau2_skill_batch_adds_public_behavior_summary(tmp_path: Path) -> None:
    config = _config(tmp_path)
    skills = {SkillKey(0, 1): _skill(0, "delegate one action then verify")}

    def fake_run_single_task(run_config: dict[str, object], task: dict[str, str], seed: int) -> FakeSimulation:
        return FakeBehaviorSimulation(str(task["id"]), 0.0)

    result = evaluate_tau2_skill_batch(
        [{"id": "task-a", "domain": "airline"}],
        skills,
        config,
        run_single_task=fake_run_single_task,
    )

    metadata = result.aggregate_oracle_scores[SkillKey(0, 1)].feedback.metadata
    summary = metadata["execution_behavior_summary"]

    assert summary["task_count"] == 1
    assert summary["termination_counts"] == {"max_steps": 1}
    assert summary["average_counts"]["observe"] == 1.0
    assert summary["average_counts"]["external_action"] == 1.0
    assert summary["average_counts"]["stall_after_action"] == 1.0
    assert "retry_or_recheck_after_final_observable_action" in summary["patterns"]
    encoded = json.dumps(summary, ensure_ascii=False)
    assert "check_status" not in encoded
    assert "toggle_setting" not in encoded
    assert "messages" not in encoded
    assert "evaluation_criteria" not in encoded
    assert "gold_action" not in encoded


def test_evaluate_tau2_skill_batch_uses_explicit_domain_for_tau2_task_objects(tmp_path: Path) -> None:
    config = _config(tmp_path)
    task = FakeTau2Task("task-a")
    skill = _skill(0, "object task guide")
    calls: list[dict[str, object]] = []

    def fake_run_single_task(run_config: dict[str, object], task_obj: FakeTau2Task, seed: int) -> FakeSimulation:
        calls.append(run_config)
        return FakeSimulation(task_obj.id, 1.0)

    evaluate_tau2_skill_batch(
        [task],
        {SkillKey(0, 1): skill},
        config,
        domain="airline",
        run_single_task=fake_run_single_task,
    )

    assert calls[0]["domain"] == "airline"
    payload = json.loads(Path(calls[0]["tau2_prompt_context"]["prompt_payload_path"]).read_text(encoding="utf-8"))
    assert "object task guide" in payload["prompt"]


def test_evaluate_tau2_skill_batch_retries_empty_assistant_message_once(tmp_path: Path) -> None:
    config = _config(tmp_path)
    task = {"id": "task-a", "domain": "airline"}
    skill = _skill(0, "retry guide")
    calls = 0

    def flaky_run_single_task(run_config: dict[str, object], task_obj: dict[str, str], seed: int) -> FakeSimulation:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("AssistantMessage must have either content or tool_calls. Got AssistantMessage\nis_final_chunk: True")
        return FakeSimulation(str(task_obj["id"]), 1.0)

    result = evaluate_tau2_skill_batch(
        [task],
        {SkillKey(0, 1): skill},
        config,
        run_single_task=flaky_run_single_task,
    )

    assert calls == 2
    assert result.task_skill_scores[("task-a", SkillKey(0, 1))].oracle_score == 1.0


def test_evaluate_tau2_skill_batch_retries_empty_user_message_once(tmp_path: Path) -> None:
    config = _config(tmp_path)
    task = {"id": "task-a", "domain": "airline"}
    skill = _skill(0, "retry guide")
    calls = 0

    def flaky_run_single_task(run_config: dict[str, object], task_obj: dict[str, str], seed: int) -> FakeSimulation:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("UserMessage must have either content or tool_calls. Got UserMessage\nis_final_chunk: True")
        return FakeSimulation(str(task_obj["id"]), 1.0)

    result = evaluate_tau2_skill_batch(
        [task],
        {SkillKey(0, 1): skill},
        config,
        run_single_task=flaky_run_single_task,
    )

    assert calls == 2
    assert result.task_skill_scores[("task-a", SkillKey(0, 1))].oracle_score == 1.0


def test_evaluate_tau2_skill_batch_raises_after_three_empty_assistant_messages(tmp_path: Path) -> None:
    config = _config(tmp_path)
    task = {"id": "task-a", "domain": "airline"}
    skill = _skill(0, "retry guide")
    calls = 0

    def always_empty_message(run_config: dict[str, object], task_obj: dict[str, str], seed: int) -> FakeSimulation:
        nonlocal calls
        calls += 1
        raise ValueError("AssistantMessage must have either content or tool_calls. Got AssistantMessage\nis_final_chunk: True")

    with pytest.raises(ValueError, match="AssistantMessage must have either content or tool_calls"):
        evaluate_tau2_skill_batch(
            [task],
            {SkillKey(0, 1): skill},
            config,
            run_single_task=always_empty_message,
        )

    assert calls == 3


def test_evaluate_tau2_skill_batch_raises_after_three_empty_user_messages(tmp_path: Path) -> None:
    config = _config(tmp_path)
    task = {"id": "task-a", "domain": "airline"}
    skill = _skill(0, "retry guide")
    calls = 0

    def always_empty_message(run_config: dict[str, object], task_obj: dict[str, str], seed: int) -> FakeSimulation:
        nonlocal calls
        calls += 1
        raise ValueError("UserMessage must have either content or tool_calls. Got UserMessage\nis_final_chunk: True")

    with pytest.raises(ValueError, match="UserMessage must have either content or tool_calls"):
        evaluate_tau2_skill_batch(
            [task],
            {SkillKey(0, 1): skill},
            config,
            run_single_task=always_empty_message,
        )

    assert calls == 3


def test_evaluate_tau2_skill_batch_does_not_retry_other_value_errors(tmp_path: Path) -> None:
    config = _config(tmp_path)
    task = {"id": "task-a", "domain": "airline"}
    skill = _skill(0, "retry guide")
    calls = 0

    def invalid_task(run_config: dict[str, object], task_obj: dict[str, str], seed: int) -> FakeSimulation:
        nonlocal calls
        calls += 1
        raise ValueError("different tau2 failure")

    with pytest.raises(ValueError, match="different tau2 failure"):
        evaluate_tau2_skill_batch(
            [task],
            {SkillKey(0, 1): skill},
            config,
            run_single_task=invalid_task,
        )

    assert calls == 1


def test_runner_prompt_context_is_consumed_by_tau2_skill_injected_agent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "tau2", types.ModuleType("tau2"))
    monkeypatch.setitem(sys.modules, "tau2.agent", types.ModuleType("tau2.agent"))
    monkeypatch.setitem(sys.modules, "tau2.agent.llm_agent", _fake_llm_agent_module())
    monkeypatch.setitem(sys.modules, "tau2.environment", types.ModuleType("tau2.environment"))
    monkeypatch.setitem(sys.modules, "tau2.environment.tool", types.SimpleNamespace(Tool=object))
    module = _load_skill_injected_agent_module()
    config = _config(tmp_path)
    calls: list[dict[str, object]] = []

    def fake_run_single_task(run_config: dict[str, object], task: dict[str, str], seed: int) -> FakeSimulation:
        calls.append(run_config)
        return FakeSimulation(str(task["id"]), 1.0)

    evaluate_tau2_skill_batch(
        [{"id": "task-a", "domain": "airline"}],
        {SkillKey(0, 1): _skill(0, "CANARY_RUNNER_SKILL: confirm before update")},
        config,
        run_single_task=fake_run_single_task,
    )

    assert calls[0]["agent"] == "skill_injected"
    agent = module.create_skill_injected_agent(
        tools=[],
        domain_policy="base policy from tau2 env",
        llm="dummy-model",
        llm_args={},
        tau2_prompt_context=calls[0]["tau2_prompt_context"],
    )
    system_prompt = agent.get_init_state().system_messages[0].content
    assert "CANARY_RUNNER_SKILL" in system_prompt
    assert "<policy>\nbase policy from tau2 env\n</policy>" in system_prompt


def test_default_tau2_runner_converts_dict_to_text_run_config(monkeypatch) -> None:
    captured: dict[str, object] = {}
    tau2_config, nl_assertions = _install_fake_tau2_nl_assertion_modules(monkeypatch)

    class FakeTextRunConfig:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    def fake_run_single_task(config, task, seed):
        assert tau2_config.DEFAULT_LLM_NL_ASSERTIONS == "fake-model"
        assert nl_assertions.DEFAULT_LLM_NL_ASSERTIONS == "fake-model"
        captured["config"] = config
        captured["task"] = task
        captured["seed"] = seed
        return FakeSimulation("task-a", 1.0)

    monkeypatch.setitem(sys.modules, "tau2.data_model", types.ModuleType("tau2.data_model"))
    monkeypatch.setitem(
        sys.modules,
        "tau2.data_model.simulation",
        types.SimpleNamespace(TextRunConfig=FakeTextRunConfig),
    )
    monkeypatch.setitem(sys.modules, "tau2.run", types.SimpleNamespace(run_single_task=fake_run_single_task))
    monkeypatch.setitem(sys.modules, "tau2.utils", types.ModuleType("tau2.utils"))
    monkeypatch.setitem(
        sys.modules,
        "tau2.utils.llm_utils",
        types.SimpleNamespace(completion=lambda **kwargs: None),
    )

    _default_tau2_run_single_task(
        {
            "domain": "airline",
            "task_split_name": "train",
            "agent": "skill_injected",
            "user": "user_simulator",
            "llm_agent": "fake-model",
            "llm_user": "fake-model",
            "llm_args_agent": {"base_url": "https://example.test/v1", "seed": 123},
            "llm_args_user": {},
            "max_steps": 12,
            "seed": 42,
            "tau2_prompt_context": {"kind": "tau2_prompt_context", "prompt_payload_path": "/tmp/payload.json"},
        },
        {"id": "task-a"},
        42,
    )

    config = captured["config"]
    assert isinstance(config, FakeTextRunConfig)
    assert config.agent == "skill_injected"
    assert config.tau2_prompt_context["kind"] == "tau2_prompt_context"
    assert config.llm_agent == "fake-model"
    assert captured["seed"] == 42
    assert nl_assertions.DEFAULT_LLM_NL_ASSERTIONS_ARGS == {"base_url": "https://example.test/v1"}


def test_install_tau2_nl_assertion_llm_defaults_changes_global_defaults(monkeypatch) -> None:
    tau2_config, nl_assertions = _install_fake_tau2_nl_assertion_modules(monkeypatch)

    _install_tau2_nl_assertion_llm_defaults(
        {
            "llm_agent": "gpt-5.4",
            "llm_args_agent": {
                "api_key": "secret",
                "api_base": "https://example.test/v1",
                "base_url": "https://example.test/v1",
                "custom_llm_provider": "openai",
                "timeout": 300,
                "temperature": 0,
                "num_retries": 2,
                "seed": 42,
                "tools": [{"type": "function"}],
                "response_format": {"type": "json_object"},
            },
        }
    )

    expected_args = {
        "api_key": "secret",
        "api_base": "https://example.test/v1",
        "base_url": "https://example.test/v1",
        "custom_llm_provider": "openai",
        "timeout": 300,
        "temperature": 0,
        "num_retries": 2,
    }
    assert tau2_config.DEFAULT_LLM_NL_ASSERTIONS == "gpt-5.4"
    assert tau2_config.DEFAULT_LLM_NL_ASSERTIONS_ARGS == expected_args
    assert nl_assertions.DEFAULT_LLM_NL_ASSERTIONS == "gpt-5.4"
    assert nl_assertions.DEFAULT_LLM_NL_ASSERTIONS_ARGS == expected_args


def test_sanitize_noargs_tool_calls_cleans_no_arg_tools_only() -> None:
    response = FakeCompletionResponse(
        [
            FakeRawToolCall("check_network_status", '{"_noargs":"unused"}'),
            FakeRawToolCall("check_status_bar", '{"_noargs":"unused"}'),
            FakeRawToolCall("check_app_status", '{"_noargs":"unused"}'),
        ]
    )
    tools_schema = [
        {
            "type": "function",
            "function": {
                "name": "check_network_status",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_status_bar",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_app_status",
                "parameters": {
                    "type": "object",
                    "properties": {"app_name": {"type": "string"}},
                    "required": ["app_name"],
                },
            },
        },
    ]

    _sanitize_noargs_tool_calls(response, tools_schema)

    assert response.choices[0].message.tool_calls[0].function.arguments == "{}"
    assert response.choices[0].message.tool_calls[1].function.arguments == "{}"
    assert response.choices[0].message.tool_calls[2].function.arguments == '{"_noargs":"unused"}'


def test_sanitize_noargs_tool_calls_supports_dict_response() -> None:
    response = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "check_network_status",
                                "arguments": {"_noargs": "unused"},
                            }
                        }
                    ]
                }
            }
        ]
    }
    tools_schema = [
        {
            "type": "function",
            "function": {
                "name": "check_network_status",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    _sanitize_noargs_tool_calls(response, tools_schema)

    assert response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == {}


def test_patch_tau2_completion_for_noargs_installs_and_restores(monkeypatch) -> None:
    response = FakeCompletionResponse([FakeRawToolCall("check_network_status", '{"_noargs":"unused"}')])

    def fake_completion(**kwargs):
        return response

    module = types.SimpleNamespace(completion=fake_completion)
    monkeypatch.setitem(sys.modules, "tau2", types.ModuleType("tau2"))
    monkeypatch.setitem(sys.modules, "tau2.utils", types.ModuleType("tau2.utils"))
    monkeypatch.setitem(sys.modules, "tau2.utils.llm_utils", module)

    tools_schema = [
        {
            "type": "function",
            "function": {
                "name": "check_network_status",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    with _patch_tau2_completion_for_noargs():
        assert module.completion is not fake_completion
        patched_response = module.completion(tools=tools_schema)

    assert module.completion is fake_completion
    assert patched_response.choices[0].message.tool_calls[0].function.arguments == "{}"


def test_evaluate_tau2_skill_batch_requires_public_domain_policy(tmp_path: Path) -> None:
    config = SkillLiftTau2TrainConfig(
        run_root=tmp_path / "run",
        manifest_path=tmp_path / "manifest.json",
        model="fake-model",
    )

    with pytest.raises(ValueError, match="missing public domain policy"):
        evaluate_tau2_skill_batch(
            [{"id": "task-a", "domain": "airline"}],
            {SkillKey(0, 1): _skill(0, "guide")},
            config,
            run_single_task=lambda run_config, task, seed: FakeSimulation(str(task["id"]), 1.0),
        )


def test_task_ranking_has_signal_when_score_ties_but_pass_differs() -> None:
    skills = {
        SkillKey(0, 1): _skill(0, "first guide"),
        SkillKey(1, 1): _skill(1, "second guide"),
    }
    task_scores: dict[tuple[str, SkillKey], OracleScore] = {
        ("task-a", SkillKey(0, 1)): OracleScore(
            SkillKey(0, 1),
            0.8,
            0,
            feedback=OracleFeedback(summary="r"),
        ),
        ("task-a", SkillKey(1, 1)): OracleScore(
            SkillKey(1, 1),
            0.8,
            1,
            feedback=OracleFeedback(summary="r"),
        ),
    }

    rankings = _build_per_task_rankings([{"id": "task-a"}], skills, task_scores)
    assert rankings[0].has_signal is True
    assert rankings[0].oracle_rank == [SkillKey(1, 1), SkillKey(0, 1)]
    assert rankings[0].no_signal_reason is None


def test_write_train_cluster_artifact_records_phase_and_skill_bundle_metadata(tmp_path: Path) -> None:
    config = _config(tmp_path)
    skills = {
        SkillKey(0, 1): _skill(0, "first guide"),
        SkillKey(1, 1): _skill(1, "second guide"),
    }
    result = evaluate_tau2_skill_batch(
        [{"id": "task-a", "domain": "airline"}],
        skills,
        config,
        run_single_task=lambda run_config, task, seed: FakeSimulation(str(task["id"]), 1.0),
    )

    artifact_path = write_train_cluster_artifact(
        config=config,
        cluster_id="airline_book_00",
        domain="airline",
        task_ids=["task-a"],
        skills=skills,
        batch_result=result,
        source_train_run_id="train-run-1",
    )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert artifact["skilllift_phase"] == "train_evolve"
    assert artifact["tau2_split"] == "train"
    assert artifact["cluster_id"] == "airline_book_00"
    assert artifact["task_ids"] == ["task-a"]
    assert artifact["per_task_rankings"][0]["task_id"] == "task-a"
    assert sorted(artifact["aggregate_oracle_scores"]) == ["s000_v001", "s001_v001"]
    assert sorted(artifact["cluster_level_skill_bundles"]) == ["s000_v001", "s001_v001"]
    bundle = artifact["cluster_level_skill_bundles"]["s000_v001"]
    assert bundle["skill_hash"].startswith("sha256:")
    assert bundle["source_train_run_id"] == "train-run-1"
    assert bundle["source_split"] == "train"
    assert bundle["source_model"] == "fake-model"
    assert bundle["prompt_hash"] == "prompt-0"


def test_train_runner_reads_manifest_and_dispatches_each_cluster(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.manifest_path.write_text(
        json.dumps(
            {
                "version": "v2.4",
                "split": "train",
                "phase": "train_evolve",
                "seed": 42,
                "domains": {
                    "airline": [
                        {
                            "cluster_id": "airline_noop_00",
                            "issue_type": "noop",
                            "semantic_key": "noop",
                            "task_ids": ["noop-task"],
                            "size": 1,
                        },
                        {
                            "cluster_id": "airline_book_00",
                            "issue_type": "book",
                            "semantic_key": "book",
                            "task_ids": ["book-1", "book-2"],
                            "size": 2,
                        },
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    skills = {SkillKey(0, 1): _skill(0, "guide")}
    loaded_clusters: list[tuple[str, list[str]]] = []

    def task_loader(cluster):
        loaded_clusters.append((cluster.cluster_id, cluster.task_ids))
        return [{"id": task_id, "domain": cluster.domain} for task_id in cluster.task_ids]

    runner = SkillLiftTau2TrainRunner(
        task_loader=task_loader,
        run_single_task=lambda run_config, task, seed: FakeSimulation(str(task["id"]), 1.0),
    )

    result = runner.run(config, skills=skills, source_train_run_id="train-run-1")

    assert loaded_clusters == [
        ("airline_noop_00", ["noop-task"]),
        ("airline_book_00", ["book-1", "book-2"]),
    ]
    assert [cluster.cluster_id for cluster in result.clusters] == [
        "airline_noop_00",
        "airline_book_00",
    ]
    assert len(result.artifact_paths) == 2
    for path in result.artifact_paths:
        artifact = json.loads(path.read_text(encoding="utf-8"))
        assert artifact["skilllift_phase"] == "train_evolve"
        assert artifact["tau2_split"] == "train"


def _load_skill_injected_agent_module():
    path = TAU2_SRC / "tau2" / "agent" / "skill_injected_agent.py"
    spec = importlib.util.spec_from_file_location("skill_injected_agent_for_skilllift_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_llm_agent_module():
    class _SystemMessage:
        def __init__(self, content: str) -> None:
            self.content = content

    class _State:
        def __init__(self, content: str) -> None:
            self.system_messages = [_SystemMessage(content)]

    class _LLMAgent:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, tools, domain_policy, llm, llm_args=None) -> None:
            self.tools = tools
            self.domain_policy = domain_policy
            self.llm = llm
            self.llm_args = llm_args or {}

        def get_init_state(self):
            return _State(self.system_prompt)

    return types.SimpleNamespace(
        AGENT_INSTRUCTION="Use valid JSON.",
        LLMAgent=_LLMAgent,
        LLMAgentStateType=object,
    )
