from __future__ import annotations

import json
import sys
import types
import importlib.util
from pathlib import Path

import pytest

from skilllift_eval.schemas import LoadedSkillContext, SkillBundle
from skilllift_eval.skills.tau2_loader import Tau2SkillLoader, validate_tau2_context


ROOT = Path(__file__).resolve().parents[2]
TAU2_SRC = ROOT / "tau2-bench" / "src"


def static_bundle() -> SkillBundle:
    return SkillBundle(
        skill_bundle_id="tau2-canary-bundle",
        baseline="autoskill",
        benchmark_target="tau2",
        granularity="domain",
        domain="airline",
        skills=[
            {
                "id": "airline-canary",
                "title": "Airline Canary",
                "content": "CANARY_TAU2_SKILL_77: ask for booking reference before changing flights.",
                "metadata": {"level": "domain"},
            }
        ],
        created_at="2026-06-23T00:00:00Z",
    )


def test_tau2_loader_renders_stable_prompt_context_for_k_tasks(tmp_path: Path) -> None:
    bundle = static_bundle()
    loader = Tau2SkillLoader(artifact_root=tmp_path / "tau2")

    context = loader.prepare(
        bundle=bundle,
        domain="airline",
        task_ids=["airline-task-1", "airline-task-2", "airline-task-3"],
        domain_policy="Only use public airline policy text.",
    )
    repeat = Tau2SkillLoader(artifact_root=tmp_path / "tau2-repeat").prepare(
        bundle=bundle,
        domain="airline",
        task_ids=["airline-task-1", "airline-task-2", "airline-task-3"],
        domain_policy="Only use public airline policy text.",
    )

    assert context.kind == "tau2_prompt_context"
    assert context.skill_hash == bundle.skill_hash
    assert context.prompt_hash == repeat.prompt_hash
    assert context.skills_block_hash == repeat.skills_block_hash
    assert context.batch_size_k == 3
    assert context.batch_task_ids == ["airline-task-1", "airline-task-2", "airline-task-3"]

    payload = json.loads(Path(context.prompt_payload_path).read_text(encoding="utf-8"))
    assert "CANARY_TAU2_SKILL_77" in payload["prompt"]
    assert "Only use public airline policy text." in payload["prompt"]
    assert "<public_domain_policy>" in payload["prompt"]
    assert "<constraints>" in payload["prompt"]
    assert "Use only information visible" in payload["prompt"]
    assert "/root/skills" not in payload["prompt"]
    assert str(tmp_path) not in payload["prompt"]

    manifest = json.loads(Path(context.manifest_path).read_text(encoding="utf-8"))
    assert manifest["domain"] == "airline"
    assert manifest["batch_size_k"] == 3
    assert manifest["prompt_hash"] == context.prompt_hash
    assert manifest["skills_block_hash"] == context.skills_block_hash


def test_tau2_loader_rejects_banking_knowledge_and_forbidden_public_prompt_fields(tmp_path: Path) -> None:
    loader = Tau2SkillLoader(artifact_root=tmp_path)

    with pytest.raises(ValueError, match="banking_knowledge"):
        loader.prepare(
            bundle=static_bundle(),
            domain="banking_knowledge",
            task_ids=["task-1", "task-2", "task-3"],
            domain_policy="public policy",
        )

    # "evaluation_criteria" was removed from FORBIDDEN_PROMPT_PATTERNS (2026-07-05):
    # it caused false positives on LLM-generated skill text. The remaining patterns
    # target unambiguous oracle field names that should never appear in a prompt.
    bundle_with_oracle = static_bundle()
    bundle_with_oracle.skills[0]["content"] = "Follow the gold actions exactly."
    with pytest.raises(ValueError, match="gold actions"):
        loader.prepare(
            bundle=bundle_with_oracle,
            domain="airline",
            task_ids=["task-1"],
            domain_policy="public policy",
        )


def test_tau2_loader_allows_generated_skill_to_mention_forbidden_vocabulary(tmp_path: Path) -> None:
    bundle = static_bundle()
    bundle.skills[0]["content"] = "Do not rely on hidden evaluation criteria."

    context = Tau2SkillLoader(artifact_root=tmp_path).prepare(
        bundle=bundle,
        domain="airline",
        task_ids=["task-1"],
        domain_policy="public policy",
    )

    payload = json.loads(Path(context.prompt_payload_path).read_text(encoding="utf-8"))
    assert "evaluation criteria" in payload["prompt"]
    assert "evaluation criteria" in payload["skills_block"]
    assert "public policy" in payload["prompt"]
    assert "<constraints>" in payload["prompt"]


def test_tau2_context_validation_fails_fast_for_wildclaw_context() -> None:
    wild = LoadedSkillContext(
        kind="wildclaw_file_context",
        benchmark="wildclawbench",
        loader_type="wildclaw_task_skills",
        skill_hash="sha256:x",
        manifest_path="manifest.json",
    )

    with pytest.raises(ValueError, match="tau2_prompt_context"):
        validate_tau2_context(wild)


def test_tau2_python_api_fallback_plumbs_prompt_context() -> None:
    simulation_text = (TAU2_SRC / "tau2" / "data_model" / "simulation.py").read_text(encoding="utf-8")
    build_text = (TAU2_SRC / "tau2" / "runner" / "build.py").read_text(encoding="utf-8")

    assert "tau2_prompt_context" in simulation_text
    assert "Optional prompt context for skill_injected agent." in simulation_text
    assert "tau2_prompt_context: Optional[dict] = None" in build_text
    assert "tau2_prompt_context=config.tau2_prompt_context" in build_text


def test_skill_injected_agent_is_registered_and_uses_prompt_payload(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "tau2", types.ModuleType("tau2"))
    monkeypatch.setitem(sys.modules, "tau2.agent", types.ModuleType("tau2.agent"))
    monkeypatch.setitem(sys.modules, "tau2.agent.llm_agent", _fake_llm_agent_module())
    monkeypatch.setitem(sys.modules, "tau2.environment", types.ModuleType("tau2.environment"))
    monkeypatch.setitem(sys.modules, "tau2.environment.tool", types.SimpleNamespace(Tool=object))
    module = _load_skill_injected_agent_module()

    context = Tau2SkillLoader(artifact_root=tmp_path).prepare(
        bundle=static_bundle(),
        domain="airline",
        task_ids=["airline-task-1"],
        domain_policy="Public policy.",
    )

    registry_text = (TAU2_SRC / "tau2" / "registry.py").read_text(encoding="utf-8")
    assert 'registry.register_agent_factory(create_skill_injected_agent, "skill_injected")' in registry_text
    agent = module.create_skill_injected_agent(
        tools=[],
        domain_policy="Public policy.",
        llm="dummy-model",
        llm_args={},
        tau2_prompt_context=context.to_dict(),
    )
    state = agent.get_init_state()
    assert "CANARY_TAU2_SKILL_77" in state.system_messages[0].content

    with pytest.raises(ValueError, match="tau2_prompt_context"):
        module.create_skill_injected_agent(
            tools=[],
            domain_policy="Public policy.",
            llm="dummy-model",
            llm_args={},
            tau2_prompt_context={
                "kind": "wildclaw_file_context",
                "benchmark": "wildclawbench",
            },
        )


def _load_skill_injected_agent_module():
    path = TAU2_SRC / "tau2" / "agent" / "skill_injected_agent.py"
    spec = importlib.util.spec_from_file_location("skill_injected_agent_for_test", path)
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
