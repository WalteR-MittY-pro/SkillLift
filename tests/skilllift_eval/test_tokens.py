from __future__ import annotations

from skilllift_eval.tokens import TokenUsage, estimate_tokens, normalize_usage


def test_normalize_openai_style_usage() -> None:
    usage = normalize_usage({"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13})
    assert usage.actual_usage_status == "available"
    assert usage.prompt_tokens == 10
    assert usage.completion_tokens == 3
    assert usage.total_tokens == 13


def test_missing_usage_is_unavailable_not_error() -> None:
    usage = normalize_usage(None)
    assert usage == TokenUsage(actual_usage_status="unavailable")
    assert estimate_tokens("one two three") >= 1
