from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TAU2_SRC = ROOT / "tau2-bench" / "src"


def test_skilllift_tau2_real_run_single_task_chain_smoke(tmp_path: Path) -> None:
    tau2_python = ROOT / "tau2-bench" / ".venv" / "bin" / "python"
    if not tau2_python.exists():
        raise AssertionError("tau2-bench .venv is required for real-chain smoke")

    script = f"""
from __future__ import annotations

import json
from pathlib import Path

from skilllift.schemas import EvoSkill, SkillKey
from tau2.agent import llm_agent as agent_mod
from tau2.data_model.message import AssistantMessage
from tau2.run import get_tasks
from tau2.user import user_simulator as user_mod
from skilllift_eval.runners.skilllift_tau2 import (
    SkillLiftTau2TrainConfig,
    _default_tau2_run_single_task,
    evaluate_tau2_skill_batch,
    load_train_clusters,
    run_heldout_test_eval,
    run_master_skill_fusion_smoke,
    write_train_cluster_artifact,
)

root = Path({str(tmp_path)!r})
train_task = get_tasks("telecom", task_split_name="train", num_tasks=1)[0]
test_task = get_tasks("telecom", task_split_name="test", num_tasks=1)[0]
assert train_task.id != test_task.id

seen = {{
    "agent_calls": 0,
    "train_skill_prompt_hits": 0,
    "master_skill_prompt_hits": 0,
    "train_payload_paths": [],
    "test_payload_paths": [],
}}


def fake_generate(model, tools=None, messages=None, call_name=None, **kwargs):
    text = "\\n".join(str(getattr(message, "content", "") or "") for message in (messages or []))
    if call_name == "agent_response":
        seen["agent_calls"] += 1
        if "CANARY_REAL_CHAIN_SEED" in text or "CANARY_REAL_CHAIN_VARIANT" in text:
            seen["train_skill_prompt_hits"] += 1
        if "CANARY_REAL_CHAIN_MASTER" in text:
            seen["master_skill_prompt_hits"] += 1
        return AssistantMessage.text("I can help with that. Please try the relevant telecom action now.")
    if call_name == "user_simulator_response":
        return AssistantMessage.text("I need help with my telecom service.")
    return AssistantMessage.text("###STOP###")


agent_mod.generate = fake_generate
user_mod.generate = fake_generate


def write_manifest(path: Path, *, split: str, phase: str, cluster_id: str, task_id: str) -> None:
    path.write_text(
        json.dumps(
            {{
                "version": "v2.4",
                "split": split,
                "phase": phase,
                "seed": 42,
                "domains": {{
                    "telecom": [
                        {{
                            "cluster_id": cluster_id,
                            "issue_type": "real_chain",
                            "semantic_key": "real_chain",
                            "task_ids": [task_id],
                            "size": 1,
                        }}
                    ]
                }},
            }},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\\n",
        encoding="utf-8",
    )


train_manifest = root / "tau2_train_cluster_manifest.json"
test_manifest = root / "tau2_test_eval_manifest.json"
write_manifest(
    train_manifest,
    split="train",
    phase="train_evolve",
    cluster_id="telecom_real_train_00",
    task_id=train_task.id,
)
write_manifest(
    test_manifest,
    split="test",
    phase="test_eval",
    cluster_id="telecom_real_test_00",
    task_id=test_task.id,
)

train_config = SkillLiftTau2TrainConfig(
    run_root=root / "run",
    manifest_path=train_manifest,
    model="fake-model",
    oracle_threshold=0.5,
    seed=42,
    max_steps=3,
    domain_policy_by_domain={{"telecom": "Only use public telecom policy."}},
)
skills = {{
    SkillKey(0, 1): EvoSkill(
        SkillKey(0, 1),
        "seed",
        {{"SKILL.md": "# Seed\\n\\n- CANARY_REAL_CHAIN_SEED: verify visible telecom state before action."}},
        None,
        {{"prompt_hash": "prompt-seed", "source_model": "fake-model"}},
    ),
    SkillKey(1, 1): EvoSkill(
        SkillKey(1, 1),
        "variant",
        {{"SKILL.md": "# Variant\\n\\n- CANARY_REAL_CHAIN_VARIANT: check results after every telecom action."}},
        None,
        {{"prompt_hash": "prompt-variant", "source_model": "fake-model"}},
    ),
}}


def train_loader(cluster):
    return get_tasks(cluster.domain, task_split_name="train", task_ids=cluster.task_ids)


def test_loader(cluster):
    return get_tasks(cluster.domain, task_split_name="test", task_ids=cluster.task_ids)


def real_runner(run_config, task, seed):
    assert run_config["agent"] == "skill_injected"
    assert isinstance(run_config["tau2_prompt_context"], dict)
    assert run_config["tau2_prompt_context"]["kind"] == "tau2_prompt_context"
    payload_path = Path(run_config["tau2_prompt_context"]["prompt_payload_path"])
    assert payload_path.is_file()
    payload_text = payload_path.read_text(encoding="utf-8")
    for forbidden in ["evaluation_criteria", "gold_action", "target_db", "messages", "transcript"]:
        assert forbidden not in payload_text
    if run_config["skilllift_phase"] == "train_evolve":
        assert run_config["task_split_name"] == "train"
        assert "CANARY_REAL_CHAIN_SEED" in payload_text or "CANARY_REAL_CHAIN_VARIANT" in payload_text
        seen["train_payload_paths"].append(str(payload_path))
    elif run_config["skilllift_phase"] == "test_eval":
        assert run_config["task_split_name"] == "test"
        assert "CANARY_REAL_CHAIN_MASTER" in payload_text
        seen["test_payload_paths"].append(str(payload_path))
    else:
        raise AssertionError(run_config["skilllift_phase"])
    return _default_tau2_run_single_task(run_config, task, seed)


train_cluster = load_train_clusters(train_manifest)[0]
train_batch_result = evaluate_tau2_skill_batch(
    train_loader(train_cluster),
    skills,
    train_config,
    domain=train_cluster.domain,
    run_single_task=real_runner,
)
train_artifact_path = write_train_cluster_artifact(
    config=train_config,
    cluster_id=train_cluster.cluster_id,
    domain=train_cluster.domain,
    task_ids=train_cluster.task_ids,
    skills=skills,
    batch_result=train_batch_result,
    source_train_run_id="real-chain-train-smoke",
)
train_artifact = json.loads(train_artifact_path.read_text(encoding="utf-8"))
assert train_artifact["skilllift_phase"] == "train_evolve"
assert train_artifact["tau2_split"] == "train"
assert train_artifact["cluster_id"] == "telecom_real_train_00"
assert train_artifact["cluster_level_skill_bundles"]["s000_v001"]["files"] == ["SKILL.md"]
assert train_artifact["cluster_level_skill_contents"]["s000_v001"]["entrypoint"] is None


def fake_fusion_llm(prompt: str, model: str) -> str:
    assert "CANARY_REAL_CHAIN_SEED" in prompt
    assert "CANARY_REAL_CHAIN_VARIANT" in prompt
    return "\\n".join(
        [
            "`````",
            "# MASTER_SKILL",
            "",
            "## Action Checkpoint Index",
            "- verify_account",
            "",
            "## verify_account",
            "- CANARY_REAL_CHAIN_MASTER: verify visible telecom state before and after action.",
            "`````",
        ]
    )


fusion_result = run_master_skill_fusion_smoke(
    train_config,
    cluster_artifact_paths=[train_artifact_path],
    domain="telecom",
    action_types=["verify_account"],
    fusion_model="fake-fusion-model",
    fusion_llm=fake_fusion_llm,
    repair_metrics={{"parse_error_count": 0, "total_generation_attempts": 1}},
)
assert fusion_result.master_skill_path.read_text(encoding="utf-8").startswith("# MASTER_SKILL")

test_config = SkillLiftTau2TrainConfig(
    run_root=root / "run",
    manifest_path=test_manifest,
    model="fake-model",
    oracle_threshold=0.5,
    seed=42,
    max_steps=3,
    domain_policy_by_domain={{"telecom": "Only use public telecom policy."}},
)
test_result = run_heldout_test_eval(
    test_config,
    source_fusion_artifact_path=fusion_result.artifact_path,
    task_loader=test_loader,
    run_single_task=real_runner,
    no_skill_test_score=None,
    eval_id="real-chain-smoke",
)
test_artifact = json.loads(test_result.artifact_path.read_text(encoding="utf-8"))
assert test_artifact["skilllift_phase"] == "test_eval"
assert test_artifact["tau2_split"] == "test"
assert test_artifact["task_ids"] == [test_task.id]
assert test_artifact["loaded_skill_artifact"] == {{
    "entrypoint": None,
    "files": ["SKILL.md"],
    "granularity": "domain",
    "has_python": False,
}}
assert test_artifact["no_skill_test_score"] is None
assert test_artifact["heldout_test_score_delta"] is None
assert train_task.id not in test_artifact["task_ids"]

serialized_test_artifact = json.dumps(test_artifact, ensure_ascii=False)
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
    assert forbidden not in serialized_test_artifact

assert len(seen["train_payload_paths"]) == 2
assert len(seen["test_payload_paths"]) == 1
assert seen["train_skill_prompt_hits"] >= 2
assert seen["master_skill_prompt_hits"] >= 1

print(
    "REAL_CHAIN_RESULT "
    + json.dumps(
        {{
            "train_task_id": train_task.id,
            "test_task_id": test_task.id,
            "agent_calls": seen["agent_calls"],
            "train_skill_prompt_hits": seen["train_skill_prompt_hits"],
            "master_skill_prompt_hits": seen["master_skill_prompt_hits"],
            "train_artifact_path": str(train_artifact_path),
            "fusion_artifact_path": str(fusion_result.artifact_path),
            "test_artifact_path": str(test_result.artifact_path),
            "heldout_test_score": test_artifact["heldout_test_score"],
        }},
        ensure_ascii=False,
        sort_keys=True,
    )
)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), str(ROOT / "skilllift"), str(TAU2_SRC)]
    )
    completed = subprocess.run(
        [str(tau2_python), "-c", script],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result_line = next(
        line for line in completed.stdout.splitlines() if line.startswith("REAL_CHAIN_RESULT ")
    )
    result = json.loads(result_line.removeprefix("REAL_CHAIN_RESULT "))

    assert result["train_task_id"] != result["test_task_id"]
    assert result["agent_calls"] >= 3
    assert result["train_skill_prompt_hits"] >= 2
    assert result["master_skill_prompt_hits"] >= 1
    assert Path(result["train_artifact_path"]).exists()
    assert Path(result["fusion_artifact_path"]).exists()
    assert Path(result["test_artifact_path"]).exists()
