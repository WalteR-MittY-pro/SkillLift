from __future__ import annotations

import re
from typing import Any

from skilllift_eval.schemas import stable_hash


TAU2_FEEDBACK_VERSION = "tau2_feedback_sanitizer_v1"
FORBIDDEN_ROLES = {"evaluator", "oracle", "gold", "system_hidden"}
FORBIDDEN_TEXT_PATTERNS = (
    re.compile(r"\bevaluation[_ -]?criteria\b", re.IGNORECASE),
    re.compile(r"\bgold\s+actions?\b", re.IGNORECASE),
    re.compile(r"\btarget[_ -]?db\b", re.IGNORECASE),
    re.compile(r"\bhidden\s+assertions?\b", re.IGNORECASE),
    re.compile(r"\b(gt|ground\s*truth)[_ -]?resolution[_ -]?steps\b", re.IGNORECASE),
)


def sanitize_tau2_feedback(raw: dict[str, Any], *, token_budget: str) -> dict[str, Any]:
    cap = _token_budget_to_chars(token_budget)
    messages = []
    truncated = False
    for message in raw.get("messages") or []:
        role = str(message.get("role", ""))
        if role in FORBIDDEN_ROLES:
            continue
        content = _clean_text(str(message.get("content", "")))
        if len(content) > cap:
            content = content[:cap]
            truncated = True
        messages.append({"role": role, "content": content})

    public_feedback = {"messages": messages}
    if raw.get("public_summary"):
        summary = _clean_text(str(raw["public_summary"]))
        if len(summary) > cap:
            summary = summary[:cap]
            truncated = True
        public_feedback["public_summary"] = summary

    result = {
        "benchmark": "tau2",
        "task_id": str(raw.get("task_id", raw.get("id", ""))),
        "domain": raw.get("domain"),
        "public_feedback": public_feedback,
        "score": raw.get("score"),
        "visible_transcript_policy": "public_conversation_summary_only",
        "hidden_fields_removed": _removed_categories(raw),
        "sanitizer_version": TAU2_FEEDBACK_VERSION,
        "context_truncated": truncated,
    }
    result["feedback_hash"] = stable_hash(result)
    return result


def _clean_text(value: str) -> str:
    for pattern in FORBIDDEN_TEXT_PATTERNS:
        value = pattern.sub("[redacted]", value)
    return value


def _token_budget_to_chars(token_budget: str) -> int:
    try:
        tokens = int(token_budget)
    except ValueError:
        return 4000
    return max(1, tokens * 4)


def _removed_categories(raw: dict[str, Any]) -> list[str]:
    allowed = {"task_id", "id", "domain", "score", "messages", "public_summary"}
    return ["non_public_tau2_feedback_fields"] if set(raw) - allowed else []
