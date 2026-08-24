from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
import json
import os
import re

from skilllift_eval.benchmarks.wildclaw_feedback import sanitize_wildclaw_feedback
from skilllift_eval.benchmarks.wildclaw_public_view import wildclaw_public_task_view
from skilllift_eval.metrics import RoundMetric
from skilllift_eval.model_endpoints import ModelEndpointProfile
from skilllift_eval.runners.wildclaw_engine import run_batch
from skilllift_eval.runners.wildclaw_engine import task_parser
from skilllift_eval.schemas import LoadedSkillContext, SkillBundle, TaskRunRecord
from skilllift_eval.skills.wildclaw_loader import LOADER_VERSION, MANIFEST_SCHEMA_VERSION, WildClawBenchSkillLoader
from skilllift_eval.tokens import estimate_tokens, normalize_usage


RUNNER_VERSION = "wildclaw_runner_v1"
SCORE_PARSER_VERSION = "wildclaw_score_parser_v1"
TaskRunner = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class WildClawRunSettings:
    run_root: Path
    wildclaw_root: Path
    model_endpoint: ModelEndpointProfile
    matrix_cell_id: str
    model_label: str
    endpoint_config_hash: str
    algorithm_param_hash: str
    benchmark_source_hash: str
    task_set_hash: str
    run_config_hash: str
    evaluation_mode: str
    context_budget_profile: str
    visible_feedback_policy_id: str
    oracle_call_budget: str
    task_sample_policy_id: str
    round_budget: str
    token_budget: str
    tasks_mode: str
    task_filter: str | None = None
    round_id: str = "task4-smoke-06-01"
    round_type: str = "wildclaw_single_task_smoke"
    thinking: str = "high"
    rate_limit_retries: int = 0
    rate_limit_wait_seconds: float = 0.0
    baseline: str = "skilllift"
    timeout_seconds_override: int | None = None
    run_id_suffix: str | None = None


@dataclass(frozen=True)
class WildClawRunResult:
    records: list[TaskRunRecord]
    summary: dict[str, Any]
    resume: dict[str, int]


@dataclass(frozen=True)
class ArtifactRolloutRecord:
    run_id: str
    task_id: str
    status: str
    output_dir: str
    artifact_root: str
    loader_manifest_path: str
    raw_output_path: str
    error_summary: str | None = None
    usage: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WildClawRunner:
    def __init__(self, *, task_runner: TaskRunner | None = None) -> None:
        self._task_runner = task_runner

    def run(self, settings: WildClawRunSettings, bundle: SkillBundle) -> WildClawRunResult:
        _validate_settings(settings)
        settings.run_root.mkdir(parents=True, exist_ok=True)
        task_path = resolve_task_path(settings.wildclaw_root, settings.task_filter or "")
        task_id = _task_id_from_path(task_path)
        task_dir = settings.run_root / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        context = WildClawBenchSkillLoader(
            wildclaw_root=settings.wildclaw_root,
            artifact_root=task_dir / "loader",
        ).prepare(task_path, bundle)
        _write_json(task_dir / "skill_bundle.json", bundle.to_dict())
        return self._run_with_context(settings, task_id, task_dir, context, bundle)

    def run_portfolio(
        self,
        settings: WildClawRunSettings,
        portfolio_root: Path,
        *,
        skill_hash: str,
    ) -> WildClawRunResult:
        _validate_settings(settings)
        settings.run_root.mkdir(parents=True, exist_ok=True)
        task_path = resolve_task_path(settings.wildclaw_root, settings.task_filter or "")
        task_id = _task_id_from_path(task_path)
        task_dir = settings.run_root / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        context = WildClawBenchSkillLoader(
            wildclaw_root=settings.wildclaw_root,
            artifact_root=task_dir / "loader",
        ).prepare_portfolio(task_path, portfolio_root, skill_hash=skill_hash)
        _write_json(
            task_dir / "skill_portfolio.json",
            {"root": str(portfolio_root.resolve()), "skill_hash": skill_hash},
        )
        metric_bundle = SkillBundle(
            skill_bundle_id=f"portfolio-{skill_hash}",
            baseline="skilllift",
            benchmark_target="wildclawbench",
            granularity="task",
            skills=[],
            created_at=_utc_now(),
            skill_hash=skill_hash,
        )
        return self._run_with_context(settings, task_id, task_dir, context, metric_bundle)

    def _run_with_context(
        self,
        settings: WildClawRunSettings,
        task_id: str,
        task_dir: Path,
        context: LoadedSkillContext,
        metric_bundle: SkillBundle,
    ) -> WildClawRunResult:
        context.validate_for_runner("wildclawbench")
        _write_redacted_run_config(task_dir / "run_config.redacted.json", settings)
        models_config = _openclaw_models_config(settings.model_endpoint)
        _write_json(task_dir / "models_config.redacted.json", _redacted_models_config(models_config, settings.model_endpoint))

        started_at = _utc_now()
        raw_path = task_dir / "raw_output.json"
        score_path = task_dir / "score.json"
        sanitized_path = task_dir / "sanitized_feedback.json"
        public_view_path = task_dir / "public_task_view.json"
        try:
            task = _parse_generated_task(settings.wildclaw_root, context)
            if settings.timeout_seconds_override is not None:
                task["timeout_seconds"] = settings.timeout_seconds_override
            _write_json(public_view_path, wildclaw_public_task_view(task))
            result = self._run_task(settings, task, models_config)
            score = _score_payload(result)
            sanitized = sanitize_wildclaw_feedback(
                {
                    "task_id": task["task_id"],
                    "category": task.get("category"),
                    "score": score.get("score"),
                    "transcript": _transcript_for_feedback(result),
                    "automated_check": _automated_check_for_feedback(result),
                }
            )
            _write_json(raw_path, result)
            _write_json(score_path, score)
            _write_json(sanitized_path, sanitized)
            record = _record(settings, context, task, raw_path, score_path, started_at, score)
            _write_round_metric(settings, metric_bundle, context, task, score, result, task_dir)
            resume = {"skipped": 0, "rerun": 0, "run": 1, "failed": 0 if record.status == "succeeded" else 1}
            return WildClawRunResult([record], _cell_summary(settings, [record]), resume)
        except Exception as exc:
            error = {"status": "failed", "error_type": type(exc).__name__, "error_summary": str(exc)}
            _write_json(raw_path, error)
            _write_json(score_path, error)
            record = _record(settings, context, {"task_id": task_id, "category": "unknown"}, raw_path, score_path, started_at, error)
            resume = {"skipped": 0, "rerun": 0, "run": 1, "failed": 1}
            return WildClawRunResult([record], _cell_summary(settings, [record]), resume)

    def _run_task(self, settings: WildClawRunSettings, task: dict[str, Any], models_config: dict[str, Any]) -> dict[str, Any]:
        runner = self._task_runner or _default_task_runner
        kwargs = {
            "task": task,
            "model": _model_alias(settings.model_endpoint),
            "models_config": models_config,
            "thinking": settings.thinking,
            "rate_limit_retries": settings.rate_limit_retries,
            "rate_limit_wait_seconds": settings.rate_limit_wait_seconds,
        }
        if self._task_runner is None:
            kwargs["output_root"] = settings.run_root / "benchmark_output"
        return runner(
            **kwargs,
        )


class WildClawArtifactRunner:
    """Executes an agent and collects public artifacts without hidden grading."""

    def __init__(self, *, task_runner: TaskRunner | None = None) -> None:
        self._task_runner = task_runner

    def run(self, settings: WildClawRunSettings, bundle: SkillBundle) -> ArtifactRolloutRecord:
        _validate_settings(settings)
        settings.run_root.mkdir(parents=True, exist_ok=True)
        task_path = resolve_task_path(settings.wildclaw_root, settings.task_filter or "")
        task_id = _task_id_from_path(task_path)
        task_dir = settings.run_root / "tasks" / task_id
        context = WildClawBenchSkillLoader(
            wildclaw_root=settings.wildclaw_root,
            artifact_root=task_dir / "loader",
        ).prepare(task_path, bundle)
        context.validate_for_runner("wildclawbench")
        models_config = _openclaw_models_config(settings.model_endpoint)
        task = _parse_generated_task(settings.wildclaw_root, context)
        if settings.timeout_seconds_override is not None:
            task["timeout_seconds"] = settings.timeout_seconds_override
        raw_path = task_dir / "raw_output.json"
        try:
            runner = self._task_runner or _default_task_runner
            kwargs: dict[str, Any] = {
                "task": task,
                "model": _model_alias(settings.model_endpoint),
                "models_config": models_config,
                "thinking": settings.thinking,
                "rate_limit_retries": settings.rate_limit_retries,
                "rate_limit_wait_seconds": settings.rate_limit_wait_seconds,
                "grade": False,
            }
            if self._task_runner is None:
                kwargs["output_root"] = settings.run_root / "benchmark_output"
            result = runner(**kwargs)
            _write_json(raw_path, result)
            output_dir = Path(str(result.get("output_dir") or ""))
            artifact_root = output_dir / "task_output" / "workspace"
            error = str(result.get("error") or "").strip() or None
            if error is None and not artifact_root.is_dir():
                error = f"artifact output directory missing: {artifact_root}"
            record = ArtifactRolloutRecord(
                run_id=str(result.get("task_id") or f"artifact-{task_id}"),
                task_id=str(task.get("task_id") or task_id),
                status="failed" if error else "succeeded",
                output_dir=str(output_dir),
                artifact_root=str(artifact_root),
                loader_manifest_path=context.manifest_path,
                raw_output_path=str(raw_path.resolve()),
                error_summary=error,
                usage=result.get("usage") if isinstance(result.get("usage"), dict) else {},
            )
        except Exception as exc:
            error = {"status": "failed", "error_type": type(exc).__name__, "error_summary": str(exc)}
            _write_json(raw_path, error)
            record = ArtifactRolloutRecord(
                run_id=f"artifact-{task_id}",
                task_id=task_id,
                status="failed",
                output_dir="",
                artifact_root="",
                loader_manifest_path=context.manifest_path,
                raw_output_path=str(raw_path.resolve()),
                error_summary=str(exc),
                usage={},
            )
        _write_json(task_dir / "artifact_rollout_record.json", record.to_dict())
        return record


def resolve_task_path(wildclaw_root: Path, task_filter: str) -> Path:
    normalized = _normalize_task_filter(task_filter)
    tasks_root = wildclaw_root / "tasks"
    if not tasks_root.is_dir():
        raise ValueError(f"WildClawBench tasks directory not found: {tasks_root}")
    candidates = sorted(tasks_root.glob("*/*task_*.md"))
    for path in candidates:
        task_id = _task_id_from_path(path)
        if normalized in {_normalize_task_filter(task_id), _short_task_filter(path)}:
            return path.resolve()
    raise ValueError(f"WildClawBench task filter did not match exactly one task: {task_filter}")


def _validate_settings(settings: WildClawRunSettings) -> None:
    if settings.tasks_mode != "single":
        raise ValueError("WildClawRunner currently supports only --tasks.mode single")
    if not settings.task_filter:
        raise ValueError("tasks_mode single requires task_filter")
    if settings.evaluation_mode != "native_end_to_end":
        raise ValueError("WildClawRunner smoke only supports native_end_to_end")


def _default_task_runner(**kwargs: Any) -> dict[str, Any]:
    return run_batch.run_single_task(**kwargs)


def _parse_generated_task(wildclaw_root: Path, context: LoadedSkillContext) -> dict[str, Any]:
    task_parser.ROOT_DIR = wildclaw_root.resolve()
    task = run_batch.parse_task_md(Path(context.generated_task_path or ""))
    if context.original_task_path:
        task["category"] = Path(context.original_task_path).parent.name
    task["skills_path"] = str(Path(context.host_skills_parent_dir or wildclaw_root / "skills").resolve())
    return task


def _task_id_from_path(path: Path) -> str:
    match = re.search(r"^id:\s*(.+?)\s*$", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    return match.group(1).strip() if match else path.stem


def _short_task_filter(path: Path) -> str:
    match = re.match(r"(\d+)_.*?_task_(\d+)", path.stem)
    if not match:
        return _normalize_task_filter(path.stem)
    return f"{int(match.group(1)):02d}-{int(match.group(2)):02d}"


def _normalize_task_filter(value: str) -> str:
    return value.strip().replace("—", "-").replace("–", "-").replace("_", "-").lower()


def _openclaw_models_config(endpoint: ModelEndpointProfile) -> dict[str, Any]:
    api_key = endpoint.api_key or os.environ.get(endpoint.api_key_env or "")
    if not api_key:
        raise ValueError(f"missing API key env {endpoint.api_key_env} for {endpoint.model_label}")
    provider_id = _provider_id(endpoint)
    alias = _model_alias(endpoint)
    return {
        "mode": "merge",
        "moduleModels": {
            "openclaw_agent": alias,
            "judge": alias,
            "oracle": alias,
        },
        "providers": {
            provider_id: {
                "baseUrl": endpoint.base_url,
                "apiKey": api_key,
                "api": endpoint.provider,
                "models": [
                    {
                        "id": endpoint.provider_model_id,
                        "name": alias,
                    }
                ],
            }
        },
    }


def _redacted_models_config(config: dict[str, Any], endpoint: ModelEndpointProfile) -> dict[str, Any]:
    redacted = json.loads(json.dumps(config))
    for provider in redacted.get("providers", {}).values():
        provider.pop("apiKey", None)
        if endpoint.api_key_env:
            provider["apiKeyEnv"] = endpoint.api_key_env
    return redacted


def _provider_id(endpoint: ModelEndpointProfile) -> str:
    if endpoint.model_label.startswith("opus") or "claude" in endpoint.provider_model_id.lower():
        return "claude"
    if endpoint.model_label.startswith("gpt") or "gpt" in endpoint.provider_model_id.lower():
        return "openai"
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", endpoint.model_label).strip("-") or "custom"


def _model_alias(endpoint: ModelEndpointProfile) -> str:
    provider_id = _provider_id(endpoint)
    return f"{provider_id}/{endpoint.provider_model_id}"


def _score_payload(result: dict[str, Any]) -> dict[str, Any]:
    scores = result.get("scores") if isinstance(result.get("scores"), dict) else {}
    overall = scores.get("overall_score", scores.get("score"))
    status = "failed" if result.get("error") else "succeeded"
    return {
        "status": status,
        "score": float(overall) if overall is not None and not result.get("error") else 0.0,
        "overall_score": float(overall) if overall is not None else 0.0,
        "scores": scores,
        "error_summary": result.get("error"),
        "output_dir": result.get("output_dir"),
        "usage": result.get("usage") or {},
    }


def _transcript_for_feedback(result: dict[str, Any]) -> list[dict[str, str]]:
    output_dir = Path(str(result.get("output_dir") or ""))
    agent_log = output_dir / "agent.log"
    if not agent_log.exists():
        return []
    return [{"visibility": "public_stream", "stdout": agent_log.read_text(encoding="utf-8", errors="ignore")[-1000:]}]


def _automated_check_for_feedback(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("error"):
        return {"category": "runner_error", "stderr": str(result["error"])}
    return {"category": "completed", "stdout": "benchmark runner completed"}


def _record(
    settings: WildClawRunSettings,
    context: LoadedSkillContext,
    task: dict[str, Any],
    raw_path: Path,
    score_path: Path,
    started_at: str,
    score: dict[str, Any],
) -> TaskRunRecord:
    usage = score.get("usage") if isinstance(score.get("usage"), dict) else {}
    public_task = wildclaw_public_task_view(task)
    record = TaskRunRecord(
        run_id="__".join(
            item
            for item in [
                settings.matrix_cell_id,
                str(task.get("task_id", "unknown-task")),
                settings.run_id_suffix,
            ]
            if item
        ),
        matrix_cell_id=settings.matrix_cell_id,
        baseline=settings.baseline,
        benchmark="wildclawbench",
        model_label=settings.model_label,
        model_endpoint_id=settings.model_endpoint.model_endpoint_id,
        provider_model_id=settings.model_endpoint.provider_model_id,
        endpoint_config_hash=settings.endpoint_config_hash,
        algorithm_param_hash=settings.algorithm_param_hash,
        benchmark_source_hash=settings.benchmark_source_hash,
        task_set_hash=settings.task_set_hash,
        loader_version=LOADER_VERSION,
        loader_manifest_schema_version=MANIFEST_SCHEMA_VERSION,
        score_parser_version=SCORE_PARSER_VERSION,
        run_config_hash=settings.run_config_hash,
        task_id=str(task.get("task_id", "unknown-task")),
        domain=str(task.get("category", "")),
        skill_hash=context.skill_hash,
        prompt_hash=None,
        loader_manifest_path=context.manifest_path,
        raw_output_path=str(raw_path.resolve()),
        score_path=str(score_path.resolve()),
        status="succeeded" if score.get("status") != "failed" else "failed",
        started_at=started_at,
        finished_at=_utc_now(),
        error_summary=score.get("error_summary"),
        token_estimate=estimate_tokens(json.dumps(public_task, ensure_ascii=False)),
        token_actual=usage.get("total_tokens", "unavailable"),
        cost_actual=usage.get("cost_usd", usage.get("cost_actual", "unavailable")),
        evaluation_mode=settings.evaluation_mode,
        context_budget_profile=settings.context_budget_profile,
        visible_feedback_policy_id=settings.visible_feedback_policy_id,
        oracle_calls_used=1,
        oracle_call_budget=settings.oracle_call_budget,
        task_sample_policy_id=settings.task_sample_policy_id,
        round_budget=settings.round_budget,
        token_budget=settings.token_budget,
        context_input_chars=len(json.dumps(public_task, ensure_ascii=False)),
        context_input_tokens_estimate=estimate_tokens(json.dumps(public_task, ensure_ascii=False)),
        context_truncated=False,
    )
    _write_json(raw_path.parent / "task_run_record.json", record.to_dict())
    return record


def _write_round_metric(
    settings: WildClawRunSettings,
    bundle: SkillBundle,
    context: LoadedSkillContext,
    task: dict[str, Any],
    score: dict[str, Any],
    result: dict[str, Any],
    task_dir: Path,
) -> None:
    usage = normalize_usage(result.get("usage") if isinstance(result.get("usage"), dict) else None)
    metric = RoundMetric(
        matrix_cell_id=settings.matrix_cell_id,
        baseline=settings.baseline,
        benchmark="wildclawbench",
        model_label=settings.model_label,
        model_endpoint_id=settings.model_endpoint.model_endpoint_id,
        provider_model_id=settings.model_endpoint.provider_model_id,
        round_id=settings.round_id,
        round_type=settings.round_type,
        skill_hash=context.skill_hash,
        prompt_hash=None,
        token_usage=usage,
        evaluation_mode=settings.evaluation_mode,
        context_budget_profile=settings.context_budget_profile,
        visible_feedback_policy_id=settings.visible_feedback_policy_id,
        oracle_calls_used=1,
        oracle_call_budget=settings.oracle_call_budget,
        task_sample_policy_id=settings.task_sample_policy_id,
        round_budget=settings.round_budget,
        token_budget=settings.token_budget,
        context_input_tokens_estimate=estimate_tokens(json.dumps(wildclaw_public_task_view(task), ensure_ascii=False)),
        context_truncated=False,
        algorithm_param_profile="paper_default",
        created_at=_utc_now(),
        eval_score=score.get("score"),
        artifact_paths={
            "skill_bundle_id": bundle.skill_bundle_id,
            "loader_manifest": context.manifest_path,
            "benchmark_output": str(result.get("output_dir", "")),
        },
    )
    with (settings.run_root / "round_metrics.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(metric.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")


def _cell_summary(settings: WildClawRunSettings, records: list[TaskRunRecord]) -> dict[str, Any]:
    scores = []
    for record in records:
        if record.status != "succeeded":
            continue
        payload = json.loads(Path(record.score_path).read_text(encoding="utf-8"))
        scores.append(float(payload["score"]))
    expected = len(records)
    succeeded = len(scores)
    completion = succeeded / expected if expected else 0.0
    status = "SUCCEEDED" if completion == 1.0 else "PARTIAL" if completion >= 0.8 else "FAILED"
    return {
        "matrix_cell_id": settings.matrix_cell_id,
        "baseline": settings.baseline,
        "benchmark": "wildclawbench",
        "model_label": settings.model_label,
        "evaluation_mode": settings.evaluation_mode,
        "context_budget_profile": settings.context_budget_profile,
        "expected_count": expected,
        "succeeded_count": succeeded,
        "completion_ratio": completion,
        "status": status,
        "mean_score": sum(scores) / len(scores) if scores and completion >= 0.8 else None,
        "artifact": str(settings.run_root.resolve()),
        "source": "single_task_smoke",
    }


def _write_redacted_run_config(path: Path, settings: WildClawRunSettings) -> None:
    data = asdict(settings)
    data["run_root"] = str(settings.run_root.resolve())
    data["wildclaw_root"] = str(settings.wildclaw_root.resolve())
    data["model_endpoint"] = settings.model_endpoint.redacted()
    _write_json(path, data)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
