from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skilllift_eval.model_endpoints import ModelEndpointProfile
from skilllift_eval.runners.wildclaw import WildClawRunSettings, WildClawRunner, resolve_task_path
from skilllift_eval.schemas import TaskRunRecord
from skilllift_eval.runners.skilllift_thresholds import probability_param


_AGENTCLAW_ROOT = Path(__file__).resolve().parents[2] / "skilllift"
_BOUNDED_EDITS_SRC = Path(__file__).resolve().parents[2] / "packages" / "bounded-edits" / "src"
for _source_root in (_AGENTCLAW_ROOT, _BOUNDED_EDITS_SRC):
    if str(_source_root) not in sys.path:
        sys.path.insert(0, str(_source_root))

from skilllift_eval.runners.bounded_edits_skill_generator import (  # noqa: E402
    BoundedEditsSkillGenerator,
)


SOURCE = "skilllift_skilllift_single_task"
TASK_SAMPLE_POLICY_ID = "single_task_portfolio_skilllift"


@dataclass(frozen=True)
class SkillLiftWildClawRunSettings:
    run_root: Path
    project_root: Path
    skilllift_root: Path
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
    round_budget: str
    token_budget: str
    tasks_mode: str
    task_filter: str
    algorithm_params: dict[str, Any]


@dataclass(frozen=True)
class SkillLiftWildClawRunResult:
    records: list[TaskRunRecord]
    summary: dict[str, Any]
    resume: dict[str, int]


class SkillLiftWildClawRunner:
    def __init__(
        self,
        *,
        wildclaw_runner: WildClawRunner | None = None,
        planner: Any | None = None,
        generator: Any | None = None,
        verifier: Any | None = None,
    ) -> None:
        self._wildclaw_runner = wildclaw_runner or WildClawRunner()
        self._planner = planner
        self._generator = generator
        self._verifier = verifier

    def run(self, settings: SkillLiftWildClawRunSettings) -> SkillLiftWildClawRunResult:
        _validate_settings(settings)
        settings.run_root.mkdir(parents=True, exist_ok=True)
        _ensure_skilllift_importable(settings.skilllift_root)

        from skilllift.adapters.wildclawbench import WildClawPortfolioAdapter
        from skilllift.llm_client import LLMClient
        from skilllift.coordinator import CoordinatorConfig
        from skilllift.portfolio import LLMRubricatorPlanner
        from skilllift.portfolio import PortfolioStore
        from skilllift import SkillLiftPortfolioCoordinator

        task_path = resolve_task_path(settings.wildclaw_root, settings.task_filter)
        task_id = _task_id(task_path)
        store = PortfolioStore(settings.run_root)
        before_physical = store.metrics(task_id)[1]
        before_records = _load_committed_records(settings.run_root, task_id)
        before_record_ids = {record.raw_output_path for record in before_records}
        params = dict(settings.algorithm_params)
        threshold_warnings: list[dict[str, Any]] = []

        def record_threshold_warning(payload: dict[str, Any]) -> None:
            threshold_warnings.append(payload)

        for threshold_key, default in (
            ("terminal_reward", 1.0),
            ("mode_a_min_score_threshold", 0.85),
            ("rank_alignment_threshold", 0.9),
            ("oracle_threshold", 0.9),
            ("global_success_threshold", 0.9),
        ):
            params[threshold_key] = probability_param(
                params,
                threshold_key,
                default,
                warning_sink=record_threshold_warning,
            )
        if threshold_warnings:
            _append_jsonl(settings.run_root / "threshold_warnings.jsonl", threshold_warnings)
        api_key = settings.model_endpoint.api_key or os.environ.get(settings.model_endpoint.api_key_env or "")
        if not api_key:
            raise ValueError(f"missing API key source for {settings.model_endpoint.model_label}")

        def settings_factory(run_root: Path) -> WildClawRunSettings:
            return WildClawRunSettings(
                run_root=run_root,
                wildclaw_root=settings.wildclaw_root,
                model_endpoint=settings.model_endpoint,
                matrix_cell_id=settings.matrix_cell_id,
                model_label=settings.model_label,
                endpoint_config_hash=settings.endpoint_config_hash,
                algorithm_param_hash=settings.algorithm_param_hash,
                benchmark_source_hash=settings.benchmark_source_hash,
                task_set_hash=settings.task_set_hash,
                run_config_hash=settings.run_config_hash,
                evaluation_mode=settings.evaluation_mode,
                context_budget_profile=settings.context_budget_profile,
                visible_feedback_policy_id=settings.visible_feedback_policy_id,
                oracle_call_budget=settings.oracle_call_budget,
                task_sample_policy_id=TASK_SAMPLE_POLICY_ID,
                round_budget=settings.round_budget,
                token_budget=settings.token_budget,
                tasks_mode="single",
                task_filter=settings.task_filter,
                thinking=str(params.get("thinking", "high")),
                rate_limit_retries=int(params.get("oracle_rate_limit_retries", 0)),
                rate_limit_wait_seconds=float(params.get("oracle_rate_limit_wait_seconds", 0.0)),
            )

        adapter = WildClawPortfolioAdapter(
            wildclaw_root=settings.wildclaw_root,
            run_root=settings.run_root,
            task_filter=settings.task_filter,
            settings_factory=settings_factory,
            runner=self._wildclaw_runner,
            config_hash=_stable_hash(
                {
                    "run_config_hash": settings.run_config_hash,
                    "algorithm_param_hash": settings.algorithm_param_hash,
                    "benchmark_source_hash": settings.benchmark_source_hash,
                    "task_set_hash": settings.task_set_hash,
                }
            ),
            terminal_threshold=float(params.get("terminal_reward", 1.0)),
        )
        planner = self._planner
        generator = self._generator
        verifier_client = self._verifier
        if planner is None or generator is None:
            model = settings.model_endpoint.provider_model_id
            planner_client = LLMClient(
                settings.model_endpoint.base_url,
                api_key,
                model,
                role="rubricator",
                usage_callback=lambda event: _append_framework_usage(settings.run_root, event),
                use_stream=settings.model_endpoint.stream,
            )
            generator_client = LLMClient(
                settings.model_endpoint.base_url,
                api_key,
                model,
                role="skill_generator",
                usage_callback=lambda event: _append_framework_usage(settings.run_root, event),
                use_stream=settings.model_endpoint.stream,
            )
            verifier_client = verifier_client or LLMClient(
                settings.model_endpoint.base_url,
                api_key,
                model,
                role="verifier",
                usage_callback=lambda event: _append_framework_usage(settings.run_root, event),
                use_stream=settings.model_endpoint.stream,
            )
            max_chars = int(params["max_context_chars"]) if params.get("max_context_chars") else None
            planner = planner or LLMRubricatorPlanner(planner_client, max_input_chars=max_chars)
            generator = generator or BoundedEditsSkillGenerator(generator_client, max_input_chars=max_chars)

        result = SkillLiftPortfolioCoordinator(
            adapter=adapter,
            planner=planner,
            generator=generator,
            store=store,
            config=CoordinatorConfig(
                candidate_concurrency=int(params.get("candidate_concurrency", 2)),
                mode_a_iters=int(params.get("mode_a_iters", 2)),
                mode_b_iters=int(params.get("mode_b_iters", 2)),
                mode_a_min_score_threshold=float(params.get("mode_a_min_score_threshold", 0.85)),
                rank_alignment_threshold=float(params.get("rank_alignment_threshold", 0.9)),
                max_context_chars=int(params.get("max_context_chars", 120_000)),
            ),
            verifier_client=verifier_client,
        ).evolve(task_id)
        records = _load_committed_records(settings.run_root, task_id)
        new_record_count = sum(record.raw_output_path not in before_record_ids for record in records)
        new_physical = max(0, result.physical_attempts - before_physical)
        failed_attempts = max(0, new_physical - new_record_count)
        resume = {
            "skipped": len(before_records),
            "rerun": failed_attempts,
            "run": new_physical,
            "failed": failed_attempts,
        }
        summary = _cell_summary(settings, result, records, task_path)
        _write_json(settings.run_root / "cell_summary.json", summary)
        _write_json(settings.run_root / "task_run_records.json", [record.to_dict() for record in records])
        return SkillLiftWildClawRunResult(records, summary, resume)


def _validate_settings(settings: SkillLiftWildClawRunSettings) -> None:
    if settings.tasks_mode != "single":
        raise ValueError("CoEvo WildClawBench runner supports only --tasks.mode single")
    if not settings.task_filter:
        raise ValueError("CoEvo WildClawBench runner requires a task_filter")
    if settings.evaluation_mode != "native_end_to_end":
        raise ValueError("CoEvo WildClawBench runner supports only native_end_to_end")


def _cell_summary(
    settings: SkillLiftWildClawRunSettings,
    result: Any,
    records: list[TaskRunRecord],
    task_path: Path,
) -> dict[str, Any]:
    score = result.final_score if result.final_score is not None else result.adaptation_reward
    succeeded = score is not None
    return {
        "matrix_cell_id": settings.matrix_cell_id,
        "baseline": "skilllift",
        "benchmark": "wildclawbench",
        "model_label": settings.model_label,
        "evaluation_mode": settings.evaluation_mode,
        "context_budget_profile": settings.context_budget_profile,
        "expected_count": 1,
        "succeeded_count": int(succeeded),
        "completion_ratio": 1.0 if succeeded else 0.0,
        "status": "SUCCEEDED" if succeeded else "FAILED",
        "mean_score": score,
        "best_oracle_score": result.adaptation_reward,
        "last_rank_alignment": None,
        "oracle_runs_count": result.physical_attempts,
        "oracle_logical_cells": result.logical_cells,
        "oracle_succeeded_count": len(records),
        "skilllift_exp_dir": str((settings.run_root / "tasks" / result.task_id).resolve()),
        "task_path": str(task_path.resolve()),
        "artifact": str(settings.run_root.resolve()),
        "source": SOURCE,
        "cohort": result.cohort,
        "stop_reason": result.stop_reason,
        "winner_portfolio_hash": result.winner_portfolio.tree_hash,
        "winner_portfolio_root": str(result.winner_portfolio.root),
        "final_rewards": list(result.final_rewards),
    }


def _append_framework_usage(run_root: Path, event: dict[str, Any]) -> None:
    usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
    path = run_root / "usage" / "usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "usage_record_id": f"framework-{event['role']}-{event['request_id']}",
        "source": "framework",
        "role": event["role"],
        "phase": "evolve",
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "cost_usd": usage.get("cost_usd"),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = payload if isinstance(payload, list) else [payload]
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _task_id(path: Path) -> str:
    match = re.search(r"^id:\s*(.+?)\s*$", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    return match.group(1).strip() if match else path.stem


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _ensure_skilllift_importable(skilllift_root: Path) -> None:
    path = str(skilllift_root.resolve())
    if path not in sys.path:
        sys.path.insert(0, path)


def _provider_id(endpoint: ModelEndpointProfile) -> str:
    if endpoint.model_label.startswith("opus") or "claude" in endpoint.provider_model_id.lower():
        return "claude"
    if endpoint.model_label.startswith("gpt") or "gpt" in endpoint.provider_model_id.lower():
        return "openai"
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", endpoint.model_label).strip("-") or "custom"


def _model_alias(endpoint: ModelEndpointProfile) -> str:
    return f"{_provider_id(endpoint)}/{endpoint.provider_model_id}"


def _redacted_models_config(config: dict[str, Any], endpoint: ModelEndpointProfile) -> dict[str, Any]:
    redacted = json.loads(json.dumps(config))
    for provider in redacted.get("providers", {}).values():
        provider.pop("apiKey", None)
        if endpoint.api_key_env:
            provider["apiKeyEnv"] = endpoint.api_key_env
    return redacted


def _write_skilllift_model_configs(settings: Any) -> tuple[Path, Path]:
    config_dir = settings.run_root / "skilllift_model_configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    provider_id = _provider_id(settings.model_endpoint)
    alias = _model_alias(settings.model_endpoint)
    api_key = (
        f"${{{settings.model_endpoint.api_key_env}}}"
        if settings.model_endpoint.api_key_env
        else settings.model_endpoint.api_key
    )
    if not api_key:
        raise ValueError(f"missing API key source for {settings.model_endpoint.model_label}")
    provider = {
        provider_id: {
            "baseUrl": settings.model_endpoint.base_url,
            "apiKey": api_key,
            "api": settings.model_endpoint.provider,
            "timeout": settings.model_endpoint.timeout_seconds,
            "maxRetries": settings.model_endpoint.max_retries,
            "models": [{"id": settings.model_endpoint.provider_model_id, "name": alias}],
        }
    }
    openclaw = {
        "mode": "merge",
        "moduleModels": {"openclaw_agent": alias, "judge": alias, "oracle": alias},
        "providers": provider,
    }
    framework = {
        "mode": "merge",
        "moduleModels": {
            "skill_generator": alias,
            "rubricator": alias,
            "verifier": alias,
        },
        "providers": provider,
    }
    openclaw_path = config_dir / "openclaw_models_config.json"
    framework_path = config_dir / "framework_models_config.json"
    _write_json(openclaw_path, openclaw)
    _write_json(framework_path, framework)
    _write_json(
        config_dir / "models_config.redacted.json",
        _redacted_models_config(openclaw, settings.model_endpoint),
    )
    _write_json(
        config_dir / "framework_models_config.redacted.json",
        _redacted_models_config(framework, settings.model_endpoint),
    )
    return framework_path, openclaw_path


def _first_runtime_skill_dir(loader_manifest_path: str) -> str:
    manifest = _read_json(Path(loader_manifest_path))
    dirs = manifest.get("runtime_skill_dirs") or []
    return str(dirs[0]) if dirs else ""


def _feedback_summary(record: TaskRunRecord, score: dict[str, Any]) -> str:
    if record.status == "succeeded":
        return f"skilllift_eval WildClawBench run succeeded with score={score.get('score')}"
    return str(score.get("error_summary") or record.error_summary or "skilllift_eval WildClawBench run failed")


def _short_task_id(task_filter: str) -> str:
    return _normalize_task_filter(task_filter).replace("-", "_")


def _normalize_task_filter(value: str) -> str:
    return value.strip().replace("—", "-").replace("–", "-").replace("_", "-").lower()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_committed_records(run_root: Path, task_id: str) -> list[TaskRunRecord]:
    records: dict[str, TaskRunRecord] = {}
    marker_root = run_root / "adapter_artifacts" / task_id
    for marker in sorted(marker_root.glob("*/trial_result.json")):
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if payload.get("task_id") != task_id:
            raise ValueError(f"WildClaw trial marker task mismatch: {marker}")
        record_path = Path(str(payload.get("task_run_record_path") or ""))
        record = TaskRunRecord.from_dict(json.loads(record_path.read_text(encoding="utf-8")))
        if record.task_id != task_id or record.skill_hash != payload.get("portfolio_hash"):
            raise ValueError(f"WildClaw committed record identity mismatch: {record_path}")
        if hashlib.sha256(Path(record.score_path).read_bytes()).hexdigest() != payload.get("result_hash"):
            raise ValueError(f"WildClaw committed score hash mismatch: {record.score_path}")
        existing = records.get(record.raw_output_path)
        if existing is not None and existing.to_dict() != record.to_dict():
            raise ValueError(f"conflicting WildClaw record identity: {record.raw_output_path}")
        records[record.raw_output_path] = record
    return sorted(records.values(), key=lambda record: (record.started_at or "", record.raw_output_path))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
