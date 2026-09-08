from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from skilllift_eval.runners.skillsbench_portfolio_adapter import (
    SKILLSBENCH_PORTFOLIO_LOADER_VERSION,
    SkillsBenchPortfolioAdapter,
    SkillsBenchPortfolioSettings,
)
from skilllift_eval.runners.skillsbench_report import append_usage_record
from skilllift_eval.skillsbench_cli import SkillsBenchSplit, load_and_validate_split


AGENTCLAW_ROOT = Path(__file__).resolve().parents[2] / "skilllift"
BOUNDED_EDITS_SRC = Path(__file__).resolve().parents[2] / "packages" / "bounded-edits" / "src"
for source_root in (AGENTCLAW_ROOT, BOUNDED_EDITS_SRC):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from skilllift.llm_client import LLMClient  # noqa: E402
from skilllift.portfolio import portfolio_tree_hash  # noqa: E402
from skilllift.coordinator import (  # noqa: E402
    CoordinatorConfig,
    TaskEvolutionResult,
)
from skilllift.portfolio import LLMRubricatorPlanner  # noqa: E402
from skilllift.portfolio import PortfolioStore  # noqa: E402
from skilllift import SkillLiftPortfolioCoordinator  # noqa: E402
from skilllift_eval.runners.bounded_edits_skill_generator import (  # noqa: E402
    BoundedEditsSkillGenerator,
)


TASK_RUNNER_VERSION = "skillsbench_skilllift_runner_v1"


@dataclass(frozen=True)
class SkillLiftSkillsBenchTasksSettings:
    project_root: Path
    run_root: Path
    tasks_root: Path
    split_path: Path
    python_executable: Path
    model: str
    sandbox: str
    base_url: str
    api_key: str
    patch_source: Path
    agent: str
    framework_model: str | None = None
    max_context_chars: int | None = None
    candidate_concurrency: int = 2
    use_stream: bool = False
    prebuilt_image_template: str | None = None
    mode_a_iters: int = 2
    mode_b_iters: int = 2
    mode_a_min_score_threshold: float = 0.85
    rank_alignment_threshold: float = 0.9
    terminal_threshold: float = 1.0


class SkillLiftSkillsBenchTasksRunner:
    def __init__(
        self,
        settings: SkillLiftSkillsBenchTasksSettings,
        *,
        adapter: Any | None = None,
        planner: Any | None = None,
        generator: Any | None = None,
        verifier_client: Any | None = None,
    ) -> None:
        self.settings = settings
        self.store = PortfolioStore(settings.run_root)
        self.adapter = adapter or SkillsBenchPortfolioAdapter(
            SkillsBenchPortfolioSettings(
                project_root=settings.project_root,
                tasks_root=settings.tasks_root,
                run_root=settings.run_root,
                python_executable=settings.python_executable,
                model=settings.model,
                sandbox=settings.sandbox,
                base_url=settings.base_url,
                api_key=settings.api_key,
                patch_source=settings.patch_source,
                agent=settings.agent,
                prebuilt_image_template=settings.prebuilt_image_template,
                terminal_threshold=settings.terminal_threshold,
            )
        )
        if planner is None or generator is None:
            framework_model = settings.framework_model or settings.model.split("/", 1)[-1]
            planner_client = LLMClient(
                settings.base_url,
                settings.api_key,
                framework_model,
                role="rubricator",
                usage_callback=self._usage_callback,
                use_stream=settings.use_stream,
            )
            generator_client = LLMClient(
                settings.base_url,
                settings.api_key,
                framework_model,
                role="skill_generator",
                usage_callback=self._usage_callback,
                use_stream=settings.use_stream,
            )
            verifier_client = verifier_client or LLMClient(
                settings.base_url,
                settings.api_key,
                framework_model,
                role="verifier",
                usage_callback=self._usage_callback,
                use_stream=settings.use_stream,
            )
            planner = planner or LLMRubricatorPlanner(
                planner_client,
                max_input_chars=settings.max_context_chars,
                core_evidence_on_overflow=True,
            )
            generator = generator or BoundedEditsSkillGenerator(
                generator_client,
                max_input_chars=settings.max_context_chars,
            )
        self.planner = planner
        self.generator = generator
        self.verifier_client = verifier_client
        self.coordinator_config = CoordinatorConfig(
            candidate_concurrency=settings.candidate_concurrency,
            mode_a_iters=settings.mode_a_iters,
            mode_b_iters=settings.mode_b_iters,
            mode_a_min_score_threshold=settings.mode_a_min_score_threshold,
            rank_alignment_threshold=settings.rank_alignment_threshold,
            max_context_chars=settings.max_context_chars or 120_000,
        )

    def run(self, task_ids: Iterable[str]) -> dict[str, Any]:
        universe = load_task_universe(self.settings.split_path, self.settings.tasks_root)
        selected = select_task_ids(universe, tasks=tuple(task_ids))
        universe_hash = task_universe_hash(self.settings.tasks_root, universe)
        manifest = build_shard_manifest(
            universe,
            selected,
            universe_hash=universe_hash,
        )
        _write_once(self.settings.run_root / "run_manifest.json", manifest)
        coordinator = SkillLiftPortfolioCoordinator(
            adapter=self.adapter,
            planner=self.planner,
            generator=self.generator,
            store=self.store,
            config=self.coordinator_config,
            verifier_client=self.verifier_client,
        )
        rows = []
        for task_id in selected:
            rows.append(_result_row(coordinator.evolve(task_id)))
            _write_json_atomic(
                self.settings.run_root / "summary.json",
                _summary(manifest, rows, status="in_progress"),
            )
        summary = _summary(manifest, rows, status="completed")
        _write_json_atomic(self.settings.run_root / "summary.json", summary)
        return summary

    def _usage_callback(self, event: dict[str, Any]) -> None:
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        append_usage_record(
            self.settings.run_root / "usage" / "usage.jsonl",
            {
                "usage_record_id": f"framework-{event['role']}-{event['request_id']}",
                "source": "framework",
                "role": event["role"],
                "phase": "evolve",
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "cache_read_tokens": usage.get("cached_tokens"),
                "cache_creation_tokens": None,
                "total_tokens": usage.get("total_tokens"),
                "cost_usd": usage.get("cost_usd"),
                "usage_source": "provider_response",
                "breakdown_available": usage.get("prompt_tokens") is not None,
                "selected_result": True,
            },
        )


def load_task_universe(split_path: Path, tasks_root: Path) -> tuple[str, ...]:
    split = load_and_validate_split(split_path, tasks_root)
    return tuple(sorted(split.train_task_ids | split.test_task_ids))


def select_domain_task_ids(
    split: SkillsBenchSplit,
    domains: Iterable[str] | None = None,
) -> tuple[str, ...]:
    requested = tuple(domains or ())
    if len(set(requested)) != len(requested):
        raise ValueError("SkillsBench domain selection contains duplicates")
    selected_domains = requested or tuple(split.domains)
    unknown = set(selected_domains) - set(split.domains)
    if unknown:
        raise ValueError(f"unknown SkillsBench domains: {sorted(unknown)}")
    selected = {
        task.task_id
        for name in selected_domains
        for batch in split.domains[name].batches
        for task in batch.tasks
    }
    selected.update(task.task_id for name in selected_domains for task in split.domains[name].test)
    universe = sorted(split.train_task_ids | split.test_task_ids)
    return tuple(task_id for task_id in universe if task_id in selected)


def select_task_ids(
    universe: tuple[str, ...],
    *,
    task_range: str | None = None,
    tasks: Iterable[str] | None = None,
) -> tuple[str, ...]:
    explicit = tuple(tasks or ())
    if task_range and explicit:
        raise ValueError("cannot combine --task-range with --tasks")
    if task_range:
        match = re.fullmatch(r"([1-9][0-9]*)-([1-9][0-9]*)", task_range.strip())
        if not match:
            raise ValueError(f"invalid one-based task range: {task_range}")
        start, end = (int(value) for value in match.groups())
        if start > end or end > len(universe):
            raise ValueError(f"task range {task_range} is outside 1-{len(universe)}")
        return universe[start - 1 : end]
    if explicit:
        if len(set(explicit)) != len(explicit):
            raise ValueError("explicit task list contains duplicates")
        unknown = set(explicit) - set(universe)
        if unknown:
            raise ValueError(f"unknown task ids: {sorted(unknown)}")
        selected = set(explicit)
        return tuple(task_id for task_id in universe if task_id in selected)
    return universe


def build_shard_manifest(
    universe: tuple[str, ...],
    assignment: tuple[str, ...],
    *,
    universe_hash: str | None = None,
) -> dict[str, Any]:
    if len(set(universe)) != len(universe) or tuple(sorted(universe)) != universe:
        raise ValueError("task universe must be unique and canonically sorted")
    if len(set(assignment)) != len(assignment) or not set(assignment) <= set(universe):
        raise ValueError("shard assignment must be a unique subset of the universe")
    canonical_assignment = tuple(task_id for task_id in universe if task_id in set(assignment))
    return {
        "schema_version": 1,
        "benchmark": "skillsbench",
        "runner_version": TASK_RUNNER_VERSION,
        "universe_hash": universe_hash or _stable_hash(list(universe)),
        "assignment_hash": _stable_hash(list(canonical_assignment)),
        "universe_task_ids": list(universe),
        "task_ids": list(canonical_assignment),
    }


def experiment_config_hash(
    settings: SkillLiftSkillsBenchTasksSettings,
    universe: tuple[str, ...],
    coordinator_config: CoordinatorConfig,
) -> str:
    source_paths = tuple(sorted((settings.project_root / "skilllift" / "skilllift").rglob("*.py")))
    source_paths += tuple(sorted((settings.project_root / "skilllift_eval").rglob("*.py")))
    source_paths += (
        settings.project_root / "scripts" / "skillsbench_tasks.py",
        settings.patch_source,
    )
    return _stable_hash(
        {
            "runner_version": TASK_RUNNER_VERSION,
            "loader_version": SKILLSBENCH_PORTFOLIO_LOADER_VERSION,
            "model": settings.model,
            "framework_model": settings.framework_model or settings.model.split("/", 1)[-1],
            "use_stream": settings.use_stream,
            "base_url": settings.base_url,
            "sandbox": settings.sandbox,
            "patch_hash": hashlib.sha256(settings.patch_source.read_bytes()).hexdigest(),
            "split_hash": hashlib.sha256(settings.split_path.read_bytes()).hexdigest(),
            "universe_hash": task_universe_hash(settings.tasks_root, universe),
            "source_hash": _source_files_hash(settings.project_root, source_paths),
            "benchmark_revision_hash": _git_worktree_hash(settings.tasks_root.parent),
            "benchflow_runtime_hash": _benchflow_runtime_hash(settings.tasks_root.parent),
            "coordinator": asdict(coordinator_config),
            "max_context_chars": settings.max_context_chars,
        }
    )


def task_universe_hash(tasks_root: Path, universe: tuple[str, ...]) -> str:
    rows = []
    for task_id in universe:
        task_root = tasks_root / task_id
        task_path = task_root / "task.md"
        skills_root = task_root / "environment" / "skills"
        if not task_path.is_file():
            raise ValueError(f"SkillsBench task is missing task.md: {task_id}")
        rows.append(
            {
                "task_id": task_id,
                "task_hash": hashlib.sha256(task_path.read_bytes()).hexdigest(),
                "portfolio_hash": portfolio_tree_hash(skills_root) if skills_root.is_dir() else None,
            }
        )
    return _stable_hash(rows)


def _source_files_hash(project_root: Path, paths: tuple[Path, ...]) -> str:
    rows = []
    for raw_path in paths:
        path = raw_path.resolve()
        try:
            label = path.relative_to(project_root.resolve()).as_posix()
        except ValueError:
            label = path.name
        rows.append({"path": label, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return _stable_hash(rows)


def _git_worktree_hash(root: Path) -> str:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout.strip()
        diff = subprocess.run(
            ["git", "diff", "--binary", "HEAD", "--"],
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout
        untracked_raw = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"cannot fingerprint benchmark git worktree: {root}") from exc
    digest = hashlib.sha256()
    digest.update(head)
    digest.update(b"\0")
    digest.update(diff)
    digest.update(b"\0")
    for raw_name in sorted(name for name in untracked_raw.split(b"\0") if name):
        path = root / raw_name.decode("utf-8", errors="surrogateescape")
        if path.is_file():
            digest.update(raw_name)
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _benchflow_runtime_hash(skillsbench_root: Path) -> str:
    candidates = sorted((skillsbench_root / ".venv" / "lib").glob("python*/site-packages/benchflow"))
    if len(candidates) != 1:
        raise ValueError(f"expected one installed BenchFlow runtime under {skillsbench_root / '.venv'}")
    runtime_root = candidates[0]
    digest = hashlib.sha256()
    for path in sorted(runtime_root.rglob("*.py")):
        digest.update(path.relative_to(runtime_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    for metadata in sorted(runtime_root.parent.glob("benchflow-*.dist-info/METADATA")):
        digest.update(metadata.parent.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(metadata.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def merge_shards(shard_roots: Iterable[Path], output_root: Path) -> dict[str, Any]:
    roots = tuple(Path(root).resolve() for root in shard_roots)
    if not roots:
        raise ValueError("at least one shard is required")
    manifests = [_read_json(root / "run_manifest.json") for root in roots]
    identity = {
        (item.get("benchmark"), item.get("runner_version"), item.get("universe_hash"))
        for item in manifests
    }
    if len(identity) != 1:
        raise ValueError("shard benchmark, universe, or runner hashes do not match")
    universe = tuple(manifests[0]["universe_task_ids"])
    rows_by_task: dict[str, dict[str, Any]] = {}
    for root, manifest in zip(roots, manifests):
        summary = _read_json(root / "summary.json")
        assigned = set(manifest["task_ids"])
        rows = summary.get("tasks")
        if not isinstance(rows, list) or {row.get("task_id") for row in rows} != assigned:
            raise ValueError(f"shard summary does not match assignment: {root}")
        for row in rows:
            task_id = row["task_id"]
            if task_id in rows_by_task:
                raise ValueError(f"duplicate task across shards: {task_id}")
            rows_by_task[task_id] = row
    missing = [task_id for task_id in universe if task_id not in rows_by_task]
    rows = [rows_by_task[task_id] for task_id in universe if task_id in rows_by_task]
    adapted = [float(row["final_score"]) for row in rows if row.get("cohort") == "adapted_budget" and row.get("final_score") is not None]
    context_skipped = [
        float(row["final_score"])
        for row in rows
        if row.get("cohort") == "adaptation_skipped_context" and row.get("final_score") is not None
    ]
    anchors = [
        float(row["adaptation_reward"])
        for row in rows
        if row.get("cohort") == "anchor_terminal" and row.get("adaptation_reward") is not None
    ]
    aggregate = {
        "status": "complete" if not missing else "partial",
        "universe_hash": manifests[0]["universe_hash"],
        "covered_tasks": len(rows),
        "missing_task_ids": missing,
        "logical_cells": sum(int(row.get("logical_cells") or 0) for row in rows),
        "physical_attempts": sum(int(row.get("physical_attempts") or 0) for row in rows),
        "anchor_terminal_count": len(anchors),
        "anchor_terminal_mean": statistics.fmean(anchors) if anchors else None,
        "adapted_budget_count": len(adapted),
        "adapted_budget_mean": statistics.fmean(adapted) if adapted else None,
        "adaptation_skipped_context_count": len(context_skipped),
        "adaptation_skipped_context_mean": statistics.fmean(context_skipped) if context_skipped else None,
        "tasks": rows,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / ("aggregate.json" if not missing else "progress.json")
    _write_json_atomic(target, aggregate)
    return aggregate


def _result_row(result: TaskEvolutionResult) -> dict[str, Any]:
    return {
        "task_id": result.task_id,
        "cohort": result.cohort,
        "stop_reason": result.stop_reason,
        "winner_hash": result.winner_portfolio.tree_hash,
        "winner_root": str(result.winner_portfolio.root),
        "adaptation_reward": result.adaptation_reward,
        "final_rewards": list(result.final_rewards),
        "final_score": result.final_score,
        "rounds_completed": result.rounds_completed,
        "logical_cells": result.logical_cells,
        "physical_attempts": result.physical_attempts,
    }


def _summary(manifest: dict[str, Any], rows: list[dict[str, Any]], *, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "universe_hash": manifest["universe_hash"],
        "assignment_hash": manifest["assignment_hash"],
        "tasks": rows,
    }


def _stable_hash(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write_once(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        existing = _read_json(path)
        existing.pop("config_hash", None)
        if existing != payload:
            raise ValueError(f"run manifest already exists with different content: {path}")
        return
    _write_json_atomic(path, payload)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
