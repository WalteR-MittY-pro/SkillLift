from __future__ import annotations

import hashlib
import mimetypes
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any, Iterable

from skilllift_eval.runners.skillsbench_evolution_adapter import (
    SkillsBenchEvolutionAdapter,
    SkillsBenchEvolutionSettings,
    build_evolution_adapter,
)


AGENTCLAW_ROOT = Path(__file__).resolve().parents[2] / "skilllift"
if str(AGENTCLAW_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTCLAW_ROOT))

from skilllift.llm_client import LLMClient  # noqa: E402
from skilllift.coordinator import TrialSpec  # noqa: E402
from coevoskills.config import CoEvoSkillsConfig  # noqa: E402
from coevoskills.coordinator import run_experiment  # noqa: E402
from coevoskills.persistence import ExperimentStore, write_json_atomic  # noqa: E402
from coevoskills.schemas import (  # noqa: E402
    ArtifactFile,
    ArtifactSnapshot,
    OracleResult,
    TaskInput,
)
from coevoskills.skill_generator import LLMSkillGenerator  # noqa: E402
from coevoskills.surrogate_runtime import PythonSurrogateRuntime  # noqa: E402
from coevoskills.surrogate_verifier import LLMSurrogateVerifier  # noqa: E402


OVERLAY_NAME = "coevoskills-generated"


class CoEvoSkillsSkillsBenchRunner:
    def __init__(
        self,
        settings: SkillsBenchEvolutionSettings,
        *,
        generator: Any | None = None,
        verifier: Any | None = None,
        surrogate_runtime: Any | None = None,
        adapter_factory: Any | None = None,
    ) -> None:
        self.settings = settings
        self._generator = generator
        self._verifier = verifier
        self._surrogate_runtime = surrogate_runtime
        self._adapter_factory = adapter_factory or (
            lambda task_root: build_evolution_adapter(settings, task_root)
        )

    def run(self, task_ids: Iterable[str]) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for task_id in task_ids:
            try:
                row = self.run_task(task_id)
            except Exception as exc:
                row = self._record_task_failure(task_id, exc)
                print(
                    f"[coevoskills] task failed task_id={task_id} "
                    f"error={type(exc).__name__}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
            rows.append(row)

        succeeded = [row for row in rows if row["status"] == "completed"]
        failed_count = len(rows) - len(succeeded)
        status = (
            "completed"
            if failed_count == 0
            else "completed_with_failures"
            if succeeded
            else "failed"
        )
        summary = {
            "baseline": "coevoskills",
            "benchmark": "skillsbench",
            "status": status,
            "tasks": rows,
            "task_count": len(rows),
            "succeeded_task_count": len(succeeded),
            "failed_task_count": failed_count,
            "mean_best_oracle_score": (
                sum(float(row["best_oracle_score"] or 0.0) for row in succeeded)
                / len(succeeded)
                if succeeded
                else 0.0
            ),
        }
        write_json_atomic(self.settings.run_root / "summary.json", summary)
        return summary

    def _record_task_failure(
        self, task_id: str, error: Exception
    ) -> dict[str, Any]:
        row = {
            "task_id": task_id,
            "status": "failed",
            "error_type": type(error).__name__,
            "error": str(error),
        }
        write_json_atomic(
            self.settings.run_root / "tasks" / task_id / "summary.json", row
        )
        return row

    def run_task(self, task_id: str) -> dict[str, Any]:
        task_root = self.settings.run_root / "tasks" / task_id
        task_root.mkdir(parents=True, exist_ok=True)
        config = _algorithm_config(self.settings.algorithm_params or {})
        task = _task_input(self.settings.tasks_root, task_id)
        adapter = self._adapter_factory(task_root)
        generator, verifier = self._framework_components(config)
        rollout = _ArtifactBackend(adapter, task_id)
        oracle = _OracleBackend(adapter, task_id, threshold=config.oracle_threshold)
        result = run_experiment(
            task,
            config,
            generator=generator,
            verifier=verifier,
            rollout=rollout,
            oracle=oracle,
            surrogate_runtime=self._surrogate_runtime
            or PythonSurrogateRuntime(
                timeout_seconds=config.surrogate_timeout_seconds,
                docker_image=config.surrogate_docker_image,
            ),
            store=ExperimentStore(task_root / "coevoskills_experiment"),
        )
        final_scores = [
            oracle.evaluate_final(task, result.best_skill, repeat=index)
            for index in range(1, config.evaluation_repeats + 1)
        ]
        row = {
            "task_id": task_id,
            "status": "completed",
            "stop_reason": result.stop_reason,
            "best_oracle_score": result.best_oracle_score,
            "evaluation_scores": [item.score for item in final_scores],
            "evaluation_mean": sum(item.score for item in final_scores)
            / len(final_scores),
            "oracle_interventions": result.oracle_interventions,
            "surrogate_retries": result.surrogate_retries,
            "framework_tokens": sum(
                int(getattr(owner.client, "total_tokens", 0))
                for owner in (generator, verifier)
                if hasattr(owner, "client")
            ),
        }
        write_json_atomic(task_root / "summary.json", row)
        return row

    def _framework_components(self, config: CoEvoSkillsConfig) -> tuple[Any, Any]:
        generator = self._generator
        verifier = self._verifier
        if generator is None:
            generator = LLMSkillGenerator(
                LLMClient(
                    self.settings.base_url,
                    self.settings.api_key,
                    self.settings.framework_model,
                    role="skill_generator",
                    use_stream=self.settings.use_stream,
                ),
                max_context_chars=config.max_context_chars,
            )
        if verifier is None:
            verifier = LLMSurrogateVerifier(
                LLMClient(
                    self.settings.base_url,
                    self.settings.api_key,
                    self.settings.framework_model,
                    role="surrogate_verifier",
                    use_stream=self.settings.use_stream,
                )
            )
        return generator, verifier


class _ArtifactBackend:
    def __init__(self, adapter: SkillsBenchEvolutionAdapter, task_id: str) -> None:
        self.adapter = adapter
        self.task_id = task_id
        self.calls = _existing_trial_count(adapter, task_id, "surrogate-")

    def rollout(self, task: TaskInput, skill: Any) -> ArtifactSnapshot:
        del task
        self.calls += 1
        trial = TrialSpec(
            trial_id=f"surrogate-{self.calls:03d}-{skill.token}",
            phase="surrogate",
            attempt=1,
            round_index=self.calls,
            candidate_id=skill.token,
        )
        result = self.adapter.evaluate(
            self.task_id,
            overlay_name=OVERLAY_NAME,
            files=skill.files,
            trial=trial,
        )
        return _snapshot(result.public_artifact_root, trial.trial_id, self.task_id)


class _OracleBackend:
    def __init__(
        self,
        adapter: SkillsBenchEvolutionAdapter,
        task_id: str,
        *,
        threshold: float,
    ) -> None:
        self.adapter = adapter
        self.task_id = task_id
        self.threshold = threshold
        self.calls = _existing_trial_count(adapter, task_id, "oracle-")

    def evaluate(self, task: TaskInput, skill: Any) -> OracleResult:
        del task
        self.calls += 1
        return self._evaluate(skill, f"oracle-{self.calls:03d}", phase="oracle")

    def evaluate_final(
        self, task: TaskInput, skill: Any, *, repeat: int
    ) -> OracleResult:
        del task
        return self._evaluate(skill, f"final-{repeat:03d}", phase="evaluation")

    def _evaluate(self, skill: Any, trial_id: str, *, phase: str) -> OracleResult:
        result = self.adapter.evaluate(
            self.task_id,
            overlay_name=OVERLAY_NAME,
            files=skill.files,
            trial=TrialSpec(
                trial_id=trial_id,
                phase=phase,
                attempt=1,
                candidate_id=skill.token,
            ),
        )
        return OracleResult(
            run_id=trial_id,
            score=result.reward,
            passed=result.reward >= self.threshold,
            artifact_path=str(result.result_path),
            metadata={"phase": phase},
        )


def _algorithm_config(params: dict[str, Any]) -> CoEvoSkillsConfig:
    allowed = {item.name for item in fields(CoEvoSkillsConfig)}
    values = {key: value for key, value in params.items() if key in allowed}
    values["token_budget"] = 0
    return CoEvoSkillsConfig(**values)


def _task_input(tasks_root: Path, task_id: str) -> TaskInput:
    path = tasks_root / task_id / "task.md"
    if not path.is_file():
        raise ValueError(f"SkillsBench task does not exist: {task_id}")
    text = path.read_text(encoding="utf-8")
    instruction = text.split("\n---\n", 1)[-1].strip()
    return TaskInput(task_id=task_id, instruction=instruction, task_path=str(path))


def _snapshot(root: Path, run_id: str, task_id: str) -> ArtifactSnapshot:
    files: list[ArtifactFile] = []
    total = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        data = path.read_bytes()
        total += len(data)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        excerpt = None
        if media_type.startswith("text/") or path.suffix in {
            ".json",
            ".md",
            ".csv",
            ".py",
        }:
            excerpt = data[:8_000].decode("utf-8", errors="replace")
        files.append(
            ArtifactFile(
                path=path.relative_to(root).as_posix(),
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                media_type=media_type,
                text_excerpt=excerpt,
            )
        )
    return ArtifactSnapshot(
        run_id=run_id,
        root_path=str(root),
        files=files,
        metadata={"task_id": task_id, "file_count": len(files), "total_bytes": total},
    )


def _existing_trial_count(
    adapter: SkillsBenchEvolutionAdapter,
    task_id: str,
    prefix: str,
) -> int:
    root = adapter.adapter.settings.run_root / "adapter_artifacts" / task_id
    if not root.is_dir():
        return 0
    return sum(
        path.is_dir() and path.name.startswith(prefix) for path in root.iterdir()
    )
