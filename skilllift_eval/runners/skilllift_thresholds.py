"""Boundary validation for benchmark-owned SkillLift threshold parameters."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping


def probability_param(
    params: dict[str, Any],
    key: str,
    default: float,
    *,
    warning_sink: Callable[[dict[str, Any]], None] | None = None,
) -> float:
    """Read a [0, 1] threshold, falling back with an auditable warning."""
    raw = params.get(key, default)
    try:
        if isinstance(raw, bool):
            raise ValueError("boolean is not a numeric threshold")
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        value = math.nan
    if math.isfinite(value) and 0.0 <= value <= 1.0:
        return value
    warning = {
        "event": "invalid_threshold_fallback",
        "field": key,
        "raw_value": raw if isinstance(raw, (str, int, float, bool)) and not (
            isinstance(raw, float) and not math.isfinite(raw)
        ) else repr(raw),
        "fallback_value": default,
        "valid_range": [0.0, 1.0],
        "reason": "SkillLift normalized thresholds must be in [0, 1]",
    }
    if warning_sink is not None:
        warning_sink(warning)
    return float(default)


def sanitize_probability_fields(
    target: Any,
    run_root: Path,
    defaults: Mapping[str, float],
) -> list[dict[str, Any]]:
    """Normalize CLI/config fields in-place and persist any fallback warnings."""
    params = vars(target) if hasattr(target, "__dict__") else target
    warnings: list[dict[str, Any]] = []
    for key, default in defaults.items():
        value = probability_param(params, key, default, warning_sink=warnings.append)
        if hasattr(target, key):
            setattr(target, key, value)
        else:
            params[key] = value
    if warnings:
        path = Path(run_root) / "threshold_warnings.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for warning in warnings:
                handle.write(json.dumps(warning, ensure_ascii=False, sort_keys=True) + "\n")
    return warnings
