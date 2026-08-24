from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

from skilllift_eval.runners.skilllift_skillsbench import SkillsBenchSubprocessAdapter
from skilllift_eval.runners.skillsbench_adapter import (
    SkillsBenchCommand,
    extract_oracle_cell,
    extract_usage_record,
    find_exact_result_json,
    find_result_json,
    provider_auth_env,
    register_benchflow_prebuilt_cleanup,
)

ROOT = Path(__file__).resolve().parents[2]
SKILLSBENCH_PYTHON = ROOT / "skillsbench" / ".venv" / "bin" / "python"


def _result(task_id: str = "task-a") -> dict:
    return {
        "task_name": task_id,
        "rewards": {"reward": 0.75, "detail": 1.0},
        "n_tool_calls": 10,
        "n_skill_invocations": 1,
        "agent_result": {
            "n_input_tokens": 0,
            "n_output_tokens": 0,
            "n_cache_read_tokens": 0,
            "n_cache_creation_tokens": 0,
            "total_tokens": 123,
            "cost_usd": 0.5,
            "usage_source": "provider_response",
        },
        "final_metrics": {"total_cost_usd": 0.5},
        "usage_tracking": {"status": "enabled", "usage_source": "provider_response"},
        "error": None,
        "error_category": None,
        "verifier_error": None,
        "verifier_error_category": None,
        "partial_trajectory": False,
    }


def test_extract_oracle_cell_uses_only_whitelisted_fields(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text(json.dumps(_result()), encoding="utf-8")

    cell = extract_oracle_cell(path, "task-a", "s000_v001")

    assert cell == {
        "task_id": "task-a",
        "skill_id": "s000_v001",
        "oracle_result": {
            "rewards": {"reward": 0.75, "detail": 1.0},
            "n_tool_calls": 10,
            "n_skill_invocations": 1,
        },
    }
    assert "agent_result" not in str(cell)


def test_extract_oracle_cell_rejects_task_name_mismatch(tmp_path: Path) -> None:
    payload = _result("wrong-task")
    path = tmp_path / "result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid SkillsBench result"):
        extract_oracle_cell(path, "task-a", "s000_v001")


def test_extract_oracle_cell_accepts_timeout_with_valid_reward(tmp_path: Path) -> None:
    # The reward is verifier-measured, so a wall-clock timeout that cut the run
    # short (with a partial trajectory) must not discard an otherwise-valid result.
    payload = _result("task-a")
    payload["error"] = "Agent prompt exceeded wall-clock budget 300s"
    payload["error_category"] = "timeout"
    payload["agent_timeout_info"] = {
        "reason": "wall_clock_timeout",
        "terminal_trajectory_complete": False,
    }
    payload["partial_trajectory"] = True
    path = tmp_path / "result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    cell = extract_oracle_cell(path, "task-a", "s000_v001")

    assert cell["oracle_result"]["rewards"]["reward"] == 0.75


def test_extract_oracle_cell_rejects_result_without_usable_reward(tmp_path: Path) -> None:
    # A transport failure that prevented the verifier from running leaves no
    # reward at all; such a result must still be rejected.
    payload = _result("task-a")
    payload["rewards"] = {}
    payload["error"] = "transport failed"
    payload["error_category"] = "transport_error"
    path = tmp_path / "result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="no usable reward"):
        extract_oracle_cell(path, "task-a", "s000_v001")


def test_find_result_json_is_scoped_to_current_jobs_dir(tmp_path: Path) -> None:
    # A result for an unrelated task is ignored, and a single usable result is returned.
    result_path = tmp_path / "rollout" / "result.json"
    result_path.parent.mkdir()
    result_path.write_text(json.dumps(_result()), encoding="utf-8")

    assert find_result_json(tmp_path, "task-a") == result_path


def test_find_result_json_raises_when_no_result_at_all(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="0 of"):
        find_result_json(tmp_path, "task-a")


def test_find_exact_result_json_selects_latest_retry_in_trial_scope(tmp_path: Path) -> None:
    first = tmp_path / "first" / "result.json"
    second = tmp_path / "second" / "result.json"
    first.parent.mkdir()
    second.parent.mkdir()
    first_payload = _result()
    first_payload["started_at"] = "2026-07-22 10:00:00"
    first.write_text(json.dumps(first_payload), encoding="utf-8")
    second_payload = _result()
    second_payload["started_at"] = "2026-07-22 10:01:00"
    second.write_text(json.dumps(second_payload), encoding="utf-8")

    assert find_exact_result_json(tmp_path, "task-a") == second


def test_find_result_json_prefers_usable_over_no_reward(tmp_path: Path) -> None:
    # An install failure that prevented the verifier from running leaves no reward;
    # a later attempt with a usable reward must be selected instead.
    failed = tmp_path / "failed" / "result.json"
    failed.parent.mkdir()
    failed_payload = _result()
    failed_payload["rewards"] = {}
    failed_payload["error"] = "install failed"
    failed_payload["error_category"] = "install_failure"
    failed.write_text(json.dumps(failed_payload), encoding="utf-8")
    succeeded = tmp_path / "succeeded" / "result.json"
    succeeded.parent.mkdir()
    succeeded_payload = _result()
    succeeded_payload["started_at"] = "2026-07-14 10:16:08"
    succeeded.write_text(json.dumps(succeeded_payload), encoding="utf-8")

    assert find_result_json(tmp_path, "task-a") == succeeded


def test_find_result_json_picks_newest_among_multiple_usable(tmp_path: Path) -> None:
    # Concurrent rollouts or a resume retry can leave several usable results;
    # the most recent started_at must win so a fresh run is preferred over a stale one.
    stale = tmp_path / "stale" / "result.json"
    stale.parent.mkdir()
    stale_payload = _result()
    stale_payload["started_at"] = "2026-07-13 23:50:03"
    stale.write_text(json.dumps(stale_payload), encoding="utf-8")
    fresh = tmp_path / "fresh" / "result.json"
    fresh.parent.mkdir()
    fresh_payload = _result()
    fresh_payload["started_at"] = "2026-07-14 10:16:08"
    fresh.write_text(json.dumps(fresh_payload), encoding="utf-8")

    assert find_result_json(tmp_path, "task-a") == fresh


def test_usage_record_preserves_authoritative_total_and_unknown_breakdown() -> None:
    record = extract_usage_record(
        _result(),
        source="benchmark",
        role="openhands",
        phase="train",
        usage_record_id="run-task-a-1",
    )

    assert record["total_tokens"] == 123
    assert record["input_tokens"] is None
    assert record["output_tokens"] is None
    assert record["breakdown_available"] is False
    assert record["cost_usd"] == 0.5


def test_register_openhands_sse_copies_openhands_and_changes_only_launch() -> None:
    script = """
import json
from benchflow.agents.registry import AGENTS
from skilllift_eval.runners.skillsbench_adapter import register_openhands_sse

config = register_openhands_sse()
base = AGENTS["openhands"]
print(json.dumps({
    "name": config.name,
    "install_matches": config.install_cmd == base.install_cmd,
    "skill_paths_match": config.skill_paths == base.skill_paths,
    "env_mapping": config.env_mapping,
    "base_env_mapping": base.env_mapping,
    "launch_cmd": config.launch_cmd,
}))
"""
    completed = subprocess.run(
        [str(SKILLSBENCH_PYTHON), "-c", script],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.splitlines()[-1])
    assert payload["name"] == "openhands-sse"
    assert payload["install_matches"] is True
    assert payload["skill_paths_match"] is True
    assert payload["env_mapping"] == {
        **payload["base_env_mapping"],
        "BENCHFLOW_PROVIDER_MODEL": "LLM_MODEL",
    }
    launch_cmd = payload["launch_cmd"]
    assert "get_default_cli_agent" in launch_cmd
    assert "model_dump_json" in launch_cmd
    assert "run_acp_server" in launch_cmd
    assert "streaming_enabled=True" in launch_cmd
    assert "OH_SKILLS_RECEIPT_ROOT=/logs/agent/skilllift-skills-receipts" in launch_cmd
    assert "/tmp/skilllift-oh-patch/sitecustomize.py" in launch_cmd
    assert "PYTHONPATH" in launch_cmd
    assert "site.getsitepackages" not in launch_cmd


def test_prebuilt_cleanup_preserves_images_but_built_cleanup_removes_them() -> None:
    class FakeDockerSandbox:
        def __init__(self, *, use_prebuilt: bool) -> None:
            self._use_prebuilt = use_prebuilt
            self.commands: list[list[str]] = []

        async def _run_docker_compose_command(self, command, **kwargs):
            self.commands.append(command)

    register_benchflow_prebuilt_cleanup(FakeDockerSandbox)
    prebuilt = FakeDockerSandbox(use_prebuilt=True)
    built = FakeDockerSandbox(use_prebuilt=False)
    cleanup = ["down", "--rmi", "all", "--volumes", "--remove-orphans"]

    asyncio.run(prebuilt._run_docker_compose_command(cleanup))
    asyncio.run(built._run_docker_compose_command(cleanup))

    assert prebuilt.commands == [["down", "--volumes", "--remove-orphans"]]
    assert built.commands == [cleanup]


def test_vllm_provider_auth_uses_native_key_without_command_argument() -> None:
    assert provider_auth_env("vllm/gpt54mini-runtime-alias", "secret") == {
        "OPENAI_API_KEY": "secret"
    }


def _command_for(
    skills_dir: Path,
    jobs_dir: Path,
    environment_manifest: Path | None = None,
) -> SkillsBenchCommand:
    return SkillsBenchCommand(
        bench_executable=Path("/bin/true"),
        tasks_dir=Path("/tmp/tasks"),
        task_ids=("task-a",),
        agent="openhands-sse",
        model="vllm/test",
        sandbox="docker",
        skill_mode="with-skill",
        skills_dir=skills_dir,
        jobs_dir=jobs_dir,
        environment_manifest=environment_manifest,
    )


def test_command_passes_environment_manifest_and_round_trips(tmp_path: Path) -> None:
    manifest = tmp_path / "environment.toml"
    command = _command_for(
        tmp_path / "skills",
        tmp_path / "jobs",
        environment_manifest=manifest,
    )

    argv = command.argv()

    assert argv[argv.index("--environment-manifest") + 1] == str(manifest)
    assert SkillsBenchCommand.from_dict(command.to_dict()) == command


def _write_result(path: Path, *, skills_dir: Path, reward: float = 0.75, task_id: str = "task-a") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _result(task_id)
    payload["skill_mode"] = "with-skill"
    payload["effective_skills_dir"] = str(skills_dir)
    payload["rewards"] = {"reward": reward}
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_adapter_reuse_returns_prior_trial_result(tmp_path: Path) -> None:
    # A crashed run left a valid result.json under trial-0; resume must reuse it
    # instead of re-executing the agent.
    skills_dir = tmp_path / "final_skill"
    skills_dir.mkdir()
    jobs_scope = tmp_path / "jobs" / "trial-0"
    result_path = jobs_scope / "attempt-1" / "rollout" / "result.json"
    _write_result(result_path, skills_dir=skills_dir)

    command = _command_for(skills_dir, jobs_scope / "attempt-2")
    adapter = SkillsBenchSubprocessAdapter()
    ledger = tmp_path / "usage" / "usage.jsonl"

    reused = adapter.reuse(
        jobs_scope,
        command,
        project_root=tmp_path,
        task_id="task-a",
        skill_id="final-cat",
        ledger_path=ledger,
        usage_context={"usage_record_id": "reuse-1", "phase": "test"},
    )

    assert reused is not None
    cell, payload, path = reused
    assert cell["oracle_result"]["rewards"]["reward"] == 0.75
    assert path == result_path
    assert payload["skill_mode"] == "with-skill"
    assert ledger.is_file() and "reuse-1" in ledger.read_text(encoding="utf-8")


def test_adapter_reuse_returns_none_when_no_result(tmp_path: Path) -> None:
    # Empty trial dir -> no reusable result, caller falls back to execute.
    skills_dir = tmp_path / "final_skill"
    skills_dir.mkdir()
    jobs_scope = tmp_path / "jobs" / "trial-0"
    jobs_scope.mkdir(parents=True)

    command = _command_for(skills_dir, jobs_scope / "attempt-1")
    adapter = SkillsBenchSubprocessAdapter()

    assert adapter.reuse(
        jobs_scope,
        command,
        project_root=tmp_path,
        task_id="task-a",
        skill_id="final-cat",
        ledger_path=tmp_path / "usage.jsonl",
        usage_context={"usage_record_id": "reuse-1", "phase": "test"},
    ) is None


def test_adapter_reuse_rejects_mismatched_skill_dir(tmp_path: Path) -> None:
    # A result produced under a different skill must not be reused for this run.
    skills_dir = tmp_path / "final_skill"
    skills_dir.mkdir()
    other_dir = tmp_path / "stale_skill"
    other_dir.mkdir()
    jobs_scope = tmp_path / "jobs" / "trial-0"
    result_path = jobs_scope / "attempt-1" / "rollout" / "result.json"
    _write_result(result_path, skills_dir=other_dir)

    command = _command_for(skills_dir, jobs_scope / "attempt-2")
    adapter = SkillsBenchSubprocessAdapter()

    assert adapter.reuse(
        jobs_scope,
        command,
        project_root=tmp_path,
        task_id="task-a",
        skill_id="final-cat",
        ledger_path=tmp_path / "usage.jsonl",
        usage_context={"usage_record_id": "reuse-1", "phase": "test"},
    ) is None
    assert provider_auth_env("anthropic/claude", "secret") == {}
