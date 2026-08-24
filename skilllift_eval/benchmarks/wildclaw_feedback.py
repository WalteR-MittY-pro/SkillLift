from __future__ import annotations

import re
from typing import Any

from skilllift_eval.schemas import stable_hash


WILDCLAW_FEEDBACK_VERSION = "wildclaw_feedback_sanitizer_v1"
PRIVATE_PATH_PATTERN = re.compile(r"(/Users/[^\s:,'\"]+|/private/[^\s:,'\"]+|/var/folders/[^\s:,'\"]+)")
SECRET_PATTERN = re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\s*=\s*[^\s,;]+")
FORBIDDEN_TEXT = (
    "hidden evaluator",
    "hidden assertion",
    "gold workspace",
    "gold state",
    "container secret",
    "target db",
)


def sanitize_wildclaw_feedback(raw: dict[str, Any], *, max_excerpt_chars: int = 500) -> dict[str, Any]:
    public_feedback = {
        "transcript": _sanitize_transcript(raw.get("transcript") or []),
    }
    automated_check = _sanitize_automated_check(raw.get("automated_check") or {}, max_excerpt_chars)
    if automated_check:
        public_feedback["automated_check"] = automated_check
    if raw.get("workspace_diff_summary"):
        public_feedback["workspace_diff_summary"] = _clean_text(str(raw["workspace_diff_summary"]))

    result = {
        "benchmark": "wildclawbench",
        "task_id": str(raw.get("task_id", "")),
        "domain": raw.get("domain") or raw.get("category"),
        "public_feedback": public_feedback,
        "score": raw.get("score"),
        "visible_transcript_policy": "agent_visible_prompt_action_stdout_stderr_summary",
        "hidden_fields_removed": _removed_categories(raw),
        "sanitizer_version": WILDCLAW_FEEDBACK_VERSION,
    }
    result["feedback_hash"] = stable_hash(result)
    return result


def _sanitize_transcript(transcript: list[dict[str, Any]]) -> list[dict[str, str]]:
    public: list[dict[str, str]] = []
    for entry in transcript:
        visibility = entry.get("visibility")
        if visibility == "agent_visible":
            public.append(
                {
                    "role": _clean_text(str(entry.get("role", ""))),
                    "content": _clean_text(str(entry.get("content", ""))),
                }
            )
        elif visibility == "agent_action":
            public.append({"action_summary": _clean_text(str(entry.get("action_summary", "")))})
        elif visibility == "public_stream":
            item: dict[str, str] = {}
            if "stdout" in entry:
                item["stdout"] = _clean_text(str(entry["stdout"]))
            if "stderr" in entry:
                item["stderr"] = _clean_text(str(entry["stderr"]))
            if item:
                public.append(item)
    return public


def _sanitize_automated_check(check: dict[str, Any], max_excerpt_chars: int) -> dict[str, str]:
    if not check:
        return {}
    category = str(check.get("category") or check.get("status") or "unknown_failure")
    excerpt = _clean_text(str(check.get("stderr") or check.get("stdout") or ""))[:max_excerpt_chars]
    result = {
        "category": _clean_text(category),
        "public_error_excerpt": excerpt,
    }
    if check.get("public_file_diff_summary"):
        result["public_file_diff_summary"] = _clean_text(str(check["public_file_diff_summary"]))
    artifact_path = str(check.get("artifact_path") or "")
    if artifact_path and not artifact_path.startswith("/"):
        result["artifact_path"] = _clean_text(artifact_path)
    return result


def _clean_text(value: str) -> str:
    value = PRIVATE_PATH_PATTERN.sub("[redacted-path]", value)
    value = SECRET_PATTERN.sub("[redacted-secret]", value)
    for phrase in FORBIDDEN_TEXT:
        value = re.sub(re.escape(phrase), "[redacted]", value, flags=re.IGNORECASE)
    return value


def _removed_categories(raw: dict[str, Any]) -> list[str]:
    allowed = {"task_id", "domain", "category", "score", "transcript", "automated_check", "workspace_diff_summary"}
    return ["non_public_feedback_fields"] if set(raw) - allowed else []
