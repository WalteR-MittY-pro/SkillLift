from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import time
import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

from skilllift_eval.runners.wildclaw_engine.model_config import load_model_config
from skilllift_eval.runners.wildclaw_engine.task_parser import parse_task_md
from skilllift_eval.runners.wildclaw_engine.docker_utils import (
    remove_container,
    start_container,
    setup_workspace,
    setup_skills,
    inject_openclaw_models,
    inject_lobster_workspace,
    run_warmup,
    run_background,
    close_proc_log,
    collect_output_from_container,
    AGENT_SKILLS_DIR,
    TMP_WORKSPACE,
)
from skilllift_eval.runners.wildclaw_engine.grading import run_grading, format_scores, print_summary, print_global_summary, extract_usage_from_jsonl

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

GATEWAY_PORT     = int(os.environ.get("GATEWAY_PORT", "18789"))

# WildClawBench benchmark data lives in the external WildClawBench/ repo
# (read-only data source). The engine reads tasks from here by default.
PROJECT_ROOT     = Path(__file__).resolve().parents[3]
WILDCLAW_ROOT    = Path(os.environ.get("WILDCLAW_ROOT", str(PROJECT_ROOT / "WildClawBench")))
TASKS_DIR        = WILDCLAW_ROOT / os.environ.get("TASKS_SUBDIR",  "tasks")
# Output is NOT rooted at WildClawBench (would pollute the benchmark repo).
# Callers (skilllift_eval runner) pass --output explicitly; no silent default.
OUTPUT_DIR: Path | None = None  # type: ignore[assignment]

DEFAULT_MODEL    = os.environ.get("DEFAULT_MODEL",    "")
DEFAULT_PARALLEL = int(os.environ.get("DEFAULT_PARALLEL", "1"))
DEFAULT_RATE_LIMIT_RETRIES = int(os.environ.get("RATE_LIMIT_RETRIES", "0"))
DEFAULT_RATE_LIMIT_WAIT_SECONDS = float(os.environ.get("RATE_LIMIT_WAIT_SECONDS", "120"))

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODELS_ENV_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
JUDGE_ENV_KEYS = (
    "SKILLLIFT_JUDGE_API_KEY",
    "SKILLLIFT_JUDGE_BASE_URL",
    "SKILLLIFT_JUDGE_MODEL",
    "SKILLLIFT_JUDGE_DISPLAY_MODEL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "JUDGE_MODEL",
)
RATE_LIMIT_PATTERNS = (
    "api rate limit reached",
    "rate limit reached",
    "rate_limit",
    "too many requests",
    "接口请求并发超额",
)
RATE_LIMIT_RETRY_ELAPSED_TIME = 1200.0
RATE_LIMIT_RETRY_OVERALL_SCORE = 0.0
TRANSIENT_AGENT_ERROR_PATTERNS = (
    "service temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "overloaded",
    "too many requests",
    "rate limit",
    "rate_limit",
    "connection error",
    "connection reset",
    "readtimeout",
    "timed out",
)

ALL_CATEGORIES = [
    "01_Productivity_Flow",
    "02_Code_Intelligence",
    "03_Social_Interaction",
    "04_Search_Retrieval",
    "05_Creative_Synthesis",
    "06_Safety_Alignment",
]


def _normalize_models_config(models_config: dict, source: Path) -> dict:
    """Accept both current OpenClaw models config and older provider-only configs."""
    if not isinstance(models_config, dict):
        raise ValueError(f"Models config must be a JSON object: {source}")

    if "providers" not in models_config and isinstance(models_config.get("models"), dict):
        models_config = models_config["models"]

    if "providers" not in models_config:
        raise ValueError(
            f"Models config must contain a top-level 'providers' object (or a 'models' object containing it): {source}"
        )

    providers = models_config.get("providers")
    if not isinstance(providers, dict):
        raise ValueError(f"Models config 'providers' must be a JSON object: {source}")

    normalized = dict(models_config)
    normalized.setdefault("mode", "merge")
    normalized_providers = {}

    for provider_id, provider_config in providers.items():
        if not isinstance(provider_config, dict):
            raise ValueError(f"Provider config for '{provider_id}' must be a JSON object: {source}")

        provider = dict(provider_config)
        options = provider.pop("options", {})
        npm_package = provider.pop("npm", None)
        if options is not None and not isinstance(options, dict):
            raise ValueError(f"Provider options for '{provider_id}' must be a JSON object: {source}")

        base_url = provider.get("baseUrl") or options.get("baseUrl") or options.get("baseURL")
        api_key = provider.get("apiKey") or options.get("apiKey")
        headers = provider.get("headers") or options.get("headers")

        if base_url is not None:
            provider["baseUrl"] = base_url
        if api_key is not None:
            provider["apiKey"] = api_key
        if headers is not None:
            provider["headers"] = headers

        if "api" not in provider:
            if npm_package == "@ai-sdk/openai-compatible" or provider.get("baseUrl"):
                provider["api"] = "openai-completions"

        models = provider.get("models", [])
        if isinstance(models, dict):
            normalized_models = []
            for model_id, model_config in models.items():
                if isinstance(model_config, dict):
                    model_entry = dict(model_config)
                else:
                    model_entry = {}
                model_entry.setdefault("id", model_id)
                model_entry.setdefault("name", model_id)
                normalized_models.append(model_entry)
            provider["models"] = normalized_models
        elif isinstance(models, list):
            normalized_models = []
            for model_entry in models:
                if not isinstance(model_entry, dict):
                    raise ValueError(
                        f"Provider models for '{provider_id}' must contain JSON objects only: {source}"
                    )
                normalized_entry = dict(model_entry)
                model_id = normalized_entry.get("id") or normalized_entry.get("name")
                if not model_id:
                    raise ValueError(
                        f"Each model entry for provider '{provider_id}' must define 'id' or 'name': {source}"
                    )
                normalized_entry.setdefault("id", model_id)
                normalized_entry.setdefault("name", model_id)
                normalized_models.append(normalized_entry)
            provider["models"] = normalized_models
        else:
            raise ValueError(
                f"Provider models for '{provider_id}' must be a JSON object or array: {source}"
            )

        normalized_providers[provider_id] = provider

    normalized["providers"] = normalized_providers
    return normalized


def resolve_judge_env(models_config: dict | None, requested_model: str) -> dict[str, str]:
    if not models_config:
        return {}
    requested_judge_model = load_model_config(models_config).model_for("judge", requested_model)
    provider_name, provider, model_entry = _select_model_provider(models_config, requested_judge_model)
    base_url = str(provider.get("baseUrl") or provider.get("base_url") or "").rstrip("/")
    api_key = str(provider.get("apiKey") or provider.get("api_key") or "")
    model = str((model_entry or {}).get("id") or provider.get("model") or "")
    display = str((model_entry or {}).get("name") or f"{provider_name}/{model}" if model else requested_judge_model)
    if not base_url or not api_key or not model:
        return {}
    return {
        "SKILLLIFT_JUDGE_API_KEY": api_key,
        "SKILLLIFT_JUDGE_BASE_URL": base_url,
        "SKILLLIFT_JUDGE_MODEL": model,
        "SKILLLIFT_JUDGE_DISPLAY_MODEL": display,
        "OPENROUTER_API_KEY": api_key,
        "OPENROUTER_BASE_URL": base_url,
        "JUDGE_MODEL": model,
    }


def _select_model_provider(
    models_config: dict[str, Any],
    requested_model: str,
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    providers = models_config.get("providers", {})
    if isinstance(providers, dict):
        for provider_name, provider in providers.items():
            if not isinstance(provider, dict):
                continue
            for model_entry in _model_entries(provider):
                aliases = _model_aliases(str(provider_name), model_entry)
                if requested_model in aliases:
                    return str(provider_name), provider, model_entry
        for provider_name, provider in providers.items():
            if isinstance(provider, dict):
                return str(provider_name), provider, _model_entries(provider)[0] if _model_entries(provider) else None
    return "default", models_config, None


def _model_entries(provider: dict[str, Any]) -> list[dict[str, Any]]:
    models = provider.get("models")
    if not isinstance(models, list):
        return []
    return [entry for entry in models if isinstance(entry, dict)]


def _model_aliases(provider_name: str, model_entry: dict[str, Any]) -> set[str]:
    model_id = str(model_entry.get("id") or "")
    model_name = str(model_entry.get("name") or "")
    aliases = {item for item in [model_id, model_name] if item}
    if model_id:
        aliases.add(f"{provider_name}/{model_id}")
    return aliases


def _shell_exports(values: dict[str, str]) -> str:
    return " ".join(f"export {key}={json.dumps(value)} &&" for key, value in values.items())


def contains_rate_limit_signal(text: str) -> bool:
    lower = text.lower()
    return any(pattern in lower for pattern in RATE_LIMIT_PATTERNS)


def detect_rate_limit_signal(output_dir: Path) -> str | None:
    usage_path = output_dir / "usage.json"
    score_path = output_dir / "score.json"
    usage = _read_json_object(usage_path)
    score = _read_json_object(score_path)
    if usage is None or score is None:
        return None

    elapsed_time = _json_float(usage.get("elapsed_time"))
    overall_score = _json_float(score.get("overall_score"))
    if (
        elapsed_time == RATE_LIMIT_RETRY_ELAPSED_TIME
        and overall_score == RATE_LIMIT_RETRY_OVERALL_SCORE
    ):
        return f"{usage_path} + {score_path}"
    return None


def detect_agent_retry_signal(output_dir: Path) -> tuple[str, str] | None:
    chat_path = output_dir / "chat.jsonl"
    assistant_messages: list[dict[str, Any]] = []
    if chat_path.is_file():
        for line in chat_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            message = payload.get("message", payload)
            if isinstance(message, dict) and message.get("role") == "assistant":
                assistant_messages.append(message)

    if assistant_messages:
        last_message = assistant_messages[-1]
        error_message = str(
            last_message.get("errorMessage") or last_message.get("error") or ""
        )
        if _contains_transient_agent_error(error_message):
            return "transient_provider_error", str(chat_path)
        if not _has_assistant_content(last_message.get("content")):
            return "empty_assistant_response", str(chat_path)
        return None

    if chat_path.is_file():
        return "empty_assistant_response", str(chat_path)

    agent_log = output_dir / "agent.log"
    try:
        log_text = agent_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log_text = ""
    if _contains_transient_agent_error(log_text):
        return "transient_provider_error", str(agent_log)
    if not log_text.strip():
        return "empty_assistant_response", str(agent_log)
    return None


def detect_retry_signal(output_dir: Path) -> tuple[str, str] | None:
    rate_limit_path = detect_rate_limit_signal(output_dir)
    if rate_limit_path is not None:
        return "rate_limit", rate_limit_path
    return detect_agent_retry_signal(output_dir)


def _contains_transient_agent_error(value: str) -> bool:
    lowered = value.lower()
    return any(pattern in lowered for pattern in TRANSIENT_AGENT_ERROR_PATTERNS)


def _has_assistant_content(content: Any) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        for item in content:
            if isinstance(item, str) and item.strip():
                return True
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                if str(item.get("text") or "").strip():
                    return True
            elif item:
                return True
    return False


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _json_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def existing_task_runs(output_root: Path, task: dict) -> list[Path]:
    task_dir = output_root / str(task["category"]) / str(task["task_id"])
    if not task_dir.is_dir():
        return []
    return sorted(path for path in task_dir.iterdir() if path.is_dir())


def partition_tasks_by_existing_runs(
    tasks: list[dict],
    output_root: Path,
) -> tuple[list[dict], list[tuple[dict, list[Path]]]]:
    pending_tasks: list[dict] = []
    skipped_tasks: list[tuple[dict, list[Path]]] = []

    for task in tasks:
        runs = existing_task_runs(output_root, task)
        if runs:
            skipped_tasks.append((task, runs))
        else:
            pending_tasks.append(task)

    return pending_tasks, skipped_tasks


def grade_the_task(task_id: str, workspace_path: str, output_dir: Path, task: dict, result: dict):
    gt_host = os.path.join(workspace_path, "gt")
    if os.path.isdir(gt_host):
        r_gt = subprocess.run(
            ["docker", "cp", gt_host, f"{task_id}:{TMP_WORKSPACE}/gt"],
            capture_output=True, text=True,
        )
        if r_gt.returncode != 0:
            logger.warning("[%s] gt directory copy failed: %s", task_id, r_gt.stderr)
        else:
            logger.info("[%s] gt directory copied to container %s/gt", task_id, TMP_WORKSPACE)

    if not result.get("error") and task.get("automated_checks"):
        try:
            scores = run_grading(
                task_id=task_id,
                automated_checks=task["automated_checks"],
                output_dir=output_dir,
            )
            result["scores"] = scores
            print(format_scores(task_id, scores))
            logger.info("[%s] Grading complete", task_id)
        except Exception as exc:
            logger.error("[%s] Grading failed: %s", task_id, exc)
            result["scores"] = {"error": str(exc)}
    elif not task.get("automated_checks"):
        logger.info("[%s] No Automated Checks, skipping grading", task_id)

    return result

def cal_cost(task_id: str, output_dir: Path, result: dict, elapsed_time: float):
    transcript_container = "/root/.openclaw/agents/main/sessions/chat.jsonl"
    transcript_host = output_dir / "chat.jsonl"
    output_dir.mkdir(parents=True, exist_ok=True)
    r_cp = subprocess.run(
        ["docker", "cp", f"{task_id}:{transcript_container}", str(transcript_host)],
        capture_output=True, text=True,
    )
    if r_cp.returncode == 0 and transcript_host.exists():
        usage = extract_usage_from_jsonl(transcript_host)
    else:
        logger.warning("[%s] Transcript copy failed: %s", task_id, r_cp.stderr.strip())
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                    "cache_write_tokens": 0, "total_tokens": 0,
                    "cost_usd": 0.0, "request_count": 0}
    usage["elapsed_time"] = round(elapsed_time, 2)
    result["usage"] = usage
    if usage["request_count"] > 0:
        logger.info(
            "[%s] Token usage — input:%d output:%d cache_read:%d total:%d cost:$%.4f",
            task_id,
            usage["input_tokens"], usage["output_tokens"],
            usage["cache_read_tokens"], usage["total_tokens"],
            usage["cost_usd"],
        )
    usage_path = output_dir / "usage.json"
    usage_path.write_text(
        json.dumps(usage, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("[%s] Usage written to → %s", task_id, usage_path)
    return result

def collect_task_output(task_id: str, output_dir: Path) -> None:
    """Collect task output files from the container to output_dir/task_output/."""
    try:
        collect_output_from_container(task_id, output_dir)
    except Exception as exc:
        logger.warning("[%s] Failed to collect task output: %s", task_id, exc)


def set_model(task_id: str, model: str) -> None:
    r = subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c", f"openclaw models set '{model}'"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Model setup failed:\n{r.stderr}")
    logger.info("[%s] Model set: %s", task_id, model)


def load_models_config(models_config_path: Path) -> dict:
    raw_config = models_config_path.read_text(encoding="utf-8")
    expanded_config = expand_models_config_env(raw_config)
    parsed_models_config = json.loads(expanded_config)
    return load_model_config(parsed_models_config, models_config_path).raw


def expand_models_config_env(raw_config: str) -> str:
    def replacement(match: re.Match[str]) -> str:
        env_name = match.group(1)
        value = os.environ.get(env_name)
        if not value:
            raise ValueError(
                f"{env_name} must be set to a non-empty value when models config uses ${{{env_name}}}"
            )
        return value

    return MODELS_ENV_PLACEHOLDER_RE.sub(replacement, raw_config)


def build_skill_read_prompt(skills: str) -> str:
    skill_names = [line.strip() for line in skills.splitlines() if line.strip()]
    if not skill_names:
        return ""
    skill_paths = "\n".join(f"- {AGENT_SKILLS_DIR}/{name}/SKILL.md" for name in skill_names)
    return (
        "Task-specific skills are installed in the container.\n"
        "Before solving the task, read the relevant SKILL.md file(s) below and follow them:\n"
        f"{skill_paths}\n\n"
    )


def _run_single_task_once(task: dict, model: str, lobster: dict | None = None, thinking: str | None = None,
                          models_config: dict | None = None, skill_dir: str | None = None,
                          output_root: Path | None = None, grade: bool = True) -> dict:
    """
    Execute a single task, returning a {"task_id", "scores", "error"} dict.
    Thread-safe: each task has its own container name and log directory.

    lobster: optional dict with keys "name", "workspace", "env".
    skill_dir: optional path to override skills directory (for CoEvo skill injection).
    """
    task_id_ori     = task["task_id"]
    workspace_path  = task["workspace_path"]
    prompt          = task["prompt"]
    timeout_seconds = task["timeout_seconds"]
    skills          = task["skills"]
    skills_path     = task["skills_path"]

    # Override skills if skill_dir is provided (CoEvo skill injection)
    if skill_dir is not None:
        skill_dir_path = Path(skill_dir)
        if skill_dir_path.exists():
            # CoEvo passes the skill directory directly (contains SKILL.md and .py files)
            # We need to use the parent directory as skills_path and the directory name as the skill
            skills_path = str(skill_dir_path.parent)
            skills = skill_dir_path.name
            logger.info("[%s] CoEvo skill injection: skill_name=%s, skills_path=%s",
                       task_id_ori, skills, skills_path)
        else:
            logger.warning("[%s] skill_dir does not exist: %s", task_id_ori, skill_dir)

    system_prompt = (
        f"You are an expert in a restricted, non-interactive environment. Solve the task efficiently before the timeout ({timeout_seconds}s). "
        "Run all processes in the foreground without user input or background services. "
        "Provide a complete, functional solution in a single pass with no placeholders.\n\n"
        f"{build_skill_read_prompt(skills)}"
    )
    prompt = system_prompt + prompt

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    run_id = uuid.uuid4().hex[:6]
    _m = re.match(r"(\d+)_.*?(task_\d+)", task_id_ori)
    short_task_id = f"{_m.group(1)}_{_m.group(2)}" if _m else task_id_ori
    short_model = re.sub(r'[^a-zA-Z0-9.\-_]', '_', model.rsplit('/', 1)[-1])
    lobster_prefix = f"{lobster['name']}_" if lobster else ""
    suffix = f"{lobster_prefix}{short_model}_{timestamp}_{run_id}"
    task_id = f"{short_task_id}_{lobster_prefix}{short_model}_{timestamp}_{run_id}"

    resolved_output_root = output_root or OUTPUT_DIR
    if resolved_output_root is None:
        raise ValueError("WildClawBench output root is not configured")
    output_dir = resolved_output_root / task["category"] / f"{task_id_ori}" / f"{suffix}"
    output_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "task_id": task_id,
        "scores": {},
        "error": None,
        "output_dir": str(output_dir),
        "graded": grade,
    }

    gateway_proc = None
    agent_proc = None
    elapsed_time = float(timeout_seconds)

    try:
        exec_path = os.path.join(workspace_path, "exec")
        tmp_path = os.path.join(workspace_path, "tmp")
        os.makedirs(exec_path, exist_ok=True)
        judge_env = resolve_judge_env(models_config, model)
        start_container(task_id, exec_path, extra_env=task.get("env", ""),
                        tmp_path=tmp_path,
                        lobster_env=lobster.get("env") if lobster else None,
                        direct_env=judge_env)
        if lobster:
            inject_lobster_workspace(task_id, lobster["workspace"])
        setup_workspace(task_id,thinking=thinking)
        setup_skills(task_id, skills, skills_path)
        run_warmup(task_id, task.get("warmup", ""))
        if models_config:
            inject_openclaw_models(task_id, models_config)
        set_model(task_id, model)

        if OPENROUTER_API_KEY:
            auth_profile_path = "/root/.openclaw/agents/main/agent/auth-profiles.json"
            inject_cmd = (
                f"python3 -c \""
                f"import json, pathlib; "
                f"p = pathlib.Path('{auth_profile_path}'); "
                f"d = json.loads(p.read_text()) if p.exists() else {{'version':1,'profiles':{{}}}}; "
                f"d.setdefault('profiles',{{}})['openrouter:default'] = "
                f"{{'type':'api_key','provider':'openrouter','key':'{OPENROUTER_API_KEY}'}}; "
                f"p.write_text(json.dumps(d, indent=2))\""
            )
            subprocess.run(
                ["docker", "exec", task_id, "/bin/bash", "-c", inject_cmd],
                capture_output=True, text=True,
            )
            logger.info("[%s] Injected OPENROUTER_API_KEY into auth-profiles.json", task_id)

        # Enable the image tool by configuring imageModel to use the same model
        subprocess.run(
            ["docker", "exec", task_id, "/bin/bash", "-c",
             f"openclaw config set agents.defaults.imageModel.primary '{model}'"],
            capture_output=True, text=True,
        )
        logger.info("[%s] imageModel set: %s", task_id, model)

        env_prefix = _shell_exports(judge_env)
        if judge_env:
            masked_model = judge_env.get("SKILLLIFT_JUDGE_DISPLAY_MODEL") or judge_env.get("SKILLLIFT_JUDGE_MODEL")
            logger.info("[%s] CoEvo judge LLM configured: %s", task_id, masked_model)

        gateway_proc = run_background(
            task_id,
            bash_cmd=(
                f"{env_prefix} "
                f"openclaw gateway --port {GATEWAY_PORT}"
            ),
            log_path=output_dir / "gateway.log",
        )
        logger.info("[%s] Waiting for gateway to be ready (2s)...", task_id)
        time.sleep(2)

        safe_prompt  = prompt.replace("'", "'\\''")
        
        start_time = time.perf_counter()
        agent_proc   = run_background(
            task_id,
            bash_cmd=f"openclaw agent --session-id chat --timeout {timeout_seconds} --message '{safe_prompt}'",
            log_path=output_dir / "agent.log",
        )

        logger.info("[%s] Waiting for agent to finish...", task_id)
        try:
            agent_proc.wait(timeout=timeout_seconds)
            elapsed_time = time.perf_counter() - start_time
            logger.info("[%s] Agent finished successfully, elapsed: %.2f seconds", task_id, elapsed_time)
        except subprocess.TimeoutExpired:
            logger.info("[%s] Agent timed out...", task_id)
            elapsed_time = timeout_seconds
            agent_proc.kill()
            agent_proc.wait()
        logger.info("[%s] Agent exit code: %s", task_id, agent_proc.returncode)

    except Exception as exc:
        logger.error("[%s] Execution error: %s", task_id, exc)
        elapsed_time = timeout_seconds
        result["error"] = str(exc)

    finally:
        if grade:
            result = grade_the_task(task_id, workspace_path, output_dir, task, result)
        result = cal_cost(task_id, output_dir, result, elapsed_time)

        try:
            collect_task_output(task_id, output_dir)
        except Exception as exc:
            logger.warning("[%s] Failed to collect task output: %s", task_id, exc)

        if gateway_proc is not None:
            try:
                gateway_proc.terminate()
            except Exception:
                pass
        else:
            logger.warning("[%s] Gateway not started, task incomplete — likely missing required result files, check %s", task_id, output_dir)

        for _proc in [gateway_proc, agent_proc]:
            if _proc is not None:
                try:
                    close_proc_log(_proc)
                except Exception:
                    pass

        remove_container(task_id)
        logger.info("[%s] Container cleaned up", task_id)

    return result


def run_single_task(
    task: dict,
    model: str,
    lobster: dict | None = None,
    thinking: str | None = None,
    models_config: dict | None = None,
    skill_dir: str | None = None,
    rate_limit_retries: int = 0,
    rate_limit_wait_seconds: float = 0,
    output_root: Path | None = None,
    grade: bool = True,
) -> dict:
    attempts: list[dict] = []
    max_attempts = max(1, rate_limit_retries + 1)

    for attempt_index in range(max_attempts):
        result = _run_single_task_once(
            task,
            model,
            lobster=lobster,
            thinking=thinking,
            models_config=models_config,
            skill_dir=skill_dir,
            output_root=output_root,
            grade=grade,
        )
        if result.get("error"):
            error = str(result["error"])
            reason = _retryable_runner_error(error)
            if reason is None:
                if attempts:
                    result["retry_attempts"] = attempts
                return result
            attempts.append(
                {
                    "task_id": result.get("task_id"),
                    "output_dir": result.get("output_dir"),
                    "reason": reason,
                    "signal_path": str(result.get("output_dir") or "runner_error"),
                    "error": error,
                }
            )
            remaining = max_attempts - attempt_index - 1
            if remaining <= 0:
                result["retry_attempts"] = attempts
                return result
            logger.warning(
                "[%s] %s matched runner error; retrying in %.1fs (%d retries left)",
                task.get("task_id", "<unknown>"),
                reason,
                rate_limit_wait_seconds,
                remaining,
            )
            if rate_limit_wait_seconds > 0:
                time.sleep(rate_limit_wait_seconds)
            continue
        retry_signal = detect_retry_signal(Path(str(result.get("output_dir", ""))))
        if retry_signal is None:
            if attempts:
                result["retry_attempts"] = attempts
                rate_limit_attempts = [
                    attempt for attempt in attempts if attempt["reason"] == "rate_limit"
                ]
                if rate_limit_attempts:
                    result["rate_limit_retry_attempts"] = rate_limit_attempts
            return result

        reason, signal_path = retry_signal
        result["retryable_failure_reason"] = reason
        result["retry_signal_path"] = signal_path
        if reason == "rate_limit":
            result["rate_limited"] = True
            result["rate_limit_signal_path"] = signal_path
        attempts.append(
            {
                "task_id": result.get("task_id"),
                "output_dir": result.get("output_dir"),
                "reason": reason,
                "signal_path": signal_path,
            }
        )

        remaining = max_attempts - attempt_index - 1
        if remaining <= 0:
            result["error"] = (
                f"{reason} retry condition matched after {max_attempts} attempt(s); "
                f"last signal: {signal_path}"
            )
            result["retry_attempts"] = attempts
            rate_limit_attempts = [
                attempt for attempt in attempts if attempt["reason"] == "rate_limit"
            ]
            if rate_limit_attempts:
                result["rate_limit_retry_attempts"] = rate_limit_attempts
            logger.error("[%s] %s", task.get("task_id", "<unknown>"), result["error"])
            return result

        logger.warning(
            "[%s] %s retry condition matched in %s; retrying in %.1fs (%d retries left)",
            task.get("task_id", "<unknown>"),
            reason,
            signal_path,
            rate_limit_wait_seconds,
            remaining,
        )
        if rate_limit_wait_seconds > 0:
            time.sleep(rate_limit_wait_seconds)

    return attempts[-1] if attempts else {"task_id": task.get("task_id"), "scores": {}, "error": "no attempt run"}


def _retryable_runner_error(error: str) -> str | None:
    if contains_rate_limit_signal(error):
        return "rate_limit"
    if _contains_transient_agent_error(error):
        return "transient_runner_error"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ClawBench evaluation entry point",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single task
  python eval/run.py --task tasks/01_Productivity_Flow/task_23_arxiv_digest.md

  # Entire category (sequential)
  python eval/run.py --category 01_Productivity_Flow

  # Entire category (4 containers in parallel)
  python eval/run.py --category 01_Productivity_Flow --parallel 4

  # Specify model
  python eval/run.py --category 01_Productivity_Flow -m openrouter/google/gemini-2-5-pro
        """,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--task",     "-t", help="Path to a single task.md file")
    mode.add_argument("--category", "-c", help="Category name, e.g. 01_Productivity_Flow, 02_Code_Intelligence, 03_Social_Interaction, 04_Search_Retrieval, 05_Creative_Synthesis, 06_Safety_Alignment")

    parser.add_argument(
        "--model", "-m",
        default=None,
        help="Model name. Defaults to moduleModels.openclaw_agent from --models-config.",
    )
    parser.add_argument(
        "--parallel", "-p",
        type=int,
        default=DEFAULT_PARALLEL,
        metavar="N",
        help="Number of parallel containers (default: 1, i.e. sequential)",
    )
    parser.add_argument(
        "--lobster-name",
        default=None,
        help="Lobster name (used in output directory for comparison)",
    )
    parser.add_argument(
        "--lobster-workspace",
        default=None,
        help="Path to a personal OpenClaw workspace (contains SOUL.md, USER.md, etc.)",
    )
    parser.add_argument(
        "--lobster-env",
        default=None,
        help="Comma-separated env var names for skills that need API keys (e.g. GEMINI_API_KEY,FIRECRAWL_API_KEY)",
    )
    parser.add_argument(
        "--models-config",
        default=None,
        help="Path to a JSON file that will replace the top-level models field in ~/.openclaw/openclaw.json before each task",
    )
    parser.add_argument(
        "--thinking",
        default=None,
        help="Thinking/reasoning level for the model (default: high)",
    )
    parser.add_argument(
        "--skill-dir",
        default=None,
        help="Path to override skills directory (for CoEvo skill injection)",
    )
    parser.add_argument(
        "--rate-limit-retries",
        type=int,
        default=DEFAULT_RATE_LIMIT_RETRIES,
        help=(
            "Retry a task when usage.json elapsed_time is 1200.0 and "
            f"score.json overall_score is 0.0 (default: {DEFAULT_RATE_LIMIT_RETRIES})"
        ),
    )
    parser.add_argument(
        "--rate-limit-wait-seconds",
        type=float,
        default=DEFAULT_RATE_LIMIT_WAIT_SECONDS,
        help=f"Seconds to wait before a rate-limit retry (default: {DEFAULT_RATE_LIMIT_WAIT_SECONDS:g})",
    )
    parser.add_argument(
        "--resume-skip-existing",
        action="store_true",
        help=(
            "In category/all mode, skip tasks that already have at least one run "
            "directory under OUTPUT_SUBDIR/<category>/<task_id>/."
        ),
    )

    args = parser.parse_args()
    models_config = None
    model_config = None
    if args.models_config:
        models_config_path = Path(args.models_config).expanduser()
        if not models_config_path.is_file():
            logger.error("Models config not found: %s", models_config_path)
            sys.exit(1)
        try:
            models_config = load_models_config(models_config_path.resolve())
            model_config = load_model_config(models_config, models_config_path.resolve())
        except (ValueError, json.JSONDecodeError) as exc:
            logger.error("Invalid models config: %s", exc)
            sys.exit(1)

    selected_model = args.model or (
        model_config.model_alias_for("openclaw_agent", DEFAULT_MODEL) if model_config else DEFAULT_MODEL
    )
    if not selected_model:
        logger.error("--model is required when --models-config does not define moduleModels.openclaw_agent")
        sys.exit(1)

    lobster = None
    if args.lobster_workspace:
        if not args.lobster_name:
            logger.error("--lobster-workspace requires --lobster-name")
            sys.exit(1)
        workspace = Path(args.lobster_workspace).expanduser()
        if not workspace.is_dir():
            logger.error("Lobster workspace not found: %s", workspace)
            sys.exit(1)
        env_keys = [k.strip() for k in args.lobster_env.split(",") if k.strip()] if args.lobster_env else []
        lobster = {
            "name": args.lobster_name,
            "workspace": str(workspace.resolve()),
            "env": env_keys,
        }
        logger.info("Lobster mode: %s (workspace=%s, env_keys=%s)",
                     lobster["name"], lobster["workspace"], lobster["env"])

    if args.task:
        task_file = Path(args.task)
        if not task_file.exists():
            logger.error("File not found: %s", task_file)
            sys.exit(1)
        task = parse_task_md(task_file)
        logger.info("Single task mode: %s", task["task_id"])
        run_single_task(
            task,
            selected_model,
            lobster=lobster,
            models_config=models_config,
            thinking=args.thinking,
            skill_dir=args.skill_dir,
            rate_limit_retries=args.rate_limit_retries,
            rate_limit_wait_seconds=args.rate_limit_wait_seconds,
        )
        return
    if args.category.lower() == "all":
        categories = ALL_CATEGORIES
    else:
        categories = [args.category]

    all_results: list[dict] = []
    safe_model_name = re.sub(r'[^a-zA-Z0-9.\-_]', '_', selected_model)

    for category in categories:
        category_dir = TASKS_DIR / category
        if not category_dir.exists():
            logger.error("Category directory not found: %s", category_dir)
            continue

        task_files = sorted(category_dir.glob("*task_*.md"))
        if not task_files:
            logger.error("No task_*.md files found in: %s", category_dir)
            continue

        logger.info("Category: %s, %d tasks, parallelism: %d",
                    category, len(task_files), args.parallel)

        tasks = []
        for tf in task_files:
            try:
                tasks.append(parse_task_md(tf))
            except Exception as exc:
                logger.error("Parse failed %s: %s", tf, exc)

        if not tasks:
            continue

        if args.resume_skip_existing:
            tasks, skipped_tasks = partition_tasks_by_existing_runs(tasks, OUTPUT_DIR)
            for task, runs in skipped_tasks:
                logger.info(
                    "[%s] Resume skip existing output (%d run(s), latest: %s)",
                    task["task_id"],
                    len(runs),
                    runs[-1],
                )
            logger.info(
                "Resume skip existing: category=%s total=%d skipped=%d pending=%d output=%s",
                category,
                len(tasks) + len(skipped_tasks),
                len(skipped_tasks),
                len(tasks),
                OUTPUT_DIR,
            )

        if not tasks:
            logger.info("No pending tasks for category %s", category)
            continue

        results: list[dict] = []
        if args.parallel <= 1:
            for task in tasks:
                results.append(
                    run_single_task(
                        task,
                        selected_model,
                        lobster=lobster,
                        models_config=models_config,
                        thinking=args.thinking,
                        skill_dir=args.skill_dir,
                        rate_limit_retries=args.rate_limit_retries,
                        rate_limit_wait_seconds=args.rate_limit_wait_seconds,
                    )
                )
        else:
            with ThreadPoolExecutor(max_workers=args.parallel) as pool:
                futures = {
                    pool.submit(
                        run_single_task,
                        task,
                        selected_model,
                        lobster=lobster,
                        thinking=args.thinking,
                        models_config=models_config,
                        skill_dir=args.skill_dir,
                        rate_limit_retries=args.rate_limit_retries,
                        rate_limit_wait_seconds=args.rate_limit_wait_seconds,
                    ): task["task_id"]
                    for task in tasks
                }
                for future in as_completed(futures):
                    tid = futures[future]
                    try:
                        results.append(future.result())
                    except Exception as exc:
                        logger.error("[%s] Thread exception: %s", tid, exc)
                        results.append({"task_id": tid, "scores": {}, "error": str(exc)})

        summary_label = f"{lobster['name']}_{safe_model_name}" if lobster else safe_model_name
        print_summary(results, category, OUTPUT_DIR, summary_label)
        all_results.extend(results)

    if len(categories) > 1 and all_results:
        summary_label = f"{lobster['name']}_{safe_model_name}" if lobster else safe_model_name
        print_global_summary(all_results, OUTPUT_DIR, summary_label)

if __name__ == "__main__":
    main()
