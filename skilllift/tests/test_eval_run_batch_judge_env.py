import subprocess
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "WildClawBench"))

from eval.run_batch import (
    build_skill_read_prompt,
    detect_rate_limit_signal,
    existing_task_runs,
    load_models_config,
    merge_skill_dir_override,
    partition_tasks_by_existing_runs,
    resolve_output_root,
    resolve_judge_env,
)
from src.utils.cli_args import build_run_batch_parser
from src.utils.docker_utils import setup_skills, setup_workspace, start_container


def test_start_container_injects_direct_env(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(cmd, capture_output=True, text=False):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="container-id\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    start_container(
        "task",
        str(tmp_path),
        direct_env={
            "SKILLLIFT_JUDGE_BASE_URL": "https://example.test/v4",
            "SKILLLIFT_JUDGE_API_KEY": "token",
            "SKILLLIFT_JUDGE_MODEL": "glm-5.1",
        },
    )
    docker_run = calls[0]
    assert "-e" in docker_run
    assert "SKILLLIFT_JUDGE_BASE_URL=https://example.test/v4" in docker_run
    assert "SKILLLIFT_JUDGE_API_KEY=token" in docker_run
    assert "SKILLLIFT_JUDGE_MODEL=glm-5.1" in docker_run


def test_start_container_prefers_direct_env_over_task_env(monkeypatch, tmp_path) -> None:
    calls = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "old-token")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

    def fake_run(cmd, capture_output=True, text=False):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="container-id\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    start_container(
        "task",
        str(tmp_path),
        extra_env="OPENROUTER_API_KEY\nOPENROUTER_BASE_URL\n",
        direct_env={
            "OPENROUTER_API_KEY": "new-token",
            "OPENROUTER_BASE_URL": "https://example.test/v1",
        },
    )
    docker_run = calls[0]

    assert "OPENROUTER_API_KEY=new-token" in docker_run
    assert "OPENROUTER_BASE_URL=https://example.test/v1" in docker_run
    assert "OPENROUTER_API_KEY=old-token" not in docker_run
    assert "OPENROUTER_BASE_URL=https://openrouter.ai/api/v1" not in docker_run


def test_setup_skills_copies_to_agent_skills(monkeypatch, tmp_path) -> None:
    source_root = tmp_path / "skills"
    skill_dir = source_root / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo-skill\n---\n", encoding="utf-8")

    calls = []

    def fake_run(cmd, capture_output=True, text=False):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    setup_skills("task", "demo-skill", str(source_root))

    assert ["docker", "exec", "task", "mkdir", "-p", "/root/.openclaw/skills"] in calls
    assert ["docker", "exec", "task", "rm", "-rf", "/root/.openclaw/skills/demo-skill"] in calls
    assert ["docker", "cp", str(skill_dir), "task:/root/.openclaw/skills/"] in calls
    assert ["docker", "exec", "task", "test", "-f", "/root/.openclaw/skills/demo-skill/SKILL.md"] in calls
    assert all("/tmp_workspace/skills" not in " ".join(cmd) for cmd in calls)


def test_build_skill_read_prompt_points_to_agent_skills() -> None:
    prompt = build_skill_read_prompt("demo-skill\nother-skill\n")

    assert "~/.openclaw/skills/demo-skill/SKILL.md" in prompt
    assert "~/.openclaw/skills/other-skill/SKILL.md" in prompt
    assert "/tmp_workspace/skills" not in prompt


def test_build_skill_read_prompt_empty_without_skills() -> None:
    assert build_skill_read_prompt("\n") == ""


def test_merge_skill_dir_override_preserves_task_skills_and_adds_skilllift_skill(tmp_path) -> None:
    source_root = tmp_path / "skills"
    native_skill = source_root / "agent-browser"
    nested_skill = source_root / "vendor" / "domain-skill"
    skilllift_skill = tmp_path / "skilllift-skill"
    native_skill.mkdir(parents=True)
    nested_skill.mkdir(parents=True)
    skilllift_skill.mkdir()
    (native_skill / "SKILL.md").write_text("---\nname: agent-browser\n---\n", encoding="utf-8")
    (nested_skill / "SKILL.md").write_text("---\nname: domain-skill\n---\n", encoding="utf-8")
    (skilllift_skill / "SKILL.md").write_text("---\nname: skilllift-skill\n---\n", encoding="utf-8")

    merged_root, merged_skills = merge_skill_dir_override(
        "agent-browser\nvendor/domain-skill\n",
        str(source_root),
        str(skilllift_skill),
    )

    merged_root_path = tmp_path / "skilllift-skill__merged_skills"
    assert merged_root == str(merged_root_path)
    assert merged_skills.splitlines() == ["agent-browser", "vendor/domain-skill", "skilllift-skill"]
    assert (merged_root_path / "agent-browser" / "SKILL.md").is_file()
    assert (merged_root_path / "vendor" / "domain-skill" / "SKILL.md").is_file()
    assert (merged_root_path / "skilllift-skill" / "SKILL.md").is_file()
    prompt = build_skill_read_prompt(merged_skills)
    assert "~/.openclaw/skills/agent-browser/SKILL.md" in prompt
    assert "~/.openclaw/skills/domain-skill/SKILL.md" in prompt
    assert "~/.openclaw/skills/skilllift-skill/SKILL.md" in prompt


def test_merge_skill_dir_override_preserves_external_markdown_skill(tmp_path) -> None:
    source_root = tmp_path / "skills"
    source_root.mkdir()
    (source_root / "task-skill.md").write_text("---\nname: task-skill\n---\n", encoding="utf-8")
    skilllift_skill = tmp_path / "skilllift-skill"
    skilllift_skill.mkdir()
    (skilllift_skill / "SKILL.md").write_text("---\nname: skilllift-skill\n---\n", encoding="utf-8")

    merged_root, _ = merge_skill_dir_override("task-skill\n", str(source_root), str(skilllift_skill))

    assert (Path(merged_root) / "task-skill" / "SKILL.md").is_file()


def test_setup_skills_accepts_external_markdown_skill(monkeypatch, tmp_path) -> None:
    skill_file = tmp_path / "task-skill.md"
    skill_file.write_text("---\nname: task-skill\n---\n", encoding="utf-8")
    calls = []

    def fake_run(cmd, capture_output=True, text=False):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    setup_skills("task", "task-skill", str(tmp_path))

    assert ["docker", "exec", "task", "mkdir", "-p", "/root/.openclaw/skills/task-skill"] in calls
    assert [
        "docker",
        "cp",
        str(skill_file),
        "task:/root/.openclaw/skills/task-skill/SKILL.md",
    ] in calls


def test_setup_workspace_points_openclaw_at_tmp_workspace(monkeypatch) -> None:
    calls = []

    def fake_run(cmd, capture_output=True, text=False):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    setup_workspace("task")

    assert [
        "docker",
        "exec",
        "task",
        "openclaw",
        "config",
        "set",
        "agents.defaults.workspace",
        "/tmp_workspace",
    ] in calls


def test_setup_skills_raises_when_source_missing(tmp_path) -> None:
    try:
        setup_skills("task", "missing-skill", str(tmp_path))
    except RuntimeError as exc:
        assert "Skill source not found" in str(exc)
    else:
        raise AssertionError("setup_skills should fail when a requested skill is missing")


def test_resolve_judge_env_includes_legacy_openrouter_aliases() -> None:
    env = resolve_judge_env(
        {
            "moduleModels": {"judge": "proxy/gpt-5.4", "openclaw_agent": "proxy/gpt-5.4"},
            "providers": {
                "proxy": {
                    "baseUrl": "https://example.test/v1",
                    "apiKey": "token",
                    "models": [{"id": "gpt-5.4", "name": "proxy/gpt-5.4"}],
                }
            },
        },
        "proxy/gpt-5.4",
    )

    assert env["SKILLLIFT_JUDGE_BASE_URL"] == "https://example.test/v1"
    assert env["SKILLLIFT_JUDGE_API_KEY"] == "token"
    assert env["SKILLLIFT_JUDGE_MODEL"] == "gpt-5.4"
    assert env["OPENROUTER_BASE_URL"] == "https://example.test/v1"
    assert env["OPENROUTER_API_KEY"] == "token"
    assert env["JUDGE_MODEL"] == "gpt-5.4"


def test_load_models_config_expands_custom_endpoint_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GPT_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("MY_PROXY_API_KEY", "token")
    monkeypatch.setenv("GPT_MODEL", "gpt-5.4")
    config_path = tmp_path / "models.json"
    config_path.write_text(
        json.dumps(
            {
                "providers": {
                    "proxy": {
                        "baseUrl": "${GPT_BASE_URL}",
                        "apiKey": "${MY_PROXY_API_KEY}",
                        "models": [{"id": "${GPT_MODEL}", "name": "proxy/gpt-5.4"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_models_config(config_path)

    provider = config["providers"]["proxy"]
    assert provider["baseUrl"] == "https://example.test/v1"
    assert provider["apiKey"] == "token"
    assert provider["models"][0]["id"] == "gpt-5.4"


def test_detect_rate_limit_signal_scans_task_logs(tmp_path) -> None:
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    (output_dir / "agent.log").write_text("normal progress\n", encoding="utf-8")
    (output_dir / "gateway.log").write_text("rate_limit from provider\n", encoding="utf-8")
    (output_dir / "usage.json").write_text(json.dumps({"elapsed_time": 285.65}), encoding="utf-8")
    (output_dir / "score.json").write_text(json.dumps({"overall_score": 0.5878}), encoding="utf-8")

    signal = detect_rate_limit_signal(output_dir)

    assert signal is None


def test_detect_rate_limit_signal_uses_timeout_score_pair(tmp_path) -> None:
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    usage_path = output_dir / "usage.json"
    score_path = output_dir / "score.json"
    usage_path.write_text(json.dumps({"elapsed_time": 1200.0}), encoding="utf-8")
    score_path.write_text(json.dumps({"overall_score": 0.0}), encoding="utf-8")

    signal = detect_rate_limit_signal(output_dir)

    assert signal == f"{usage_path} + {score_path}"


def test_detect_rate_limit_signal_ignores_missing_score_or_usage(tmp_path) -> None:
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    (output_dir / "usage.json").write_text(json.dumps({"elapsed_time": 1200.0}), encoding="utf-8")

    assert detect_rate_limit_signal(output_dir) is None


def test_existing_task_runs_detects_run_subdirectories(tmp_path) -> None:
    task = {"category": "01_Productivity_Flow", "task_id": "task_7_demo"}
    task_root = tmp_path / "01_Productivity_Flow" / "task_7_demo"
    run_dir = task_root / "glm-5-turbo_20260512_120000_ab12cd"
    run_dir.mkdir(parents=True)
    (task_root / "summary_glm-5-turbo.json").write_text("{}", encoding="utf-8")

    assert existing_task_runs(tmp_path, task) == [run_dir]


def test_partition_tasks_by_existing_runs_keeps_only_pending_tasks(tmp_path) -> None:
    existing_task = {"category": "01_Productivity_Flow", "task_id": "task_7_demo"}
    pending_task = {"category": "01_Productivity_Flow", "task_id": "task_8_demo"}
    run_dir = tmp_path / "01_Productivity_Flow" / "task_7_demo" / "glm-5-turbo_run"
    run_dir.mkdir(parents=True)

    pending, skipped = partition_tasks_by_existing_runs(
        [existing_task, pending_task],
        tmp_path,
    )

    assert pending == [pending_task]
    assert skipped == [(existing_task, [run_dir])]


def test_external_cli_accepts_skilllift_resume_and_retry_flags() -> None:
    parser = build_run_batch_parser("model", 1)

    args = parser.parse_args(
        [
            "--category",
            "all",
            "--resume-skip-existing",
            "--rate-limit-retries",
            "2",
            "--rate-limit-wait-seconds",
            "120",
        ]
    )

    assert args.resume_skip_existing is True
    assert args.rate_limit_retries == 2
    assert args.rate_limit_wait_seconds == 120


def test_openclaw_output_root_preserves_skilllift_layout(tmp_path) -> None:
    assert resolve_output_root(tmp_path, "openclaw") == tmp_path
    assert resolve_output_root(tmp_path, "codex") == tmp_path / "codex"
