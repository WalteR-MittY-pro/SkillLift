from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

from skilllift.schemas import EvoSkill, SkillKey
from skilllift_eval.runners.skilllift_tau2 import SkillLiftTau2TrainConfig, run_train_evolve_smoke


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
            "evaluation_criteria": "must not leak",
            "gold_action": "must not leak",
            "target_db": "must not leak",
        }


class FakeTau2Task:
    def __init__(self, task_id: str) -> None:
        self.id = task_id


def _skill(slot: int, body: str) -> EvoSkill:
    return EvoSkill(
        SkillKey(slot, 1),
        f"guide-{slot}",
        {"SKILL.md": body},
        None,
        {"prompt_hash": f"prompt-{slot}", "source_model": "fake-model"},
    )


def test_train_evolve_smoke_writes_artifacts_and_uses_skill_injected_context(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "tau2", types.ModuleType("tau2"))
    monkeypatch.setitem(sys.modules, "tau2.agent", types.ModuleType("tau2.agent"))
    monkeypatch.setitem(sys.modules, "tau2.agent.llm_agent", _fake_llm_agent_module())
    monkeypatch.setitem(sys.modules, "tau2.environment", types.ModuleType("tau2.environment"))
    monkeypatch.setitem(sys.modules, "tau2.environment.tool", types.SimpleNamespace(Tool=object))
    skill_injected_module = _load_skill_injected_agent_module()

    manifest_path = tmp_path / "tau2_train_cluster_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": "v2.4",
                "split": "train",
                "phase": "train_evolve",
                "seed": 42,
                "domains": {
                    "telecom": [
                        {
                            "cluster_id": "telecom_billing_00",
                            "issue_type": "billing",
                            "semantic_key": "billing",
                            "task_ids": ["telecom-task-1", "telecom-task-2"],
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
    skills = {
        SkillKey(0, 1): _skill(0, "CANARY_SMOKE_SEED: verify account before action."),
        SkillKey(1, 1): _skill(1, "CANARY_SMOKE_VARIANT: confirm resolution after action."),
    }
    rewards = {
        ("telecom-task-1", "s000_v001"): 0.0,
        ("telecom-task-1", "s001_v001"): 1.0,
        ("telecom-task-2", "s000_v001"): 1.0,
        ("telecom-task-2", "s001_v001"): 1.0,
    }
    calls: list[dict[str, object]] = []

    def task_loader(cluster):
        return [FakeTau2Task(task_id) for task_id in cluster.task_ids]

    def fake_run_single_task(run_config, task, seed):
        calls.append(run_config)
        prompt_payload = json.loads(
            Path(run_config["tau2_prompt_context"]["prompt_payload_path"]).read_text(encoding="utf-8")
        )
        assert "CANARY_SMOKE" in prompt_payload["prompt"]
        assert "Only use public telecom policy." in prompt_payload["prompt"]
        agent = skill_injected_module.create_skill_injected_agent(
            tools=[],
            domain_policy="tau2 base policy",
            llm="dummy-model",
            llm_args={},
            tau2_prompt_context=run_config["tau2_prompt_context"],
        )
        system_prompt = agent.get_init_state().system_messages[0].content
        assert "CANARY_SMOKE" in system_prompt
        assert "Only use public telecom policy." in system_prompt
        return FakeSimulation(task.id, rewards[(task.id, run_config["skilllift_skill_key"])])

    result = run_train_evolve_smoke(
        config,
        skills=skills,
        task_loader=task_loader,
        run_single_task=fake_run_single_task,
        source_train_run_id="train-smoke-1",
        smoke_id="smoke-min",
    )

    assert len(calls) == 4
    assert all(call["agent"] == "skill_injected" for call in calls)
    assert all(call["tau2_prompt_context"]["kind"] == "tau2_prompt_context" for call in calls)
    assert result.artifact_path.exists()

    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert artifact["skilllift_phase"] == "train_evolve"
    assert artifact["tau2_split"] == "train"
    assert artifact["smoke_scope"] == "smoke-min"
    assert artifact["cluster_id"] == "telecom_billing_00"
    assert artifact["task_ids"] == ["telecom-task-1", "telecom-task-2"]
    assert artifact["actual_task_count"] == 2
    assert artifact["actual_skill_count"] == 2
    assert len(artifact["per_task_rankings"]) == 2
    assert artifact["rubricator_input"]["per_task_rankings"]
    assert artifact["round_metrics"]["oracle_calls_used"] == 4
    assert artifact["skill_artifacts"]["s000_v001"]["files"] == ["SKILL.md"]
    assert artifact["skill_artifacts"]["s000_v001"]["entrypoint"] is None
    assert artifact["skill_artifacts"]["s000_v001"]["skill_hash"].startswith("sha256:")
    assert artifact["skill_artifacts"]["s000_v001"]["prompt_hash"] == "prompt-0"

    serialized = json.dumps(artifact, ensure_ascii=False)
    assert "evaluation_criteria" not in serialized
    assert "gold_action" not in serialized
    assert "target_db" not in serialized
    assert "messages" not in serialized
    assert "transcript" not in serialized


def test_train_evolve_smoke_metrics_use_actual_loaded_tasks(tmp_path: Path) -> None:
    manifest_path = tmp_path / "tau2_train_cluster_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": "v2.4",
                "split": "train",
                "phase": "train_evolve",
                "seed": 42,
                "domains": {
                    "telecom": [
                        {
                            "cluster_id": "telecom_billing_00",
                            "issue_type": "billing",
                            "semantic_key": "billing",
                            "task_ids": ["telecom-task-1", "telecom-task-2"],
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
    skills = {
        SkillKey(0, 1): _skill(0, "Seed guide."),
        SkillKey(1, 1): _skill(1, "Variant guide."),
    }
    calls: list[dict[str, object]] = []

    def task_loader(cluster):
        return [FakeTau2Task(cluster.task_ids[0])]

    def fake_run_single_task(run_config, task, seed):
        calls.append(run_config)
        return FakeSimulation(task.id, 1.0)

    result = run_train_evolve_smoke(
        config,
        skills=skills,
        task_loader=task_loader,
        run_single_task=fake_run_single_task,
        source_train_run_id="train-smoke-1",
        smoke_id="smoke-min",
    )

    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert len(calls) == 2
    assert artifact["task_ids"] == ["telecom-task-1", "telecom-task-2"]
    assert artifact["actual_task_count"] == 1
    assert len(artifact["per_task_rankings"]) == 1
    assert artifact["round_metrics"]["oracle_calls_used"] == 2


def test_tau2_upstream_run_single_task_consumes_skill_injected_prompt_context(tmp_path: Path) -> None:
    tau2_python = ROOT / "tau2-bench" / ".venv" / "bin" / "python"
    if not tau2_python.exists():
        raise AssertionError("tau2-bench .venv is required for upstream run_single_task smoke")

    prompt_payload_path = tmp_path / "tau2_prompt_payload.json"
    prompt_payload_path.write_text(
        json.dumps({"prompt": "CANARY_SMOKE_RUN_SINGLE\nOnly use public telecom policy."}),
        encoding="utf-8",
    )
    script = f"""
import json
from tau2.agent import llm_agent as agent_mod
from tau2.data_model.simulation import TextRunConfig
from tau2.data_model.message import AssistantMessage
from tau2.run import get_tasks
from tau2.run import run_single_task
from tau2.user import user_simulator as user_mod

seen = {{"agent_prompt": False, "agent_calls": 0, "user_calls": 0}}

def fake_generate(model, tools=None, messages=None, call_name=None, **kwargs):
    text = "\\n".join(str(getattr(message, "content", "") or "") for message in (messages or []))
    if call_name == "agent_response":
        seen["agent_calls"] += 1
        seen["agent_prompt"] = (
            "CANARY_SMOKE_RUN_SINGLE" in text
            and "Only use public telecom policy." in text
        )
        return AssistantMessage.text("Please try sending MMS now.")
    if call_name == "user_simulator_response":
        seen["user_calls"] += 1
        if seen["user_calls"] == 1:
            return AssistantMessage.text("I cannot send MMS.")
        return AssistantMessage.text("###STOP###")
    return AssistantMessage.text("###STOP###")

agent_mod.generate = fake_generate
user_mod.generate = fake_generate

task_id = "[mms_issue]airplane_mode_on|bad_network_preference|bad_wifi_calling|break_apn_mms_setting|break_app_both_permissions|data_mode_off|data_usage_exceeded|unseat_sim_card|user_abroad_roaming_disabled_off[PERSONA:Hard]"
config = TextRunConfig(
    domain="telecom",
    task_split_name="train",
    agent="skill_injected",
    user="user_simulator",
    llm_agent="fake-model",
    llm_user="fake-model",
    llm_args_agent={{}},
    llm_args_user={{}},
    max_steps=3,
    seed=42,
    tau2_prompt_context={{
        "kind": "tau2_prompt_context",
        "prompt_payload_path": {str(prompt_payload_path)!r},
    }},
)
task = get_tasks("telecom", task_ids=[task_id])[0]
simulation = run_single_task(config, task, seed=42)
assert seen["agent_calls"] == 1
assert seen["agent_prompt"] is True
print(json.dumps({{
    "agent_calls": seen["agent_calls"],
    "has_skill": seen["agent_prompt"],
    "reward": simulation.reward_info.reward if simulation.reward_info else None,
    "termination_reason": str(simulation.termination_reason),
}}))
"""
    completed = subprocess.run(
        [str(tau2_python), "-c", script],
        cwd=ROOT,
        env={"PYTHONPATH": str(TAU2_SRC)},
        text=True,
        capture_output=True,
        check=True,
    )
    assert '"agent_calls": 1' in completed.stdout
    assert '"has_skill": true' in completed.stdout
    assert '"reward": 0.0' in completed.stdout


def _load_skill_injected_agent_module():
    path = TAU2_SRC / "tau2" / "agent" / "skill_injected_agent.py"
    spec = importlib.util.spec_from_file_location("skill_injected_agent_for_smoke_test", path)
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
