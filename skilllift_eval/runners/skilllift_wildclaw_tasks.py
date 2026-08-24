from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from skilllift_eval.model_endpoints import endpoint_config_hash, resolve_endpoints


BATCH_RUNNER_VERSION = "wildclaw_task_portfolio_batch_v1"
ALL_CATEGORIES = tuple(range(1, 7))


@dataclass(frozen=True)
class WildClawTask:
    category: int
    task_id: str
    path: Path


@dataclass(frozen=True)
class WildClawBatchSettings:
    project_root: Path
    wildclaw_root: Path
    run_root: Path
    config_path: Path
    model: str
    categories: tuple[int, ...] = ALL_CATEGORIES


def discover_wildclaw_tasks(wildclaw_root: Path, categories: tuple[int, ...]) -> tuple[WildClawTask, ...]:
    selected = tuple(sorted(set(categories)))
    if not selected or any(category not in ALL_CATEGORIES for category in selected):
        raise ValueError("WildClaw categories must be a non-empty subset of 1-6")
    tasks_root = wildclaw_root.resolve() / "tasks"
    tasks: list[WildClawTask] = []
    seen: set[str] = set()
    for category in selected:
        matches = sorted(path for path in tasks_root.glob(f"{category:02d}_*") if path.is_dir())
        if len(matches) != 1:
            raise ValueError(f"WildClaw category {category:02d} must resolve to exactly one directory")
        paths = sorted(matches[0].glob("*task_*.md"))
        if not paths:
            raise ValueError(f"WildClaw category {category:02d} contains no task files")
        for path in paths:
            task_id = _task_id(path)
            if task_id in seen:
                raise ValueError(f"duplicate WildClaw task id: {task_id}")
            seen.add(task_id)
            tasks.append(WildClawTask(category, task_id, path.resolve()))
    return tuple(tasks)


def run_wildclaw_batch(settings: WildClawBatchSettings) -> dict[str, Any]:
    project_root = settings.project_root.resolve()
    run_root = settings.run_root.resolve()
    config_path = settings.config_path.resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("skilllift_eval config must be a YAML object")
    endpoint = resolve_endpoints(config, [settings.model])[settings.model]
    tasks = discover_wildclaw_tasks(settings.wildclaw_root, settings.categories)
    manifest = {
        "schema_version": 1,
        "runner_version": BATCH_RUNNER_VERSION,
        "benchmark": "wildclawbench",
        "model": settings.model,
        "endpoint_config_hash": endpoint_config_hash(endpoint),
        "config_hash": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "categories": sorted(set(settings.categories)),
        "task_ids": [task.task_id for task in tasks],
    }
    run_root.mkdir(parents=True, exist_ok=True)
    _write_once(run_root / "batch_manifest.json", manifest)

    rows: list[dict[str, Any]] = []
    for task in tasks:
        cell_root = run_root / "cells" / task.task_id
        command = _task_command(config_path, settings.model, cell_root, task.task_id)
        try:
            completed = subprocess.run(
                command,
                cwd=project_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            _write_text(cell_root / "launcher.stdout.log", completed.stdout)
            _write_text(cell_root / "launcher.stderr.log", completed.stderr)
            cell_summary = _read_optional_json(cell_root / "cell_summary.json")
            succeeded = completed.returncode == 0 and cell_summary.get("status") == "SUCCEEDED"
            row = {
                "task_id": task.task_id,
                "category": task.category,
                "task_path": str(task.path),
                "cell_root": str(cell_root),
                "status": "succeeded" if succeeded else "failed",
                "returncode": completed.returncode,
                "score": cell_summary.get("mean_score"),
                "stop_reason": cell_summary.get("stop_reason"),
                "winner_portfolio_root": cell_summary.get("winner_portfolio_root"),
                "resume": cell_summary.get("resume"),
            }
        except OSError as exc:
            row = {
                "task_id": task.task_id,
                "category": task.category,
                "task_path": str(task.path),
                "cell_root": str(cell_root),
                "status": "failed",
                "returncode": None,
                "error": str(exc),
            }
        rows.append(row)
        _write_json(run_root / "batch_summary.json", _summary(manifest, rows, len(tasks)))
    return _summary(manifest, rows, len(tasks))


def _task_command(
    config_path: Path,
    model: str,
    cell_root: Path,
    task_id: str,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "skilllift_eval.cli",
        "run",
        "skilllift",
        "--benchmark",
        "wildclawbench",
        "--model",
        model,
        "--config",
        str(config_path),
        "--run-root",
        str(cell_root),
        "--tasks.mode",
        "single",
        "--tasks.filter",
        task_id,
    ]


def _summary(manifest: dict[str, Any], rows: list[dict[str, Any]], expected: int) -> dict[str, Any]:
    succeeded = sum(row["status"] == "succeeded" for row in rows)
    status = "in_progress"
    if len(rows) == expected:
        status = "completed" if succeeded == expected else "completed_with_failures"
    return {
        "status": status,
        "model": manifest["model"],
        "categories": manifest["categories"],
        "expected_tasks": expected,
        "completed_tasks": len(rows),
        "succeeded_tasks": succeeded,
        "failed_tasks": len(rows) - succeeded,
        "tasks": rows,
    }


def _task_id(path: Path) -> str:
    match = re.search(r"^id:\s*(.+?)\s*$", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    if not match:
        raise ValueError(f"WildClaw task is missing frontmatter id: {path}")
    task_id = match.group(1).strip()
    if not task_id or "/" in task_id or "\\" in task_id:
        raise ValueError(f"unsafe WildClaw task id: {task_id!r}")
    return task_id


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_once(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != payload:
            raise ValueError(f"WildClaw batch resume manifest mismatch: {path}")
        return
    _write_json(path, payload)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
