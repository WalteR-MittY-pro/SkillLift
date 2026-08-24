from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class TokenUsage:
    actual_usage_status: Literal["available", "unavailable"] = "unavailable"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_actual: float | str = "unavailable"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def normalize_usage(usage: dict[str, Any] | None) -> TokenUsage:
    if not usage:
        return TokenUsage(actual_usage_status="unavailable")
    prompt = usage.get("prompt_tokens") or usage.get("input_tokens")
    completion = usage.get("completion_tokens") or usage.get("output_tokens")
    total = usage.get("total_tokens")
    if total is None and prompt is not None and completion is not None:
        total = int(prompt) + int(completion)
    if prompt is None and completion is None and total is None:
        return TokenUsage(actual_usage_status="unavailable")
    return TokenUsage(
        actual_usage_status="available",
        prompt_tokens=int(prompt) if prompt is not None else None,
        completion_tokens=int(completion) if completion is not None else None,
        total_tokens=int(total) if total is not None else None,
        cost_actual=usage.get("cost", usage.get("cost_actual", "unavailable")),
    )


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)
