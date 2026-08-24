from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DOCKER_IMAGE  = os.environ.get("DOCKER_IMAGE",   "wildclawbench-ubuntu:v1.3")
TMP_WORKSPACE = os.environ.get("TMP_WORKSPACE",  "/tmp_workspace")
AGENT_SKILLS_DIR = "/root/.agents/skills"
MODEL_CHECKPOINT_SUFFIXES = {".bin", ".ckpt", ".onnx", ".pt", ".pth", ".safetensors"}
MODEL_CHECKPOINT_MIN_BYTES = 100 * 1024 * 1024

BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")

def remove_container(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)

def start_container(
    task_id: str,
    workspace_path: str,
    extra_env: str = "",
    tmp_path: str = "",
    lobster_env: list[str] | None = None,
    direct_env: dict[str, str] | None = None,
) -> None:
    proxy_http = os.environ.get('HTTP_PROXY_INNER', '')
    proxy_https = os.environ.get('HTTPS_PROXY_INNER', '')
    env_args = [
        "-e", f"http_proxy={proxy_http}",
        "-e", f"https_proxy={proxy_https}",
        "-e", f"HTTP_PROXY={proxy_http}",
        "-e", f"HTTPS_PROXY={proxy_https}",
        "-e", f"BRAVE_API_KEY={BRAVE_API_KEY}",
        "-e", f"no_proxy={'' if not proxy_http else os.environ.get('NO_PROXY_INNER', '')}",
    ]
    direct_env = direct_env or {}
    for line in extra_env.splitlines():
        key = line.strip()
        if not key or key.startswith("#"):
            continue
        if key in direct_env:
            logger.info("[%s] Skipping task env var overridden by direct env: %s", task_id, key)
            continue
        value = os.environ.get(key, "")
        env_args += ["-e", f"{key}={value}"]
        masked = (value[:4] + "***") if value else "(empty)"
        logger.info("[%s] Injecting env var: %s=%s", task_id, key, masked)

    for key in (lobster_env or []):
        value = os.environ.get(key, "")
        if not value:
            logger.warning("[%s] Lobster env key %s not found in environment, skipping", task_id, key)
            continue
        env_args += ["-e", f"{key}={value}"]
        masked = value[:4] + "***"
        logger.info("[%s] Injecting lobster env: %s=%s", task_id, key, masked)

    for key, value in direct_env.items():
        env_args += ["-e", f"{key}={value}"]
        masked = value[:4] + "***" if "KEY" in key and value else value
        logger.info("[%s] Injecting direct env: %s=%s", task_id, key, masked)
 
    cmd = [
        "docker", "run", "-d",
        "--name", task_id,
        *env_args,
        "-v", f"{workspace_path}:/app:ro",
        DOCKER_IMAGE,
        "/bin/bash", "-c", "tail -f /dev/null",
    ]
    logger.info("[%s] Starting container, mounting %s → /app (ro)", task_id, workspace_path)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Container startup failed:\n{r.stderr}")
    logger.info("[%s] Container ID: %s", task_id, r.stdout.strip()[:12])

    if tmp_path and os.path.exists(tmp_path):
        mkdir_cmd = ["docker", "exec", task_id, "mkdir", "-p", "/tmp_workspace/tmp"]
        subprocess.run(mkdir_cmd, capture_output=True)

        cp_cmd = ["docker", "cp", f"{tmp_path}/.", f"{task_id}:/tmp_workspace/tmp/"]
        
        logger.info("[%s] Copying temp files: %s → /tmp_workspace/tmp", task_id, tmp_path)
        cp_r = subprocess.run(cp_cmd, capture_output=True, text=True)
        
        if cp_r.returncode != 0:
            logger.error("[%s] File copy failed: %s", task_id, cp_r.stderr)
        else:
            logger.info("[%s] Temp file copy complete", task_id)

def setup_workspace(task_id: str, thinking: str | None = None) -> None:
    logger.info("[%s] Copying /app → %s", task_id, TMP_WORKSPACE)
    r = subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c",
         f"cp -r /app/. {TMP_WORKSPACE} && chmod -R u+w {TMP_WORKSPACE}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Workspace copy failed:\n{r.stderr}")

    if thinking is not None:
        logger.info("[%s] Setting thinkingDefault to %s", task_id, thinking)
        thinking_result = subprocess.run(
            ["docker", "exec", task_id,
             "openclaw", "config", "set", "agents.defaults.thinkingDefault", thinking],
            capture_output=True, text=True,
        )
        if thinking_result.returncode != 0:
            raise RuntimeError(
                f"Failed to set thinkingDefault to {thinking}:\n{thinking_result.stderr}"
            )

    # Symlink OpenClaw workspace → TMP_WORKSPACE so the image tool's
    # media-local-roots check allows reading files under /tmp_workspace.
    symlink_result = subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c",
         f"rm -rf /root/.openclaw/workspace && ln -s {TMP_WORKSPACE} /root/.openclaw/workspace"],
        capture_output=True, text=True,
    )
    if symlink_result.returncode != 0:
        raise RuntimeError(f"Workspace symlink setup failed:\n{symlink_result.stderr}")

    workspace_result = subprocess.run(
        ["docker", "exec", task_id,
         "openclaw", "config", "set", "agents.defaults.workspace", TMP_WORKSPACE],
        capture_output=True, text=True,
    )
    if workspace_result.returncode != 0:
        raise RuntimeError(f"Failed to set OpenClaw workspace to {TMP_WORKSPACE}:\n{workspace_result.stderr}")

def setup_skills(task_id: str, skills: str, skills_path: str) -> None:
    base_path = Path(skills_path)
    for line in skills.splitlines():
        line = line.strip()
        if not line:
            continue
        source_path = _resolve_skill_source(base_path, line)
        if source_path is None:
            raise RuntimeError(f"Skill source not found: {line} under {skills_path}")
        if not source_path.is_dir() or not (source_path / "SKILL.md").exists():
            raise RuntimeError(f"Skill source must be a directory containing SKILL.md: {source_path}")

        target_path = f"{AGENT_SKILLS_DIR}/{source_path.name}"
        mkdir_result = subprocess.run(
            ["docker", "exec", task_id, "mkdir", "-p", AGENT_SKILLS_DIR],
            capture_output=True, text=True,
        )
        if mkdir_result.returncode != 0:
            raise RuntimeError(f"Failed to create agent skills directory:\n{mkdir_result.stderr}")

        remove_result = subprocess.run(
            ["docker", "exec", task_id, "rm", "-rf", target_path],
            capture_output=True, text=True,
        )
        if remove_result.returncode != 0:
            raise RuntimeError(f"Failed to clear existing skill {target_path}:\n{remove_result.stderr}")

        r = subprocess.run(
            ["docker", "cp",
             str(source_path), f"{task_id}:{AGENT_SKILLS_DIR}/"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"Failed to copy skill {line} from {source_path}:\n{r.stderr}")

        check_result = subprocess.run(
            ["docker", "exec", task_id, "test", "-f", f"{target_path}/SKILL.md"],
            capture_output=True, text=True,
        )
        if check_result.returncode != 0:
            raise RuntimeError(f"Copied skill is missing SKILL.md in agent skills dir: {target_path}")
        logger.info("[%s] Skill copied to agent skills dir: %s → %s", task_id, source_path, target_path)


def _resolve_skill_source(base_path: Path, skill_name: str) -> Path | None:
    direct = base_path / skill_name
    if direct.exists():
        return direct

    short_match = re.fullmatch(r"(\d+)_task(\d+)", skill_name)
    if short_match:
        category, task_num = short_match.groups()
        pattern = f"{category}_*_task_{task_num}_*"
        for candidate in sorted(base_path.glob(pattern)):
            if candidate.is_dir():
                return candidate

    return None


def inject_openclaw_models(task_id: str, models_config: dict) -> None:
    """Inject custom models into ~/.openclaw/openclaw.json."""
    container_tmp_path = "/tmp/openclaw_models.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as tmp_file:
        json.dump(models_config, tmp_file, indent=2)
        tmp_file_path = tmp_file.name

    try:
        cp_r = subprocess.run(
            ["docker", "cp", tmp_file_path, f"{task_id}:{container_tmp_path}"],
            capture_output=True, text=True,
        )
        if cp_r.returncode != 0:
            raise RuntimeError(f"Failed to copy models config into container:\n{cp_r.stderr}")

        inject_cmd = f"""python3 - <<'PY'
import json
import pathlib

config_path = pathlib.Path('/root/.openclaw/openclaw.json')
models_path = pathlib.Path('{container_tmp_path}')

config = json.loads(config_path.read_text()) if config_path.exists() else {{}}
models_config = json.loads(models_path.read_text())

# Check if merge mode is enabled
if models_config.get('mode') == 'merge':
    # Merge providers instead of replacing
    if 'models' not in config:
        config['models'] = {{'providers': {{}}}}
    if 'providers' not in config['models']:
        config['models']['providers'] = {{}}

    # Merge each provider from models_config into existing config
    for provider_name, provider_data in models_config.get('providers', {{}}).items():
        config['models']['providers'][provider_name] = provider_data
else:
    # Replace mode (original behavior)
    config['models'] = models_config

config_path.write_text(json.dumps(config, indent=2))
PY"""
        r = subprocess.run(
            ["docker", "exec", task_id, "/bin/bash", "-c", inject_cmd],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"Failed to inject models config:\n{r.stderr}")
    finally:
        Path(tmp_file_path).unlink(missing_ok=True)

    logger.info("[%s] Injected custom models config", task_id)


def run_warmup(task_id: str, warmup: str) -> None:
    """Execute warmup bash commands line by line inside the container (skip blank lines and comments)."""
    if not warmup.strip():
        return
    commands = [
        line.strip()
        for line in warmup.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not commands:
        return

    logger.info("[%s] Running warmup (%d commands)", task_id, len(commands))
    for cmd in commands:
        logger.info("[%s] warmup: %s", task_id, cmd)
        r = subprocess.run(
            ["docker", "exec", task_id, "/bin/bash", "-c", cmd],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"Warmup command failed: {cmd!r}\n{r.stderr}")


def run_background(task_id: str, bash_cmd: str, log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        ["docker", "exec", task_id, "/bin/bash", "-c",
         f"cd {TMP_WORKSPACE} && {bash_cmd}"],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
    )
    proc._log_file = log_file
    logger.info("[%s] Started process PID=%s → %s", task_id, proc.pid, log_path)
    return proc


def close_proc_log(proc: subprocess.Popen) -> None:
    """Close the log file handle created by run_background."""
    log_file = getattr(proc, "_log_file", None)
    if log_file and not log_file.closed:
        log_file.close()


def collect_output_from_container(task_id: str, output_dir: Path) -> None:
    """Collect task output files from the container to output_dir/task_output/.

    Collection strategy:
      1. All files under /tmp/openclaw/ (agent session logs, etc.)
      2. Task deliverables under /tmp_workspace/results/
      3. The complete workspace only when WILDCLAW_COLLECT_FULL_WORKSPACE=1

    Large model checkpoints are never retained as rollout artifacts. They are
    reproducible runtime dependencies and can otherwise consume gigabytes per
    retry.
    """
    task_output_dir = output_dir / "task_output"
    task_output_dir.mkdir(parents=True, exist_ok=True)

    _copy_dir_from_container(task_id, "/tmp/openclaw/.", str(task_output_dir))

    workspace_out = task_output_dir / "workspace"
    workspace_out.mkdir(parents=True, exist_ok=True)

    collect_full_workspace = os.environ.get(
        "WILDCLAW_COLLECT_FULL_WORKSPACE", ""
    ).lower() in {"1", "true", "yes"}
    if collect_full_workspace:
        source = f"{TMP_WORKSPACE}/."
        destination = workspace_out
    else:
        source = f"{TMP_WORKSPACE}/results/."
        destination = workspace_out / "results"
        destination.mkdir(parents=True, exist_ok=True)

    ok = _copy_dir_from_container(task_id, source, str(destination))
    if not ok:
        logger.warning("[%s] task results do not exist or are empty", task_id)
    _remove_large_model_checkpoints(workspace_out)


def _remove_large_model_checkpoints(workspace_root: Path) -> None:
    for path in workspace_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in MODEL_CHECKPOINT_SUFFIXES:
            continue
        try:
            if path.stat().st_size < MODEL_CHECKPOINT_MIN_BYTES:
                continue
            size = path.stat().st_size
            path.unlink()
            logger.info("Removed rollout model checkpoint %s (%d bytes)", path, size)
        except OSError as exc:
            logger.warning("Failed to remove rollout model checkpoint %s: %s", path, exc)


def inject_lobster_workspace(task_id: str, workspace_path: str) -> None:
    """Copy the entire lobster workspace into /root/ (the OpenClaw workspace in the image).

    This brings in everything: SOUL.md, USER.md, MEMORY.md, memory/, skills/, etc.
    """
    r = subprocess.run(
        ["docker", "cp", f"{workspace_path}/.", f"{task_id}:/root/"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        logger.error("[%s] Lobster workspace copy failed: %s", task_id, r.stderr)
    else:
        logger.info("[%s] Lobster workspace copied: %s → /root/", task_id, workspace_path)


def _copy_dir_from_container(task_id: str, src: str, dest: str) -> bool:
    r = subprocess.run(
        ["docker", "cp", f"{task_id}:{src}", dest],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        logger.info("[%s] Collected container directory %s → %s", task_id, src, dest)
        return True
    return False
