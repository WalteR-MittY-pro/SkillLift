from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .adapters.wildclawbench import (
    build_run_batch_command,
    compute_models_config_hash,
    compute_skill_package_hash,
    contains_rate_limit_signal,
    preflight_validate_oracle_run,
    resolve_wildclaw_output_dir,
    run_wildclawbench_task,
)
from .errors import OracleAdapterError
from .experiment_trace import load_public_score, summarize_oracle_output
from .persistence import ExperimentStore, read_json, write_json, write_json_atomic
from .ranking import assign_tie_aware_ranks, rank_oracle_scores
from .schemas import SkillLiftConfig, EvoSkill, OracleFeedback, OracleScore, SkillKey, TaskSpec
from .baselines.skill_package import evo_skill_to_runtime_dir
from .task_loader import parse_task_metadata, rewrite_task_id_for_skill


def evaluate_skill_batch(
    task: TaskSpec,
    skills: dict[SkillKey, EvoSkill],
    config: SkillLiftConfig,
    store: ExperimentStore,
) -> dict[SkillKey, OracleScore]:
    items = sorted(skills.items())
    if config.oracle_concurrency <= 1 or len(items) <= 1:
        scores = {
            key: evaluate_one_skill(task, skill, config, store)
            for key, skill in items
        }
    else:
        with ThreadPoolExecutor(max_workers=config.oracle_concurrency) as pool:
            results = pool.map(
                lambda item: (item[0], evaluate_one_skill(task, item[1], config, store)),
                items,
            )
        scores = dict(results)
    ranks = assign_tie_aware_ranks(
        {key: score.oracle_score for key, score in scores.items()}
    )
    for key in rank_oracle_scores(scores):
        scores[key].rank = ranks[key]
    return scores


def evaluate_one_skill(
    task: TaskSpec,
    skill: EvoSkill,
    config: SkillLiftConfig,
    store: ExperimentStore,
) -> OracleScore:
    cache_key = build_oracle_cache_key(task, skill, config)
    cached = load_cached_oracle_score(cache_key, store)
    if cached is not None:
        cached.feedback.metadata["cache_hit"] = True
        return cached

    task_path, skill_dir = inject_skill_for_oracle(task, skill, store, config)
    started_at = time.time()
    command = build_run_batch_command(task_path, config.model, config, skill_dir)
    manifest: dict[str, Any] = _base_manifest(task, task_path, skill, skill_dir, config, command, cache_key)

    try:
        preflight_validate_oracle_run(task_path, skill_dir, config)
    except OracleAdapterError as exc:
        score = _failed_score(skill.key, "preflight_validation", str(exc))
        manifest.update(_finish_manifest(started_at, None, "", "preflight_validation", 0, "", str(exc), False))
        write_oracle_run_manifest(manifest, store)
        return score

    result = run_wildclawbench_task(command, config)
    try:
        output_dir = resolve_wildclaw_output_dir(
            task_path,
            Path(config.output_root) if config.output_root else None,
            started_at,
            config,
        )
        score = load_oracle_score(output_dir, skill.key, config.oracle_threshold, config.oracle_feedback_level)
    except OracleAdapterError as exc:
        score = _failed_score(skill.key, "output_resolution", str(exc))
        output_dir = None
    stage = classify_oracle_failure_stage(result.returncode, output_dir, score.feedback, result.stdout, result.stderr)
    if stage != "success":
        score.oracle_pass = 0
        score.feedback.metadata["failure_type"] = stage
        if stage != "task_low_score" and score.oracle_score == 0.0:
            score.feedback.metadata["non_skill_failure"] = True
    manifest.update(_finish_manifest(started_at, output_dir, score.output_dir, stage, result.returncode, result.stdout, result.stderr, False))
    write_oracle_run_manifest(manifest, store)
    save_cached_oracle_score(cache_key, score, store)
    return score


def inject_skill_for_oracle(
    task: TaskSpec,
    skill: EvoSkill,
    store: ExperimentStore,
    config: SkillLiftConfig | None = None,
) -> tuple[Path, Path]:
    if not task.task_doc_path:
        raise OracleAdapterError("TaskSpec.task_doc_path is required for oracle execution")
    outer_dir = f"outer_{store.current_outer_round:03d}"
    skill_root = store.exp_dir / "oracle_skills" / outer_dir / skill.key.token()
    runtime_dir = evo_skill_to_runtime_dir(skill, skill_root)
    task_dir = store.exp_dir / "oracle_tasks" / outer_dir
    task_path = rewrite_task_id_for_skill(
        Path(task.task_doc_path),
        skill,
        task_dir,
        config.agent_timeout_override if config is not None else 0,
    )
    return task_path, runtime_dir


def load_oracle_score(output_dir: Path, skill: SkillKey, threshold: float, feedback_level: int) -> OracleScore:
    if not output_dir.exists():
        return _failed_score(skill, "output_resolution", f"output directory not found: {output_dir}")
    scores = load_public_score(output_dir)
    if not scores:
        feedback = summarize_oracle_output(output_dir, feedback_level)
        feedback.metadata["failure_type"] = "grader_missing_score"
        return OracleScore(skill, 0.0, 0, feedback=feedback, output_dir=str(output_dir))
    oracle_score = float(scores.get("overall_score", 0.0) or 0.0)
    feedback = summarize_oracle_output(output_dir, feedback_level)
    oracle_pass = 1 if oracle_score >= threshold else 0
    failure_type = "success" if oracle_pass else "task_low_score"
    feedback.metadata["failure_type"] = failure_type
    return OracleScore(skill, oracle_score, oracle_pass, feedback=feedback, output_dir=str(output_dir))


# Bump when scoring or feedback semantics change so stale cache entries
# written by an older logic version stop matching.
ORACLE_SCORING_VERSION = 1


def build_oracle_cache_key(task: TaskSpec, skill: EvoSkill, config: SkillLiftConfig) -> str:
    openclaw_models_config = config.openclaw_models_config_path()
    payload = {
        "scoring_version": ORACLE_SCORING_VERSION,
        "task": task.task_name,
        "model": config.model,
        "openclaw_models_config_hash": compute_models_config_hash(openclaw_models_config),
        "skill_package_hash": compute_skill_package_hash(skill),
        "oracle_threshold": config.oracle_threshold,
        # Both of these change the measured result or the cached feedback
        # text; leaving them out returned stale scores after overrides.
        "agent_timeout_override": int(config.agent_timeout_override),
        "oracle_feedback_level": int(config.oracle_feedback_level),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def load_cached_oracle_score(cache_key: str, store: ExperimentStore) -> OracleScore | None:
    path = store.exp_dir / "oracle_cache" / f"{cache_key}.json"
    if not path.exists():
        return None
    return OracleScore.from_dict(read_json(path))


def save_cached_oracle_score(cache_key: str, score: OracleScore, store: ExperimentStore) -> None:
    write_json_atomic(store.exp_dir / "oracle_cache" / f"{cache_key}.json", score)


def write_oracle_run_manifest(manifest: dict[str, Any], store: ExperimentStore) -> Path:
    key = manifest.get("skill_key", "unknown")
    root = store.exp_dir / "oracle_manifests" / f"outer_{store.current_outer_round:03d}"
    path = root / f"{key}_oracle_run_manifest.json"
    write_json(path, manifest)
    return path


def classify_oracle_failure_stage(
    returncode: int,
    output_dir: Path | None,
    feedback: OracleFeedback,
    stdout: str = "",
    stderr: str = "",
) -> str:
    if returncode != 0:
        return "run_batch_process"
    if str(feedback.metadata.get("failure_type") or "") == "success":
        # The grader already produced a passing score. Keyword hits in agent
        # logs must not override it: config echoes like "timeout=3600" or
        # "api_key_env=GLM_API_KEY" show up in perfectly healthy runs.
        return "success"
    combined = f"{stdout}\n{stderr}\n{feedback.summary}\n{feedback.stderr_excerpt}".lower()
    if contains_rate_limit_signal(combined):
        return "llm_rate_limited"
    if _contains_timeout_signal(combined):
        return "llm_or_agent_timeout"
    if _contains_llm_access_signal(combined):
        return "llm_access_failure"
    if output_dir is None:
        return "output_resolution"
    failure = str(feedback.metadata.get("failure_type") or "")
    if failure in {"grader_missing_score", "skill_load", "agent_execution", "task_low_score", "success"}:
        return failure
    if failure == "container_start":
        return "container_start"
    return "success"


def _contains_timeout_signal(text: str) -> bool:
    return any(token in text for token in ["timeout", "timed out", "read timed out", "deadline exceeded"])


_HTTP_ACCESS_CODE_RE = re.compile(r"\b(?:401|403)\b")


def _contains_llm_access_signal(text: str) -> bool:
    if _HTTP_ACCESS_CODE_RE.search(text):
        return True
    return any(
        token in text
        for token in [
            "unauthorized",
            "forbidden",
            "invalid api key",
            "api key",
            "llm call failed",
            "model setup failed",
            "no judge api config",
        ]
    )


def _failed_score(skill: SkillKey, stage: str, summary: str) -> OracleScore:
    return OracleScore(
        skill=skill,
        oracle_score=0.0,
        oracle_pass=0,
        feedback=OracleFeedback(summary=summary, metadata={"failure_type": stage}),
        output_dir="",
    )


def _base_manifest(
    task: TaskSpec,
    task_path: Path,
    skill: EvoSkill,
    skill_dir: Path,
    config: SkillLiftConfig,
    command: list[str],
    cache_key: str,
) -> dict[str, Any]:
    openclaw_models_config = config.openclaw_models_config_path()
    return {
        "skill_key": skill.key.token(),
        "task_path": str(task_path),
        "task_id": parse_task_metadata(task_path)["task_id"],
        "original_task_id": task.task_name,
        "skill_dir": str(skill_dir),
        "skill_package_hash": compute_skill_package_hash(skill),
        "model": config.model,
        "models_config": str(openclaw_models_config),
        "models_config_hash": compute_models_config_hash(openclaw_models_config),
        "openclaw_models_config": str(openclaw_models_config),
        "openclaw_models_config_hash": compute_models_config_hash(openclaw_models_config),
        "framework_models_config": str(config.framework_models_config_path()),
        "command": command,
        "cache_key": cache_key,
        "cache_hit": False,
        "cache_source": "",
    }


def _finish_manifest(
    started_at: float,
    output_dir: Path | None,
    output_dir_text: str,
    failure_stage: str,
    returncode: int,
    stdout: str,
    stderr: str,
    cache_hit: bool,
) -> dict[str, Any]:
    return {
        "started_at": started_at,
        "finished_at": time.time(),
        "returncode": returncode,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-2000:],
        "output_dir": str(output_dir) if output_dir else output_dir_text,
        "failure_stage": failure_stage,
        "cache_hit": cache_hit,
        "cache_source": "",
    }
