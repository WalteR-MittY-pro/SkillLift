from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

from skilllift_eval.runners.skilllift_tau2 import (
    SkillLiftTau2TrainConfig,
    run_heldout_test_eval,
)
from skilllift_eval.schemas import SkillBundle, stable_hash


ROOT = Path(__file__).resolve().parents[2]
TAU2_SRC = ROOT / "tau2-bench" / "src"


class FakeRewardInfo:
    def __init__(self, reward: float) -> None:
        self.reward = reward

    def model_dump(self, mode: str = "json") -> dict[str, float]:
        return {"reward": self.reward}


class FakeSimulation:
    def __init__(self, task_id: str, reward: float) -> None:
        self.task_id = task_id
        self.reward_info = FakeRewardInfo(reward)

    def model_dump(self, mode: str = "json") -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "reward_info": self.reward_info.model_dump(mode=mode),
            "messages": ["must not be persisted"],
            "transcript": "must not be persisted",
            "evaluation_criteria": "must not leak",
            "gold_action": "must not leak",
            "target_db": "must not leak",
        }


class FakeTau2Task:
    def __init__(self, task_id: str) -> None:
        self.id = task_id


def test_heldout_test_eval_loads_master_skill_and_writes_isolated_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setitem(sys.modules, "tau2", types.ModuleType("tau2"))
    monkeypatch.setitem(sys.modules, "tau2.agent", types.ModuleType("tau2.agent"))
    monkeypatch.setitem(sys.modules, "tau2.agent.llm_agent", _fake_llm_agent_module())
    monkeypatch.setitem(sys.modules, "tau2.environment", types.ModuleType("tau2.environment"))
    monkeypatch.setitem(sys.modules, "tau2.environment.tool", types.SimpleNamespace(Tool=object))
    skill_injected_module = _load_skill_injected_agent_module()

    manifest_path = tmp_path / "tau2_test_eval_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": "v2.4",
                "split": "test",
                "phase": "test_eval",
                "seed": 42,
                "domains": {
                    "telecom": [
                        {
                            "cluster_id": "telecom_billing_test_00",
                            "issue_type": "billing",
                            "semantic_key": "billing",
                            "task_ids": ["telecom-test-1", "telecom-test-2"],
                            "size": 2,
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    source_fusion_artifact_path = _write_fusion_artifact(tmp_path)
    config = SkillLiftTau2TrainConfig(
        run_root=tmp_path / "run",
        manifest_path=manifest_path,
        model="fake-model",
        oracle_threshold=0.5,
        seed=42,
        domain_policy_by_domain={"telecom": "Only use public telecom policy."},
    )
    calls: list[dict[str, object]] = []

    def task_loader(cluster):
        return [FakeTau2Task(task_id) for task_id in cluster.task_ids]

    def fake_run_single_task(run_config, task, seed):
        calls.append(run_config)
        assert seed == 42
        assert run_config["agent"] == "skill_injected"
        assert run_config["task_split_name"] == "test"
        assert run_config["skilllift_phase"] == "test_eval"
        assert isinstance(run_config["tau2_prompt_context"], dict)
        assert run_config["tau2_prompt_context"]["kind"] == "tau2_prompt_context"

        payload = json.loads(
            Path(run_config["tau2_prompt_context"]["prompt_payload_path"]).read_text(encoding="utf-8")
        )
        assert "CANARY_MASTER_SKILL" in payload["prompt"]
        assert "Only use public telecom policy." in payload["prompt"]
        assert "cluster seed skill that must not be loaded" not in payload["prompt"]
        for forbidden in ["evaluation_criteria", "gold_action", "target_db", "messages", "transcript"]:
            assert forbidden not in json.dumps(payload, ensure_ascii=False)

        agent = skill_injected_module.create_skill_injected_agent(
            tools=[],
            domain_policy="tau2 base policy",
            llm="dummy-model",
            llm_args={},
            tau2_prompt_context=run_config["tau2_prompt_context"],
        )
        system_prompt = agent.get_init_state().system_messages[0].content
        assert "CANARY_MASTER_SKILL" in system_prompt
        return FakeSimulation(task.id, 1.0 if task.id == "telecom-test-1" else 0.0)

    result = run_heldout_test_eval(
        config,
        source_fusion_artifact_path=source_fusion_artifact_path,
        task_loader=task_loader,
        run_single_task=fake_run_single_task,
        no_skill_test_score=0.25,
        eval_id="smoke-min",
    )

    assert len(calls) == 2
    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert artifact["skilllift_phase"] == "test_eval"
    assert artifact["tau2_split"] == "test"
    assert artifact["split_role"] == "heldout_test"
    assert artifact["domain"] == "telecom"
    assert artifact["cluster_id"] == "telecom_billing_test_00"
    assert artifact["task_ids"] == ["telecom-test-1", "telecom-test-2"]
    assert artifact["master_skill_path"] == str(result.master_skill_path.resolve())
    assert artifact["best_skill_bundle_path"] == str(result.best_skill_bundle_path.resolve())
    assert artifact["source_fusion_artifact_path"] == str(source_fusion_artifact_path.resolve())
    assert artifact["skill_artifact_hash"] == artifact["master_skill_hash"]
    assert artifact["master_skill_hash"].startswith("sha256:")
    assert artifact["fusion_prompt_hash"] == "sha256:fusion-prompt"
    assert artifact["skill_artifact_source_run_id"] == "fusion-smoke-1"
    assert artifact["heldout_test_score"] == 0.5
    assert artifact["skilllift_loaded_skill_test_score"] == 0.5
    assert artifact["no_skill_test_score"] == 0.25
    assert artifact["heldout_test_score_delta"] == 0.25
    assert artifact["per_task_scores"] == [
        {
            "task_id": "telecom-test-1",
            "reward": 1.0,
            "pass": 1,
            "summary": "reward=1.000 pass=1",
        },
        {
            "task_id": "telecom-test-2",
            "reward": 0.0,
            "pass": 0,
            "summary": "reward=0.000 pass=0",
        },
    ]

    serialized = json.dumps(artifact, ensure_ascii=False)
    for forbidden in [
        "evaluation_criteria",
        "gold_action",
        "target_db",
        "messages",
        "transcript",
        "receipt_revision",
        "rubricator_input",
        "mode_a",
        "mode_b",
    ]:
        assert forbidden not in serialized


def test_heldout_test_eval_rejects_non_test_eval_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "tau2_train_cluster_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": "v2.4",
                "split": "train",
                "phase": "train_evolve",
                "domains": {"telecom": []},
            }
        ),
        encoding="utf-8",
    )

    try:
        run_heldout_test_eval(
            SkillLiftTau2TrainConfig(
                run_root=tmp_path / "run",
                manifest_path=manifest_path,
                model="fake-model",
                domain_policy_by_domain={"telecom": "Only use public telecom policy."},
            ),
            source_fusion_artifact_path=_write_fusion_artifact(tmp_path),
            task_loader=lambda cluster: [],
            run_single_task=lambda run_config, task, seed: FakeSimulation("", 0.0),
        )
    except ValueError as exc:
        assert "split=test and phase=test_eval" in str(exc)
    else:
        raise AssertionError("test_eval must reject train_evolve manifests")


def test_heldout_test_eval_reuses_existing_task_score_files(tmp_path: Path) -> None:
    manifest_path = tmp_path / "tau2_test_eval_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": "v2.4",
                "split": "test",
                "phase": "test_eval",
                "seed": 42,
                "domains": {
                    "telecom": [
                        {
                            "cluster_id": "telecom_billing_test_00",
                            "issue_type": "billing",
                            "semantic_key": "billing",
                            "task_ids": ["telecom-test-1", "telecom-test-2"],
                            "size": 2,
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = SkillLiftTau2TrainConfig(
        run_root=tmp_path / "run",
        manifest_path=manifest_path,
        model="fake-model",
        oracle_threshold=0.5,
        seed=42,
        domain_policy_by_domain={"telecom": "Only use public telecom policy."},
    )
    existing_score_path = (
        config.run_root
        / "test_eval"
        / "telecom"
        / "telecom_billing_test_00"
        / "task_scores"
        / "telecom-test-1.json"
    )
    existing_score_path.parent.mkdir(parents=True)
    existing_score_path.write_text(
        json.dumps(
            {
                "task_id": "telecom-test-1",
                "reward": 1.0,
                "pass": 1,
                "summary": "reward=1.000 pass=1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    calls: list[str] = []

    def task_loader(cluster):
        return [FakeTau2Task(task_id) for task_id in cluster.task_ids]

    def fake_run_single_task(run_config, task, seed):
        calls.append(task.id)
        return FakeSimulation(task.id, 0.0)

    result = run_heldout_test_eval(
        config,
        source_fusion_artifact_path=_write_fusion_artifact(tmp_path),
        task_loader=task_loader,
        run_single_task=fake_run_single_task,
        resume_enabled=True,
    )

    assert calls == ["telecom-test-2"]
    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert artifact["per_task_scores"] == [
        {
            "task_id": "telecom-test-1",
            "reward": 1.0,
            "pass": 1,
            "summary": "reward=1.000 pass=1",
        },
        {
            "task_id": "telecom-test-2",
            "reward": 0.0,
            "pass": 0,
            "summary": "reward=0.000 pass=0",
        },
    ]
    new_score_path = existing_score_path.with_name("telecom-test-2.json")
    assert json.loads(new_score_path.read_text(encoding="utf-8")) == {
        "task_id": "telecom-test-2",
        "reward": 0.0,
        "pass": 0,
        "summary": "reward=0.000 pass=0",
    }


def _write_fusion_artifact(tmp_path: Path) -> Path:
    artifact_dir = tmp_path / "fusion"
    artifact_dir.mkdir()
    master_skill = "# MASTER_SKILL\n\n## verify_account\n- CANARY_MASTER_SKILL: verify before action.\n"
    master_skill_path = artifact_dir / "MASTER_SKILL.md"
    master_skill_path.write_text(master_skill, encoding="utf-8")
    master_skill_hash = stable_hash({"SKILL.md": master_skill})
    bundle = SkillBundle(
        skill_bundle_id="telecom_MASTER_SKILL",
        baseline="skilllift",
        benchmark_target="tau2",
        granularity="domain",
        domain="telecom",
        source_round="train_evolve_fusion",
        source_artifact_path=str(artifact_dir.resolve()),
        skills=[
            {
                "id": "MASTER_SKILL",
                "title": "MASTER_SKILL",
                "content": master_skill,
                "metadata": {"fusion_prompt_template_id": "skilllift_tau2_master_skill_fusion_v1"},
            }
        ],
        created_at="1970-01-01T00:00:00Z",
        skill_hash=master_skill_hash,
    )
    best_skill_bundle_path = artifact_dir / "best_skill_bundle.json"
    best_skill_bundle_path.write_text(
        json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    fusion_artifact_path = artifact_dir / "fusion_artifact.json"
    fusion_artifact_path.write_text(
        json.dumps(
            {
                "skilllift_phase": "train_evolve",
                "tau2_split": "train",
                "domain": "telecom",
                "fusion_prompt_hash": "sha256:fusion-prompt",
                "output_skill_hash": master_skill_hash,
                "master_skill_path": str(master_skill_path.resolve()),
                "best_skill_bundle_path": str(best_skill_bundle_path.resolve()),
                "source_train_run_id": "fusion-smoke-1",
                "cluster_artifact_paths": [
                    str((tmp_path / "missing_cluster_artifact_must_not_be_loaded.json").resolve())
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return fusion_artifact_path


def _load_skill_injected_agent_module():
    path = TAU2_SRC / "tau2" / "agent" / "skill_injected_agent.py"
    spec = importlib.util.spec_from_file_location("skill_injected_agent_for_test_eval_test", path)
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
