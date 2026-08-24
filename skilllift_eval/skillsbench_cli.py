from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from skilllift_eval.runners.skillsbench_adapter import SkillsBenchCommand


EXPECTED_COUNTS = (8, 9, 36, 51)


@dataclass(frozen=True)
class TaskRef:
    task_id: str
    task_md_sha256: str


@dataclass(frozen=True)
class BatchSplit:
    batch_id: str
    tasks: tuple[TaskRef, ...]

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(task.task_id for task in self.tasks)


@dataclass(frozen=True)
class DomainSplit:
    batches: tuple[BatchSplit, ...]
    test: tuple[TaskRef, ...]


@dataclass(frozen=True)
class SkillsBenchSplit:
    version: int
    domains: dict[str, DomainSplit]

    @property
    def train_task_ids(self) -> set[str]:
        return {
            task.task_id
            for domain in self.domains.values()
            for batch in domain.batches
            for task in batch.tasks
        }

    @property
    def test_task_ids(self) -> set[str]:
        return {task.task_id for domain in self.domains.values() for task in domain.test}


def load_and_validate_split(
    split_path: Path,
    tasks_root: Path,
    *,
    expected_counts: tuple[int, int, int, int] | None = EXPECTED_COUNTS,
) -> SkillsBenchSplit:
    payload = json.loads(split_path.read_text(encoding="utf-8"))
    domains = {
        category: DomainSplit(
            batches=tuple(
                BatchSplit(
                    batch_id=str(batch["batch_id"]),
                    tasks=tuple(TaskRef(**task) for task in batch["tasks"]),
                )
                for batch in data["batches"]
            ),
            test=tuple(TaskRef(**task) for task in data["test"]),
        )
        for category, data in payload["domains"].items()
    }
    split = SkillsBenchSplit(version=int(payload["version"]), domains=domains)
    _validate_split(split, tasks_root, expected_counts)
    return split


def _validate_split(
    split: SkillsBenchSplit,
    tasks_root: Path,
    expected_counts: tuple[int, int, int, int] | None,
) -> None:
    train_occurrences = [
        task.task_id
        for domain in split.domains.values()
        for batch in domain.batches
        for task in batch.tasks
    ]
    test_occurrences = [task.task_id for domain in split.domains.values() for task in domain.test]
    if len(train_occurrences) != len(set(train_occurrences)):
        raise ValueError("duplicate train task in split")
    if len(test_occurrences) != len(set(test_occurrences)):
        raise ValueError("duplicate test task in split")
    overlap = set(train_occurrences) & set(test_occurrences)
    if overlap:
        raise ValueError(f"train/test overlap: {sorted(overlap)}")
    for domain in split.domains.values():
        for batch in domain.batches:
            if len(batch.tasks) != 4:
                raise ValueError(f"batch {batch.batch_id} must contain exactly 4 tasks")
    for category, domain in split.domains.items():
        refs = [task for batch in domain.batches for task in batch.tasks] + list(domain.test)
        for task in refs:
            task_path = tasks_root / task.task_id / "task.md"
            if not task_path.is_file():
                raise ValueError(f"missing task.md: {task_path}")
            task_bytes = task_path.read_bytes()
            actual_hash = hashlib.sha256(task_bytes).hexdigest()
            if actual_hash != task.task_md_sha256:
                raise ValueError(f"task.md hash mismatch for {task.task_id}")
            text = task_bytes.decode("utf-8")
            frontmatter = yaml.safe_load(text.split("---", 2)[1]) if text.startswith("---") else {}
            actual_category = (frontmatter.get("metadata") or {}).get("category")
            if actual_category != category:
                raise ValueError(
                    f"task category mismatch for {task.task_id}: expected {category}, got {actual_category}"
                )
    if expected_counts is not None:
        actual = (
            len(split.domains),
            sum(len(domain.batches) for domain in split.domains.values()),
            len(train_occurrences),
            len(test_occurrences),
        )
        if actual != expected_counts:
            raise ValueError(f"split counts {actual} do not match expected {expected_counts}")
        disk_tasks = {path.parent.name for path in tasks_root.glob("*/task.md")}
        covered = set(train_occurrences) | set(test_occurrences)
        if covered != disk_tasks:
            raise ValueError(
                f"split task coverage mismatch; missing={sorted(disk_tasks - covered)}, extra={sorted(covered - disk_tasks)}"
            )


def build_execution_plan(
    *,
    split: SkillsBenchSplit,
    categories: list[str],
    tasks_root: Path,
    run_root: Path,
    bench_executable: Path,
    model: str,
    sandbox: str,
) -> dict[str, Any]:
    unknown = set(categories) - set(split.domains)
    if unknown:
        raise ValueError(f"unknown SkillsBench categories: {sorted(unknown)}")
    tasks_root = tasks_root.resolve()
    run_root = run_root.resolve()
    bench_executable = bench_executable.absolute()
    train: list[dict[str, Any]] = []
    test: list[dict[str, Any]] = []
    for category in categories:
        domain = split.domains[category]
        for batch in domain.batches:
            batch_root = run_root / "train" / category / batch.batch_id
            commands = []
            for slot in range(4):
                skill_id = f"s{slot:03d}_v001"
                for task_id in batch.task_ids:
                    command = SkillsBenchCommand(
                        bench_executable=bench_executable,
                        tasks_dir=tasks_root,
                        task_ids=(task_id,),
                        agent="openhands-sse",
                        model=model,
                        sandbox=sandbox,
                        skill_mode="with-skill",
                        skills_dir=(batch_root / "round-0" / "candidates" / skill_id).resolve(),
                        jobs_dir=(batch_root / "round-0" / "jobs" / skill_id / task_id / "attempt-1").resolve(),
                    )
                    commands.append(command.to_dict())
            train.append(
                {
                    "category": category,
                    "batch_id": batch.batch_id,
                    "commands": commands,
                    "output_path": str((batch_root / "round-0" / "round_result.json").resolve()),
                }
            )
        final_skill_root = run_root / "final_skills" / category
        for task in domain.test:
            task_root = run_root / "test" / category / task.task_id
            command = SkillsBenchCommand(
                bench_executable=bench_executable,
                tasks_dir=tasks_root,
                task_ids=(task.task_id,),
                agent="openhands-sse",
                model=model,
                sandbox=sandbox,
                skill_mode="with-skill",
                skills_dir=final_skill_root.resolve(),
                jobs_dir=(task_root / "jobs").resolve(),
            )
            test.append(
                {
                    "category": category,
                    "task_id": task.task_id,
                    "command": command.to_dict(),
                    "output_path": str((task_root / "score.json").resolve()),
                }
            )
    all_train = split.train_task_ids
    all_test = split.test_task_ids
    summary = {
        "domains": len(split.domains),
        "train_batches": sum(len(domain.batches) for domain in split.domains.values()),
        "train_tasks": len(all_train),
        "test_tasks": len(all_test),
        "overlap": len(all_train & all_test),
        "missing": 0,
        "status": "ready",
    }
    return {"summary": summary, "train": train, "test": test}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_config(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("SkillsBench config must be a YAML object")
    return payload


def _resolve_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _resolve_executable_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    return path.absolute() if path.is_absolute() else (project_root / path).absolute()


def _run_check(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    config = _load_config(config_path)
    project_root = _resolve_path(config_path.parent, str(config.get("project_root", "..")))
    benchmark = config["skillsbench"]
    split = load_and_validate_split(
        _resolve_path(project_root, benchmark["split_path"]),
        _resolve_path(project_root, benchmark["tasks_root"]),
    )
    categories = list(split.domains) if args.categories == "all" else [item.strip() for item in args.categories.split(",") if item.strip()]
    run_root = _resolve_path(project_root, args.run_root or config["run_root"])
    plan = build_execution_plan(
        split=split,
        categories=categories,
        tasks_root=_resolve_path(project_root, benchmark["tasks_root"]),
        run_root=run_root,
        bench_executable=_resolve_executable_path(project_root, benchmark["python_executable"]),
        model=str(benchmark["model"]),
        sandbox=str(benchmark.get("sandbox", "docker")),
    )
    _write_json_atomic(run_root / "execution_plan.json", plan)
    print(json.dumps(plan["summary"], sort_keys=True))
    return 0


def _run_phase(args: argparse.Namespace) -> int:
    from skilllift_eval.runners.skilllift_skillsbench import (
        SkillLiftSkillsBenchRunner,
        SkillLiftSkillsBenchSettings,
    )

    config_path = Path(args.config).resolve()
    config = _load_config(config_path)
    project_root = _resolve_path(config_path.parent, str(config.get("project_root", "..")))
    _load_env_files(project_root)
    benchmark = config["skillsbench"]
    tasks_root = _resolve_path(project_root, benchmark["tasks_root"])
    split = load_and_validate_split(
        _resolve_path(project_root, benchmark["split_path"]), tasks_root
    )
    categories = list(split.domains) if args.categories == "all" else [item.strip() for item in args.categories.split(",") if item.strip()]
    unknown = set(categories) - set(split.domains)
    if unknown:
        raise ValueError(f"unknown SkillsBench categories: {sorted(unknown)}")
    settings = SkillLiftSkillsBenchSettings(
        project_root=project_root,
        run_root=_resolve_path(project_root, args.run_root or config["run_root"]),
        tasks_root=tasks_root,
        python_executable=_resolve_executable_path(project_root, benchmark["python_executable"]),
        model=str(benchmark["model"]),
        sandbox=str(benchmark.get("sandbox", "docker")),
        trials=int(benchmark.get("trials", 3)),
        base_url_env=str(benchmark["base_url_env"]),
        api_key_env=str(benchmark["api_key_env"]),
        skilllift=dict(config["skilllift"]),
        resume=bool(args.resume),
    )
    runner = SkillLiftSkillsBenchRunner(settings)
    result = runner.train(split, categories) if args.phase == "train" else runner.test(split, categories)
    print(json.dumps({"status": result["status"], "phase": args.phase}, sort_keys=True))
    return 0


def _load_env_files(project_root: Path) -> None:
    for path in (project_root / ".env", project_root / "skillsbench" / ".env"):
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _run_benchflow(argv: list[str]) -> int:
    register_openhands_sse = __import__(
        "skilllift_eval.runners.skillsbench_adapter",
        fromlist=["register_openhands_sse"],
    ).register_openhands_sse
    register_openhands_sse()
    from benchflow.cli.main import app

    app(args=argv, prog_name="bench", standalone_mode=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "benchflow":
        return _run_benchflow(argv[1:])
    parser = argparse.ArgumentParser(prog="skilllift-skillsbench")
    parser.add_argument("--config", required=True)
    parser.add_argument("--categories", default="all")
    parser.add_argument("--phase", choices=("check", "train", "test"), required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-root")
    args = parser.parse_args(argv)
    if args.phase == "check":
        return _run_check(args)
    return _run_phase(args)


if __name__ == "__main__":
    raise SystemExit(main())
