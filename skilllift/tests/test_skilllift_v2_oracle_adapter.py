import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT / "WildClawBench"))

from skilllift.errors import OracleAdapterError
from skilllift.oracle import (
    build_oracle_cache_key,
    build_run_batch_command,
    classify_oracle_failure_stage,
    evaluate_skill_batch,
    inject_skill_for_oracle,
    load_oracle_score,
    preflight_validate_oracle_run,
    resolve_wildclaw_output_dir,
)
from skilllift.adapters import wildclawbench as wildclaw_adapter
from skilllift.persistence import ExperimentStore
from skilllift.persistence import write_json
from skilllift.schemas import SkillLiftConfig, EvoSkill, OracleFeedback, SkillKey, TaskSpec
from eval.run_batch import resolve_judge_env


def _task(path: Path) -> TaskSpec:
    path.write_text(
        "---\nid: demo_task\ncategory: Demo\ntimeout_seconds: 1\n---\n## Prompt\n\nDo it.\n",
        encoding="utf-8",
    )
    return TaskSpec("demo_task", "## Prompt\n\nDo it.\n", str(path))


def _skill(key: SkillKey = SkillKey(0, 1), name: str = "demo") -> EvoSkill:
    return EvoSkill(
        key,
        name,
        {
            "SKILL.md": "---\nname: demo\n---\nverify complete read-only deadline forbidden\n",
            "executor.py": "print('ok')\n",
        },
        "executor.py",
    )


def test_run_batch_command_contains_skill_dir(tmp_path) -> None:
    config = SkillLiftConfig(models_config="legacy.json", openclaw_models_config="models_config_openclaw.json")
    command = build_run_batch_command(tmp_path / "task.md", "model", config, tmp_path / "skill")
    assert "eval/run_batch.py" in command
    assert "--task" in command
    assert "--model" in command
    assert "--models-config" in command
    assert command[command.index("--models-config") + 1] == str(
        (Path(__file__).resolve().parent.parent / "models_config_openclaw.json").resolve()
    )
    assert "--rate-limit-retries" in command
    assert "--skill-dir" in command
    assert command[-1] == str(tmp_path / "skill")


def test_run_batch_command_can_target_external_wildclaw_root(tmp_path) -> None:
    external_root = tmp_path / "WildClawBench"
    (external_root / "eval").mkdir(parents=True)
    (external_root / "eval" / "run_batch.py").write_text("print('ok')\n", encoding="utf-8")
    models_config = tmp_path / "models.json"
    models_config.write_text('{"providers":{}}', encoding="utf-8")
    config = SkillLiftConfig(openclaw_models_config=str(models_config), wildclaw_root=str(external_root))
    command = build_run_batch_command(tmp_path / "task.md", "model", config, tmp_path / "skill")
    assert command[1] == "eval/run_batch.py"
    assert command[command.index("--models-config") + 1] == str(models_config)
    assert "--rate-limit-retries" in command
    assert "--skill-dir" in command


def test_run_batch_command_defaults_to_sibling_wildclaw_root(tmp_path, monkeypatch) -> None:
    agent_root = tmp_path / "skilllift"
    external_root = tmp_path / "WildClawBench"
    (agent_root / "skilllift" / "adapters").mkdir(parents=True)
    (external_root / "eval").mkdir(parents=True)
    (external_root / "eval" / "run_batch.py").write_text("print('ok')\n", encoding="utf-8")
    models_config = tmp_path / "models.json"
    models_config.write_text('{"providers":{}}', encoding="utf-8")
    monkeypatch.setattr(wildclaw_adapter, "_repo_root", lambda: agent_root)

    config = SkillLiftConfig(openclaw_models_config=str(models_config))
    command = build_run_batch_command(tmp_path / "task.md", "model", config, tmp_path / "skill")

    assert command[1] == "eval/run_batch.py"
    assert "--rate-limit-retries" in command


def test_resolve_judge_env_uses_openclaw_models_config() -> None:
    config = {
        "providers": {
            "zhipu": {
                "baseUrl": "https://example.test/v4",
                "apiKey": "token",
                "models": [{"id": "glm-5.1", "name": "zhipu/glm-5.1"}],
            }
        }
    }
    env = resolve_judge_env(config, "zhipu/glm-5.1")
    assert env["SKILLLIFT_JUDGE_BASE_URL"] == "https://example.test/v4"
    assert env["SKILLLIFT_JUDGE_API_KEY"] == "token"
    assert env["SKILLLIFT_JUDGE_MODEL"] == "glm-5.1"
    assert env["SKILLLIFT_JUDGE_DISPLAY_MODEL"] == "zhipu/glm-5.1"


def test_resolve_judge_env_uses_judge_module_model() -> None:
    config = {
        "moduleModels": {"judge": "provider/judge-model", "openclaw_agent": "provider/agent-model"},
        "providers": {
            "provider": {
                "baseUrl": "https://example.test/v4",
                "apiKey": "token",
                "models": [
                    {"id": "agent", "name": "provider/agent-model"},
                    {"id": "judge", "name": "provider/judge-model"},
                ],
            }
        },
    }
    env = resolve_judge_env(config, "provider/agent-model")
    assert env["SKILLLIFT_JUDGE_MODEL"] == "judge"
    assert env["SKILLLIFT_JUDGE_DISPLAY_MODEL"] == "provider/judge-model"


def test_inject_skill_matches_run_batch_skill_dir_contract(tmp_path) -> None:
    task = _task(tmp_path / "task.md")
    store = ExperimentStore(tmp_path / "exp")
    task_path, skill_dir = inject_skill_for_oracle(task, _skill(), store)
    assert task_path.exists()
    assert "__skilllift_s000_v001" in task_path.read_text(encoding="utf-8")
    assert (skill_dir / "SKILL.md").exists()
    assert skill_dir.parent.name == "s000_v001"
    assert skill_dir.name.startswith("s000_v001_")


def test_preflight_rejects_missing_skill_md_and_parent_dir(tmp_path) -> None:
    task = _task(tmp_path / "task.md")
    store = ExperimentStore(tmp_path / "exp")
    task_path, skill_dir = inject_skill_for_oracle(task, _skill(), store)
    config = SkillLiftConfig(models_config="models_config_openclaw.json")
    preflight_validate_oracle_run(task_path, skill_dir, config)
    with pytest.raises(OracleAdapterError):
        preflight_validate_oracle_run(task_path, skill_dir.parent, config)


def test_preflight_uses_openclaw_models_config(tmp_path) -> None:
    task = _task(tmp_path / "task.md")
    store = ExperimentStore(tmp_path / "exp")
    task_path, skill_dir = inject_skill_for_oracle(task, _skill(), store)
    openclaw_config = tmp_path / "openclaw.json"
    openclaw_config.write_text(
        json.dumps(
            {
                "providers": {
                    "provider": {
                        "baseUrl": "https://example.test/v4",
                        "apiKey": "token",
                        "models": [{"id": "agent", "name": "provider/agent"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config = SkillLiftConfig(models_config=str(tmp_path / "missing.json"), openclaw_models_config=str(openclaw_config))
    preflight_validate_oracle_run(task_path, skill_dir, config)


def test_resolve_output_ignores_stale_runs(tmp_path) -> None:
    task_path = tmp_path / "task.md"
    task = _task(task_path)
    store = ExperimentStore(tmp_path / "exp")
    rewritten, _ = inject_skill_for_oracle(task, _skill(), store)
    task_id = "demo_task__skilllift_s000_v001"
    stale = tmp_path / "out" / rewritten.parent.name / task_id / "old"
    fresh = tmp_path / "out" / rewritten.parent.name / task_id / "fresh"
    stale.mkdir(parents=True)
    old_time = time.time() - 100
    os.utime(stale, (old_time, old_time))
    start = time.time() - 1
    fresh.mkdir(parents=True)
    assert resolve_wildclaw_output_dir(rewritten, tmp_path / "out", start, SkillLiftConfig()) == fresh


def test_resolve_output_defaults_to_sibling_wildclaw_output(tmp_path, monkeypatch) -> None:
    agent_root = tmp_path / "skilllift"
    external_root = tmp_path / "WildClawBench"
    (agent_root / "skilllift" / "adapters").mkdir(parents=True)
    monkeypatch.setattr(wildclaw_adapter, "_repo_root", lambda: agent_root)
    task_path = tmp_path / "task.md"
    task = _task(task_path)
    store = ExperimentStore(tmp_path / "exp")
    rewritten, _ = inject_skill_for_oracle(task, _skill(), store)
    task_id = "demo_task__skilllift_s000_v001"
    fresh = external_root / "output" / rewritten.parent.name / task_id / "fresh"
    fresh.mkdir(parents=True)

    assert resolve_wildclaw_output_dir(rewritten, None, time.time() - 1, SkillLiftConfig()) == fresh


def test_load_oracle_score_missing_score_returns_feedback(tmp_path) -> None:
    out = tmp_path / "run"
    out.mkdir()
    score = load_oracle_score(out, SkillKey(0, 1), 0.8, 2)
    assert score.oracle_pass == 0
    assert score.feedback.metadata["failure_type"] == "grader_missing_score"


def test_real_batch_manifest_and_cache_with_mocked_run(tmp_path, monkeypatch) -> None:
    task = _task(tmp_path / "task.md")
    openclaw_config = tmp_path / "openclaw.json"
    framework_config = tmp_path / "framework.json"
    openclaw_config.write_text('{"providers":{"provider":{"baseUrl":"https://example.test","apiKey":"token","models":[{"id":"agent"}]}}}', encoding="utf-8")
    framework_config.write_text('{"providers":{"provider":{"baseUrl":"https://example.test","apiKey":"token","models":[{"id":"framework"}]}}}', encoding="utf-8")
    config = SkillLiftConfig(
        exp_root=str(tmp_path),
        exp_name="exp",
        output_root=str(tmp_path / "out"),
        models_config=str(framework_config),
        openclaw_models_config=str(openclaw_config),
        framework_models_config=str(framework_config),
        oracle_concurrency=2,
    )
    store = ExperimentStore(tmp_path / "exp")
    skills = {SkillKey(0, 1): _skill(SkillKey(0, 1), "a"), SkillKey(1, 1): _skill(SkillKey(1, 1), "b")}

    outputs: list[Path] = []

    def fake_run(command, config):
        skill_dir = Path(command[-1])
        token = skill_dir.parent.name
        output_dir = tmp_path / "out" / token
        output_dir.mkdir(parents=True, exist_ok=True)
        write_json(output_dir / "score.json", {"overall_score": 0.9})
        write_json(output_dir / "usage.json", {"elapsed_time": 0.01, "request_count": 1})
        (output_dir / "agent.log").write_text("", encoding="utf-8")
        (output_dir / "gateway.log").write_text("", encoding="utf-8")
        outputs.append(output_dir)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("skilllift.oracle.run_wildclawbench_task", fake_run)
    monkeypatch.setattr("skilllift.oracle.resolve_wildclaw_output_dir", lambda *args, **kwargs: outputs.pop(0))

    scores = evaluate_skill_batch(task, skills, config, store)
    assert set(scores) == set(skills)
    assert all(score.output_dir for score in scores.values())
    manifests = list((tmp_path / "exp" / "oracle_manifests").rglob("*oracle_run_manifest.json"))
    assert len(manifests) == 2
    task_ids = {json.loads(path.read_text())["task_id"] for path in manifests}
    skill_dirs = {json.loads(path.read_text())["skill_dir"] for path in manifests}
    manifest_payloads = [json.loads(path.read_text()) for path in manifests]
    assert len(task_ids) == 2
    assert len(skill_dirs) == 2
    assert all(payload["openclaw_models_config"] == str(openclaw_config) for payload in manifest_payloads)
    assert all(payload["framework_models_config"] == str(framework_config) for payload in manifest_payloads)
    assert all(payload["command"][payload["command"].index("--models-config") + 1] == str(openclaw_config) for payload in manifest_payloads)

    cache_key = build_oracle_cache_key(task, skills[SkillKey(0, 1)], config)
    cached_scores = evaluate_skill_batch(task, {SkillKey(0, 1): skills[SkillKey(0, 1)]}, config, store)
    assert cached_scores[SkillKey(0, 1)].feedback.metadata["cache_hit"] is True
    assert (tmp_path / "exp" / "oracle_cache" / f"{cache_key}.json").exists()


def test_failure_stage_classification() -> None:
    assert classify_oracle_failure_stage(0, None, OracleFeedback(), stdout="API rate limit reached") == "llm_rate_limited"
    assert classify_oracle_failure_stage(1, Path("/tmp/out"), OracleFeedback(metadata={"failure_type": "success"})) == "run_batch_process"
    assert classify_oracle_failure_stage(0, None, OracleFeedback()) == "output_resolution"
    assert classify_oracle_failure_stage(0, Path("/tmp/out"), OracleFeedback(metadata={"failure_type": "task_low_score"})) == "task_low_score"
    assert classify_oracle_failure_stage(0, Path("/tmp/out"), OracleFeedback(metadata={"failure_type": "success"})) == "success"
