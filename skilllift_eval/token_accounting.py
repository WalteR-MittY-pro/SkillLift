from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

TOKEN_USAGE_FILE = "token_usage.json"
TOKEN_USAGE_SUMMARY_FILE = "token_usage_summary.json"
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "total_tokens",
    "request_count",
)
_TASK_DIR_RE = re.compile(r"^\d{2}-\d{2}$")
_FRAMEWORK_ARTIFACT_GLOBS = (
    "skills/**/*.json",
    "receipts/*.json",
    "verifier_scores/*.json",
    "receipt_revision_attempts/**/prompt.txt",
    "receipt_revision_attempts/**/parsed_candidate.json",
)


def normalize_usage(value: dict[str, Any] | None) -> dict[str, int]:
    raw = value if isinstance(value, dict) else {}
    usage = {field: _nonnegative_int(raw.get(field, 0)) for field in USAGE_FIELDS}
    if usage["total_tokens"] == 0:
        usage["total_tokens"] = sum(
            usage[field]
            for field in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
            )
        )
    return usage


def aggregate_usage(values: Iterable[dict[str, Any] | None]) -> dict[str, int]:
    total = {field: 0 for field in USAGE_FIELDS}
    for value in values:
        usage = normalize_usage(value)
        for field in USAGE_FIELDS:
            total[field] += usage[field]
    return total


def estimate_skilllift_task_usage(
    task_dir: Path | str,
    *,
    budget_multiplier: float = 2.0,
) -> dict[str, Any]:
    root = Path(task_dir).resolve()
    if budget_multiplier <= 0:
        raise ValueError("budget_multiplier must be > 0")

    usage_paths = sorted((root / "raw").rglob("usage.json"))
    oracle_usage = aggregate_usage(read_json(path) for path in usage_paths)
    framework_paths = _framework_artifact_paths(root / "skilllift")
    framework_chars = sum(path.stat().st_size for path in framework_paths)
    framework_tokens = math.ceil(framework_chars / 4)
    base_tokens = framework_tokens + oracle_usage["total_tokens"]
    limit_tokens = math.ceil(base_tokens * budget_multiplier)

    config_path = root / "skilllift" / "config.json"
    config = read_json(config_path) if config_path.exists() else {}
    return {
        "schema_version": "skilllift_reference_token_usage_v1",
        "task_key": root.name,
        "measurement": {
            "framework": {
                "total_tokens": framework_tokens,
                "estimated": True,
                "method": "persisted_framework_artifact_bytes_div_4",
                "artifact_bytes": framework_chars,
                "artifact_file_count": len(framework_paths),
                "note": (
                    "Historical framework API usage was not persisted. This is a "
                    "four-bytes-per-token estimate over selected repeated framework artifacts."
                ),
            },
            "oracle": {
                **oracle_usage,
                "estimated": False,
                "method": "sum_unique_raw_usage_json",
                "usage_file_count": len(usage_paths),
                "includes_cache_tokens": True,
            },
            "total_tokens": base_tokens,
        },
        "budget": {
            "base_tokens": base_tokens,
            "multiplier": budget_multiplier,
            "limit_tokens": limit_tokens,
        },
        "reference_rounds": {
            "outer_rounds": _nonnegative_int(config.get("outer_rounds", 0)),
            "mode_a_iters": _nonnegative_int(config.get("mode_a_iters", 0)),
            "mode_b_iters": _nonnegative_int(config.get("mode_b_iters", 0)),
        },
    }


def write_skilllift_reference_usage(
    task_dir: Path | str,
    *,
    budget_multiplier: float = 2.0,
) -> Path:
    root = Path(task_dir).resolve()
    payload = estimate_skilllift_task_usage(
        root,
        budget_multiplier=budget_multiplier,
    )
    path = root / TOKEN_USAGE_FILE
    write_json(path, payload)
    return path


def backfill_skilllift_reference_usage(
    reference_root: Path | str,
    *,
    budget_multiplier: float = 2.0,
) -> list[Path]:
    root = Path(reference_root).resolve()
    paths = []
    for task_dir in sorted(root.iterdir() if root.exists() else []):
        if not task_dir.is_dir() or not _TASK_DIR_RE.fullmatch(task_dir.name):
            continue
        if not (task_dir / "skilllift").is_dir():
            continue
        paths.append(
            write_skilllift_reference_usage(
                task_dir,
                budget_multiplier=budget_multiplier,
            )
        )
    return paths


def load_reference_budget(
    reference_root: Path | str,
    task_key: str,
    *,
    multiplier: float = 2.0,
) -> dict[str, Any]:
    if multiplier <= 0:
        raise ValueError("multiplier must be > 0")
    root = Path(reference_root).resolve()
    if root.is_file():
        base_tokens = _summary_base_tokens(root, task_key)
        path = root
    else:
        path = root / task_key / TOKEN_USAGE_FILE
        if not path.exists():
            raise FileNotFoundError(
                f"reference token usage not found for task {task_key}: {path}"
            )
        payload = read_json(path)
        base_tokens = _nonnegative_int(
            payload.get("measurement", {}).get("total_tokens")
            or payload.get("budget", {}).get("base_tokens")
        )
    if base_tokens < 1:
        raise ValueError(f"reference token usage has no positive base_tokens: {path}")
    return {
        "task_key": task_key,
        "reference_path": str(path),
        "base_tokens": base_tokens,
        "multiplier": multiplier,
        "limit_tokens": math.ceil(base_tokens * multiplier),
    }


def task_key_from_path(task_path: Path | str) -> str:
    path = Path(task_path)
    category_match = re.match(r"^(\d+)", path.parent.name)
    task_match = re.search(r"_task_(\d+)(?:_|$)", path.stem)
    if not category_match or not task_match:
        raise ValueError(f"cannot derive NN-NN task key from {path}")
    return f"{int(category_match.group(1)):02d}-{int(task_match.group(1)):02d}"


def _summary_base_tokens(summary_path: Path, task_key: str) -> int:
    payload = read_json(summary_path)
    per_task = payload.get("per_task")
    if not isinstance(per_task, list):
        raise ValueError(f"summary reference missing per_task list: {summary_path}")
    for row in per_task:
        if not isinstance(row, dict) or str(row.get("task_id") or "").strip() != task_key:
            continue
        return _nonnegative_int(
            row.get("combined_total_tokens")
            or (
                _nonnegative_int(row.get("oracle_total_tokens"))
                + _nonnegative_int(row.get("framework_total_tokens_est"))
            )
        )
    raise FileNotFoundError(
        f"reference token usage not found for task {task_key}: {summary_path}"
    )


def _framework_artifact_paths(skilllift_root: Path) -> list[Path]:
    paths: dict[Path, Path] = {}
    for pattern in _FRAMEWORK_ARTIFACT_GLOBS:
        for path in skilllift_root.glob(pattern):
            if path.is_file():
                paths[path.resolve()] = path
    return sorted(paths.values())


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
