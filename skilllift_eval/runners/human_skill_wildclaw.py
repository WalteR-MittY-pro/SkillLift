from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import re

from skilllift_eval.runners.wildclaw import (
    TaskRunner,
    WildClawRunResult,
    WildClawRunSettings,
    WildClawRunner,
    resolve_task_path,
)
from skilllift_eval.schemas import SkillBundle, TaskRunRecord, stable_hash


SOURCE = "human_authored_skill"
SOURCE_VERSION = "human_skill_wildclaw_v1"
SINGLE_TASK_SAMPLE_POLICY_ID = "human_skill_single_task_v1"
FULL_TASK_SAMPLE_POLICY_ID = "human_skill_full_60_v1"


class HumanSkillWildClawRunner:
    def __init__(self, *, task_runner: TaskRunner | None = None) -> None:
        self._runner = WildClawRunner(task_runner=task_runner)

    def run(
        self,
        settings: WildClawRunSettings,
        human_skills_root: Path,
    ) -> WildClawRunResult:
        if settings.baseline != "human_skill":
            raise ValueError("HumanSkillWildClawRunner requires baseline=human_skill")
        if settings.tasks_mode not in {"single", "full"}:
            raise ValueError("human_skill WildClawBench runner supports single or full mode")

        inventory = validate_human_skill_inventory(settings.wildclaw_root, human_skills_root)
        if settings.tasks_mode == "single":
            task_path = resolve_task_path(settings.wildclaw_root, settings.task_filter or "")
            selected = [(task_path, resolve_human_skill_path(task_path, human_skills_root))]
        else:
            selected = inventory

        source_hash = human_skill_source_hash(human_skills_root)
        _write_json(
            settings.run_root / "human_skill_source_manifest.json",
            {
                "source": SOURCE,
                "source_version": SOURCE_VERSION,
                "human_skills_root": str(human_skills_root.resolve()),
                "source_hash": source_hash,
                "inventory_count": len(inventory),
                "selected_count": len(selected),
                "selected_files": [skill.name for _, skill in selected],
            },
        )

        records: list[TaskRunRecord] = []
        resume = {"skipped": 0, "rerun": 0, "run": 0, "failed": 0}
        for task_path, skill_path in selected:
            bundle = build_human_skill_bundle(task_path, skill_path)
            child_settings = replace(
                settings,
                tasks_mode="single",
                task_filter=task_path.stem,
                round_id=task_path.stem,
                round_type="human_skill_evaluation",
                task_sample_policy_id=(
                    SINGLE_TASK_SAMPLE_POLICY_ID
                    if settings.tasks_mode == "single"
                    else FULL_TASK_SAMPLE_POLICY_ID
                ),
            )
            result = self._runner.run(child_settings, bundle)
            records.extend(result.records)
            for key in resume:
                resume[key] += result.resume.get(key, 0)

        return WildClawRunResult(records, _cell_summary(settings, records), resume)


def validate_human_skill_inventory(
    wildclaw_root: Path,
    human_skills_root: Path,
) -> list[tuple[Path, Path]]:
    tasks_root = wildclaw_root / "tasks"
    task_paths = sorted(tasks_root.glob("*/*task_*.md"))
    if not task_paths:
        raise ValueError(f"WildClawBench task inventory is empty: {tasks_root}")
    if not human_skills_root.is_dir():
        raise ValueError(f"human skill source directory not found: {human_skills_root}")

    expected = {task.name.lower() for task in task_paths}
    actual = {path.name.lower() for path in human_skills_root.glob("*.md")}
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing={missing}")
        if extra:
            details.append(f"extra={extra}")
        raise ValueError("human skill inventory does not match WildClawBench tasks: " + "; ".join(details))

    return [
        (task_path.resolve(), resolve_human_skill_path(task_path, human_skills_root))
        for task_path in task_paths
    ]


def resolve_human_skill_path(task_path: Path, human_skills_root: Path) -> Path:
    expected_name = task_path.name.lower()
    matches = [path for path in human_skills_root.glob("*.md") if path.name.lower() == expected_name]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one human skill for {task_path.name}, found {len(matches)}"
        )
    return matches[0].resolve()


def build_human_skill_bundle(task_path: Path, skill_path: Path) -> SkillBundle:
    content = skill_path.read_text(encoding="utf-8")
    skill_name = _frontmatter_name(content) or skill_path.stem.replace("_", "-")
    task_id = _task_id(task_path)
    return SkillBundle(
        skill_bundle_id=f"human-skill-{task_id.lower().replace('_', '-')}",
        baseline="human_skill",
        benchmark_target="wildclawbench",
        granularity="task",
        skills=[
            {
                "id": skill_name,
                "title": skill_name,
                "content": content,
                "files": {"SKILL.md": content},
                "metadata": {
                    "source": SOURCE,
                    "source_path": str(skill_path.resolve()),
                    "task_id": task_id,
                },
            }
        ],
        created_at=datetime.now(timezone.utc).isoformat(),
        source_artifact_path=str(skill_path.resolve()),
    )


def human_skill_source_hash(human_skills_root: Path) -> str:
    files = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(human_skills_root.glob("*.md"))
    }
    return stable_hash({"source_version": SOURCE_VERSION, "files": files})


def _frontmatter_name(content: str) -> str | None:
    match = re.search(r"^name:\s*([^\n]+?)\s*$", content, flags=re.MULTILINE)
    return match.group(1).strip().strip("'\"") if match else None


def _task_id(task_path: Path) -> str:
    match = re.search(
        r"^id:\s*(.+?)\s*$",
        task_path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    return match.group(1).strip() if match else task_path.stem


def _cell_summary(
    settings: WildClawRunSettings,
    records: list[TaskRunRecord],
) -> dict[str, Any]:
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
        "baseline": "human_skill",
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
        "source": SOURCE,
    }


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
