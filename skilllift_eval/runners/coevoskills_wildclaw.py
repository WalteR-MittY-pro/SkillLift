from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skilllift_eval.model_endpoints import ModelEndpointProfile
from skilllift_eval.runners.wildclaw import (
    ArtifactRolloutRecord,
    WildClawArtifactRunner,
    WildClawRunSettings,
    WildClawRunner,
    resolve_task_path,
)
from skilllift_eval.schemas import SkillBundle, TaskRunRecord
from skilllift_eval.token_accounting import (
    aggregate_usage,
    load_reference_budget,
    task_key_from_path,
    write_skilllift_reference_usage,
    write_json as write_token_json,
)


SOURCE = "coevoskills_paper_algorithm_wildclaw_adaptation"
TASK_SAMPLE_POLICY_ID = "single_task_coevoskills_wildclaw_v1"
MAX_EXCERPT_CHARS = 8_000
MAX_SNAPSHOT_TEXT_BYTES = 2_000_000
TEXT_SUFFIXES = {
    ".txt", ".md", ".json", ".jsonl", ".csv", ".tsv", ".xml", ".html",
    ".yaml", ".yml", ".py", ".js", ".ts", ".tex", ".log",
}
SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*[^\s,;]+"
)


@dataclass(frozen=True)
class CoEvoSkillsWildClawRunSettings:
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
class CoEvoSkillsWildClawRunResult:
    records: list[TaskRunRecord]
    artifact_records: list[ArtifactRolloutRecord]
    summary: dict[str, Any]
    resume: dict[str, int]


class _EvolutionTokenBudget:
    def __init__(
        self,
        run_root: Path,
        *,
        generator: Any,
        verifier: Any,
        artifact_backend: Any,
        oracle_backend: Any,
        reference: dict[str, Any] | None,
        framework_token_offset: int = 0,
        framework_request_offset: int = 0,
    ) -> None:
        self.run_root = run_root
        self.framework_clients = _unique_clients(generator, verifier)
        self.artifact_backend = artifact_backend
        self.oracle_backend = oracle_backend
        self.framework_token_offset = framework_token_offset
        self.framework_request_offset = framework_request_offset
        self.reference = reference or {
            "task_key": "",
            "reference_path": "",
            "base_tokens": 0,
            "multiplier": 0.0,
            "limit_tokens": 0,
        }
        self.last_payload: dict[str, Any] = {}

    def checkpoint(self, *, turns_completed: int, stage: str) -> bool:
        framework_total = self.framework_token_offset + sum(
            _nonnegative_int(getattr(client, "total_tokens", 0))
            for client in self.framework_clients
        )
        framework_requests = self.framework_request_offset + sum(
            _nonnegative_int(getattr(client, "request_count", 0))
            for client in self.framework_clients
        )
        artifact_usage = aggregate_usage(
            getattr(record, "usage", None)
            for record in self.artifact_backend.records
        )
        private_usage = aggregate_usage(
            _task_run_usage(record)
            for record in self.oracle_backend.evolution_records
        )
        oracle_usage = aggregate_usage([artifact_usage, private_usage])
        used_tokens = framework_total + oracle_usage["total_tokens"]
        limit_tokens = _nonnegative_int(self.reference.get("limit_tokens"))
        exhausted = limit_tokens > 0 and used_tokens >= limit_tokens
        self.last_payload = {
            "schema_version": "coevoskills_evolution_token_usage_v1",
            "scope": "evolution_only_final_evaluation_excluded",
            "reference": self.reference,
            "usage": {
                "framework": {
                    "total_tokens": framework_total,
                    "request_count": framework_requests,
                    "source": "framework_llm_response_usage",
                },
                "oracle": {
                    **oracle_usage,
                    "artifact_rollout_tokens": artifact_usage["total_tokens"],
                    "private_oracle_tokens": private_usage["total_tokens"],
                    "source": "docker_run_usage",
                },
                "total_tokens": used_tokens,
            },
            "budget": {
                "limit_tokens": limit_tokens,
                "remaining_tokens": max(0, limit_tokens - used_tokens),
                "overshoot_tokens": max(0, used_tokens - limit_tokens),
                "exhausted": exhausted,
            },
            "progress": {
                "turns_completed": turns_completed,
                "checkpoint": stage,
            },
        }
        write_token_json(self.run_root / "token_usage.json", self.last_payload)
        return exhausted


class CoEvoSkillsWildClawRunner:
    def __init__(
        self,
        *,
        wildclaw_runner: WildClawRunner | None = None,
        artifact_runner: WildClawArtifactRunner | None = None,
        generator: Any | None = None,
        verifier: Any | None = None,
        surrogate_runtime: Any | None = None,
    ) -> None:
        self._wildclaw_runner = wildclaw_runner or WildClawRunner()
        self._artifact_runner = artifact_runner or WildClawArtifactRunner()
        self._generator = generator
        self._verifier = verifier
        self._surrogate_runtime = surrogate_runtime

    def run(self, settings: CoEvoSkillsWildClawRunSettings) -> CoEvoSkillsWildClawRunResult:
        _validate_settings(settings)
        settings.run_root.mkdir(parents=True, exist_ok=True)
        exp_root = settings.run_root / "coevoskills_experiment"
        resume_enabled = (exp_root / "checkpoint.json").is_file()
        if not resume_enabled:
            _clean_fresh_run_root(
                settings.run_root,
                directories=(
                    "coevoskills_experiment",
                    "surrogate_rollouts",
                    "oracle_private",
                    "evaluation",
                    "resume",
                ),
            )
        _ensure_skilllift_importable(settings.skilllift_root)

        from coevoskills.config import CoEvoSkillsConfig
        from coevoskills.coordinator import run_experiment
        from coevoskills.persistence import (
            ExperimentStore,
            write_json,
            write_json_atomic,
        )
        from coevoskills.skill_generator import LLMSkillGenerator
        from coevoskills.surrogate_runtime import PythonSurrogateRuntime
        from coevoskills.surrogate_verifier import LLMSurrogateVerifier

        task_path = resolve_task_path(settings.wildclaw_root, settings.task_filter)
        task = _load_task(task_path, settings.wildclaw_root)
        reference_budget = _reference_budget(settings, task_path)
        config = _algorithm_config(
            CoEvoSkillsConfig,
            settings.algorithm_params,
            token_budget=(reference_budget or {}).get("limit_tokens", 0),
        )
        generator = self._generator
        verifier = self._verifier
        if generator is None or verifier is None:
            from skilllift.llm_client import create_llm_client

            model_config = _framework_model_config(settings.model_endpoint)
            generator = generator or LLMSkillGenerator(
                create_llm_client(model_config, role="skill_generator"),
                max_context_chars=config.max_context_chars,
            )
            verifier = verifier or LLMSurrogateVerifier(
                create_llm_client(model_config, role="surrogate_verifier")
            )
        surrogate_runtime = self._surrogate_runtime or PythonSurrogateRuntime(
            timeout_seconds=config.surrogate_timeout_seconds,
            docker_image=config.surrogate_docker_image,
        )

        previous_usage = (
            _read_json(settings.run_root / "token_usage.json")
            if resume_enabled and (settings.run_root / "token_usage.json").is_file()
            else {}
        )
        previous_framework = previous_usage.get("usage", {}).get("framework", {})
        artifact_backend = _WildClawArtifactBackend(
            settings, self._artifact_runner, resume=resume_enabled
        )
        oracle_backend = _WildClawOracleBackend(
            settings,
            self._wildclaw_runner,
            threshold=config.oracle_threshold,
            resume=resume_enabled,
        )
        token_budget = _EvolutionTokenBudget(
            settings.run_root,
            generator=generator,
            verifier=verifier,
            artifact_backend=artifact_backend,
            oracle_backend=oracle_backend,
            reference=reference_budget,
            framework_token_offset=_nonnegative_int(
                previous_framework.get("total_tokens")
            ),
            framework_request_offset=_nonnegative_int(
                previous_framework.get("request_count")
            ),
        )
        store = ExperimentStore(exp_root)
        result = run_experiment(
            task,
            config,
            generator=generator,
            verifier=verifier,
            rollout=artifact_backend,
            oracle=oracle_backend,
            surrogate_runtime=surrogate_runtime,
            store=store,
            budget=token_budget,
        )
        token_budget.checkpoint(
            turns_completed=int(result.summary.get("turns_completed", 0)),
            stage="evolution_complete",
        )

        evaluation_results = []
        evaluation_skipped = 0
        for repeat in range(1, config.evaluation_repeats + 1):
            repeat_path = exp_root / "evaluation" / "repeats" / f"repeat_{repeat:03d}.json"
            if repeat_path.is_file():
                evaluation_results.append(
                    _oracle_result_from_dict(_read_json(repeat_path))
                )
                evaluation_skipped += 1
                continue
            evaluation_result = oracle_backend.evaluate_final(
                task, result.best_skill, repeat=repeat
            )
            write_json_atomic(repeat_path, evaluation_result.to_dict())
            evaluation_results.append(evaluation_result)
        evaluation_scores = [item.score for item in evaluation_results]
        evaluation_payload = {
            "repeats": [item.to_dict() for item in evaluation_results],
            "scores": evaluation_scores,
            "best_score": max(evaluation_scores),
            "mean_score": sum(evaluation_scores) / len(evaluation_scores),
            "pass_rate": sum(item.passed for item in evaluation_results) / len(evaluation_results),
            "score_aggregation": "max",
        }
        write_json(exp_root / "evaluation" / "summary.json", evaluation_payload)
        summary = _summary(
            settings,
            result,
            evaluation_payload,
            oracle_backend.records,
            artifact_backend.records,
            task_path,
            token_budget.last_payload,
        )
        resume = {
            "skipped": (
                artifact_backend.loaded_count
                + oracle_backend.loaded_count
                + evaluation_skipped
            ),
            "rerun": int(resume_enabled),
            "run": len(oracle_backend.records)
            + len(artifact_backend.records)
            - oracle_backend.loaded_count
            - artifact_backend.loaded_count,
            "failed": sum(record.status != "succeeded" for record in oracle_backend.records)
            + sum(record.status != "succeeded" for record in artifact_backend.records),
        }
        return CoEvoSkillsWildClawRunResult(
            records=oracle_backend.records,
            artifact_records=artifact_backend.records,
            summary=summary,
            resume=resume,
        )


class _WildClawArtifactBackend:
    def __init__(
        self,
        settings: CoEvoSkillsWildClawRunSettings,
        runner: WildClawArtifactRunner,
        *,
        resume: bool = False,
    ) -> None:
        self.settings = settings
        self.runner = runner
        self._records_path = settings.run_root / "resume" / "artifact_records.json"
        self.records = _load_artifact_records(self._records_path) if resume else []
        self.loaded_count = len(self.records)

    def rollout(self, task: Any, skill: Any) -> Any:
        from coevoskills.schemas import ArtifactSnapshot

        index = len(self.records) + 1
        run_root = self.settings.run_root / "surrogate_rollouts" / f"rollout_{index:03d}"
        record = self.runner.run(
            _wildclaw_settings(self.settings, run_root, phase="surrogate"),
            _skill_bundle(skill, task.task_id, phase=f"surrogate-{index}"),
        )
        self.records.append(record)
        _write_resume_json(
            self._records_path, [_record_dict(item) for item in self.records]
        )
        if record.status != "succeeded":
            raise RuntimeError(f"WildClaw artifact rollout failed: {record.error_summary}")
        return _snapshot(ArtifactSnapshot, record)


class _WildClawOracleBackend:
    def __init__(
        self,
        settings: CoEvoSkillsWildClawRunSettings,
        runner: WildClawRunner,
        *,
        threshold: float,
        resume: bool = False,
    ) -> None:
        self.settings = settings
        self.runner = runner
        self.threshold = threshold
        self._records_path = settings.run_root / "resume" / "task_records.json"
        self._evolution_records_path = (
            settings.run_root / "resume" / "evolution_task_records.json"
        )
        self.records = _load_task_records(self._records_path) if resume else []
        self.evolution_records = (
            _load_task_records(self._evolution_records_path) if resume else []
        )
        self.loaded_count = len(self.records)
        self._optimization_calls = len(self.evolution_records)

    def evaluate(self, task: Any, skill: Any) -> Any:
        self._optimization_calls += 1
        start = len(self.records)
        result = self._evaluate(
            task,
            skill,
            phase="oracle_private",
            label=f"oracle_{self._optimization_calls:03d}",
        )
        self.evolution_records.extend(self.records[start:])
        _write_resume_json(
            self._evolution_records_path,
            [_record_dict(item) for item in self.evolution_records],
        )
        return result

    def evaluate_final(self, task: Any, skill: Any, *, repeat: int) -> Any:
        return self._evaluate(
            task,
            skill,
            phase="evaluation",
            label=f"repeat_{repeat:03d}",
        )

    def _evaluate(self, task: Any, skill: Any, *, phase: str, label: str) -> Any:
        from coevoskills.schemas import OracleResult

        run_root = self.settings.run_root / phase / label
        run_result = self.runner.run(
            _wildclaw_settings(self.settings, run_root, phase=phase),
            _skill_bundle(skill, task.task_id, phase=label),
        )
        if not run_result.records:
            raise RuntimeError("WildClaw Oracle returned no TaskRunRecord")
        record = run_result.records[0]
        self.records.append(record)
        _write_resume_json(
            self._records_path, [_record_dict(item) for item in self.records]
        )
        payload = json.loads(Path(record.score_path).read_text(encoding="utf-8"))
        if record.status != "succeeded":
            raise RuntimeError(f"WildClaw Oracle failed: {record.error_summary}")
        score = float(payload.get("score", payload.get("overall_score", 0.0)) or 0.0)
        return OracleResult(
            run_id=record.run_id,
            score=score,
            passed=score >= self.threshold,
            artifact_path=record.score_path,
            metadata={"phase": phase, "task_run_record": record.raw_output_path},
        )


def _snapshot(cls: Any, record: ArtifactRolloutRecord) -> Any:
    from coevoskills.schemas import ArtifactFile

    root = Path(record.artifact_root).resolve()
    files = []
    total = 0
    remaining_text_bytes = MAX_SNAPSHOT_TEXT_BYTES
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in {".git", ".openclaw"} for part in relative.parts):
            continue
        rel_path = relative.as_posix()
        size = path.stat().st_size
        total += size
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        excerpt = None
        if remaining_text_bytes > 0 and (
            path.suffix.lower() in TEXT_SUFFIXES or media_type.startswith("text/")
        ):
            with path.open("rb") as handle:
                excerpt_bytes = handle.read(
                    min(remaining_text_bytes, MAX_EXCERPT_CHARS * 4)
                )
            excerpt = _sanitize_excerpt(
                excerpt_bytes.decode("utf-8", errors="replace")[:MAX_EXCERPT_CHARS]
            )
            remaining_text_bytes -= len(excerpt_bytes)
        files.append(
            ArtifactFile(
                path=rel_path,
                size_bytes=size,
                sha256=_sha256_file(path),
                media_type=media_type,
                text_excerpt=excerpt,
            )
        )
    return cls(
        run_id=record.run_id,
        root_path=str(root),
        files=files,
        metadata={"task_id": record.task_id, "file_count": len(files), "total_bytes": total},
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _oracle_result_from_dict(payload: dict[str, Any]) -> Any:
    from coevoskills.schemas import OracleResult

    return OracleResult(
        run_id=str(payload["run_id"]),
        score=float(payload["score"]),
        passed=bool(payload["passed"]),
        artifact_path=str(payload["artifact_path"]),
        metadata=dict(payload.get("metadata", {})),
    )


def _load_task_records(path: Path) -> list[TaskRunRecord]:
    if not path.is_file():
        return []
    return [TaskRunRecord.from_dict(item) for item in _read_json(path)]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_artifact_records(path: Path) -> list[ArtifactRolloutRecord]:
    if not path.is_file():
        return []
    return [ArtifactRolloutRecord(**item) for item in _read_json(path)]


def _write_resume_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _clean_fresh_run_root(
    run_root: Path, *, directories: tuple[str, ...]
) -> None:
    for name in directories:
        path = run_root / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    for name in (
        "token_usage.json",
        "task_run_records.json",
        "artifact_rollout_records.json",
        "cell_summary.json",
    ):
        path = run_root / name
        if path.exists():
            path.unlink()


def _record_dict(record: Any) -> dict[str, Any]:
    to_dict = getattr(record, "to_dict", None)
    return to_dict() if callable(to_dict) else dict(vars(record))


def _skill_bundle(skill: Any, task_id: str, *, phase: str) -> SkillBundle:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", skill.name).strip("-._") or "evolved-skill"
    safe_task_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", task_id).strip("-._") or "task"
    entry = {
        "id": f"coevoskills-{safe_task_id}-{safe_name}-{skill.token}",
        "title": skill.name,
        "content": skill.files["SKILL.md"],
        "files": dict(skill.files),
        "entrypoint": skill.entrypoint,
        "metadata": {"skill_version": skill.version, "phase": phase},
    }
    return SkillBundle(
        skill_bundle_id=f"coevoskills-{task_id}-{skill.token}-{phase}",
        baseline="coevoskills",
        benchmark_target="wildclawbench",
        granularity="task",
        skills=[entry],
        source_round=skill.token,
        created_at=_utc_now(),
    )


def _wildclaw_settings(
    settings: CoEvoSkillsWildClawRunSettings,
    run_root: Path,
    *,
    phase: str,
) -> WildClawRunSettings:
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
        task_sample_policy_id=f"{TASK_SAMPLE_POLICY_ID}:{phase}",
        round_budget=settings.round_budget,
        token_budget=settings.token_budget,
        tasks_mode="single",
        task_filter=settings.task_filter,
        baseline="coevoskills",
        rate_limit_retries=max(0, settings.model_endpoint.max_retries),
        timeout_seconds_override=int(
            settings.algorithm_params.get(
                "evaluation_timeout_seconds" if phase == "evaluation" else "evolution_timeout_seconds",
                7200 if phase == "evaluation" else 3000,
            )
        ),
        run_id_suffix=f"{phase}__{run_root.name}",
    )


def _load_task(path: Path, wildclaw_root: Path) -> Any:
    from coevoskills.schemas import TaskInput

    text = path.read_text(encoding="utf-8")
    task_id_match = re.search(r"^id:\s*(.+?)\s*$", text, flags=re.MULTILINE)
    prompt_match = re.search(
        r"^##\s+Prompt\s*$\n(.*?)(?=^##\s+|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not prompt_match or not prompt_match.group(1).strip():
        raise ValueError(f"WildClaw task has no public Prompt section: {path}")
    workspace_match = re.search(
        r"^##\s+Workspace Path\s*$\n(.*?)(?=^##\s+|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    reference_material = _public_workspace_context(
        wildclaw_root,
        workspace_match.group(1).strip() if workspace_match else "",
    )
    return TaskInput(
        task_id=task_id_match.group(1).strip() if task_id_match else path.stem,
        instruction=prompt_match.group(1).strip(),
        task_path=str(path.resolve()),
        reference_material=reference_material,
    )


def _public_workspace_context(wildclaw_root: Path, workspace_value: str) -> str:
    if not workspace_value:
        return ""
    workspace = (wildclaw_root / workspace_value).resolve()
    allowed_root = (wildclaw_root / "workspace").resolve()
    if allowed_root not in workspace.parents or not workspace.is_dir():
        return ""
    lines: list[str] = []
    remaining = 20_000
    for subdir in ("exec", "tmp"):
        root = workspace / subdir
        if not root.is_dir():
            continue
        for file_path in sorted(root.rglob("*")):
            if not file_path.is_file() or file_path.is_symlink():
                continue
            rel_path = file_path.relative_to(workspace).as_posix()
            size = file_path.stat().st_size
            header = f"[Public input: {rel_path}, {size} bytes]"
            lines.append(header)
            if remaining <= 0 or size > 100_000:
                continue
            content = file_path.read_bytes()
            if file_path.suffix.lower() not in TEXT_SUFFIXES and b"\x00" in content[:1024]:
                continue
            excerpt = _sanitize_excerpt(
                content.decode("utf-8", errors="replace")[: min(4_000, remaining)]
            )
            lines.append(excerpt)
            remaining -= len(excerpt)
    return "\n\n".join(lines)


def _algorithm_config(
    cls: Any,
    params: dict[str, Any],
    *,
    token_budget: int = 0,
) -> Any:
    max_surrogate_retries = int(params.get("max_surrogate_retries", 15))
    max_oracle_interventions = int(params.get("max_oracle_interventions", 5))
    return cls(
        max_oracle_interventions=max_oracle_interventions,
        max_surrogate_retries=max_surrogate_retries,
        min_turns=int(params.get("min_turns", 1)),
        max_turns=int(
            params.get(
                "max_turns",
                max_surrogate_retries + max_oracle_interventions,
            )
        ),
        token_budget=int(token_budget),
        context_cap_ratio=float(params.get("context_cap_ratio", 0.7)),
        max_context_chars=int(params.get("max_context_chars", 200_000)),
        oracle_threshold=float(params.get("oracle_threshold", 1.0)),
        surrogate_timeout_seconds=int(params.get("surrogate_timeout_seconds", 30)),
        surrogate_docker_image=(
            os.environ.get("SKILLLIFTSKILLS_SURROGATE_DOCKER_IMAGE")
            or os.environ.get("DOCKER_IMAGE")
            or params.get("surrogate_docker_image")
            or None
        ),
        evaluation_repeats=int(params.get("evaluation_repeats", 5)),
        initial_suite_policy=str(
            params.get("initial_suite_policy", "generate_before_first_score_v1")
        ),
        surrogate_counter_policy=str(
            params.get("surrogate_counter_policy", "surrogate_failure_repairs_v1")
        ),
    )


def _framework_model_config(endpoint: ModelEndpointProfile) -> dict[str, Any]:
    api_key = endpoint.api_key or os.environ.get(endpoint.api_key_env or "")
    if not api_key:
        raise ValueError(f"missing API key for {endpoint.model_label}")
    return {
        "runtime": {"llm_timeout_seconds": endpoint.timeout_seconds, "llm_max_retries": endpoint.max_retries},
        "providers": {
            "coevoskills": {
                "baseUrl": endpoint.base_url,
                "apiKey": api_key,
                "stream": endpoint.stream,
                "models": [{"id": endpoint.provider_model_id, "name": endpoint.model_label}],
            }
        },
    }


def _summary(
    settings: CoEvoSkillsWildClawRunSettings,
    result: Any,
    evaluation: dict[str, Any],
    records: list[TaskRunRecord],
    artifact_records: list[ArtifactRolloutRecord],
    task_path: Path,
    token_usage: dict[str, Any],
) -> dict[str, Any]:
    evaluation_count = len(evaluation["repeats"])
    status = "SUCCEEDED" if evaluation_count else "FAILED"
    return {
        "matrix_cell_id": settings.matrix_cell_id,
        "baseline": "coevoskills",
        "benchmark": "wildclawbench",
        "model_label": settings.model_label,
        "evaluation_mode": settings.evaluation_mode,
        "context_budget_profile": settings.context_budget_profile,
        "expected_count": 1,
        "succeeded_count": int(status == "SUCCEEDED"),
        "completion_ratio": float(status == "SUCCEEDED"),
        "status": status,
        # Matrix consumers use mean_score as the cell's primary score. CoEvoSkills
        # now follows the original CoEvo convention and selects the best repeat.
        "mean_score": evaluation["best_score"],
        "best_score": evaluation["best_score"],
        "evaluation_mean_score": evaluation["mean_score"],
        "score_aggregation": evaluation["score_aggregation"],
        "pass_rate": evaluation["pass_rate"],
        "stop_reason": result.stop_reason,
        "oracle_interventions": result.oracle_interventions,
        "surrogate_retries": result.surrogate_retries,
        "surrogate_rollouts": len(artifact_records),
        "turns_completed": result.summary.get("turns_completed", 0),
        "min_turns": result.summary.get("min_turns", 1),
        "max_turns": result.summary.get("max_turns", 0),
        "stop_priority": result.summary.get("stop_priority", []),
        "token_usage": token_usage.get("usage", {}),
        "token_budget": token_usage.get("budget", {}),
        "token_budget_reference": token_usage.get("reference", {}),
        "evaluation_repeats": evaluation_count,
        "task_path": str(task_path.resolve()),
        "artifact": str(settings.run_root.resolve()),
        "source": SOURCE,
        "implementation_scope": "wildclawbench_adaptation_not_skillsbench_reproduction",
    }


def _sanitize_excerpt(value: str) -> str:
    return SECRET_RE.sub("[redacted-secret]", value)


def _validate_settings(settings: CoEvoSkillsWildClawRunSettings) -> None:
    if settings.tasks_mode != "single":
        raise ValueError("CoEvoSkills WildClawBench runner currently requires --tasks.mode single")
    if not settings.task_filter.strip():
        raise ValueError("CoEvoSkills WildClawBench runner requires --tasks.filter")
    if settings.evaluation_mode != "native_end_to_end":
        raise ValueError("CoEvoSkills WildClawBench supports native_end_to_end")


def _reference_budget(
    settings: CoEvoSkillsWildClawRunSettings,
    task_path: Path,
) -> dict[str, Any] | None:
    raw_root = str(settings.algorithm_params.get("token_budget_reference_root") or "").strip()
    if not raw_root:
        return None
    reference_root = Path(raw_root).expanduser()
    if not reference_root.is_absolute():
        reference_root = (settings.project_root / reference_root).resolve()
    task_key = task_key_from_path(task_path)
    report_path = reference_root / task_key / "token_usage.json"
    if not report_path.exists() and (reference_root / task_key / "skilllift").is_dir():
        write_skilllift_reference_usage(
            reference_root / task_key,
            budget_multiplier=float(
                settings.algorithm_params.get("token_budget_multiplier", 2.0)
            ),
        )
    return load_reference_budget(
        reference_root,
        task_key,
        multiplier=float(settings.algorithm_params.get("token_budget_multiplier", 2.0)),
    )


def _unique_clients(*owners: Any) -> list[Any]:
    clients: list[Any] = []
    seen: set[int] = set()
    for owner in owners:
        client = getattr(owner, "client", None)
        if client is None or id(client) in seen:
            continue
        seen.add(id(client))
        clients.append(client)
    return clients


def _task_run_usage(record: Any) -> dict[str, Any]:
    raw_path = Path(str(getattr(record, "raw_output_path", "")))
    if raw_path.is_file():
        try:
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
            if isinstance(payload.get("usage"), dict):
                return payload["usage"]
        except (OSError, ValueError, TypeError):
            pass
    actual = getattr(record, "token_actual", 0)
    return {"total_tokens": _nonnegative_int(actual), "request_count": 0}


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _ensure_skilllift_importable(root: Path) -> None:
    value = str(root.resolve())
    if value not in sys.path:
        sys.path.insert(0, value)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
