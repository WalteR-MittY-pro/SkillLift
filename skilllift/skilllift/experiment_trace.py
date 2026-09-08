from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .schemas import OracleFeedback


MAX_LOG_CHARS = 6000
MAX_EXCERPT_CHARS = 1200
SAFE_SCORE_KEYS = {
    "overall_score",
    "files_created",
    "strict_ordered_ratio",
    "unordered_recall",
    "unordered_precision",
    "unordered_f1",
    "output_dir_exists",
    "all_expected_files_present",
    "no_duplicate_or_extra_files",
}
SECRET_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)((?:api[_-]?key|apiKey|token|secret|password|passwd|authorization)\s*[:=]\s*)(['\"]?)[^'\"\s,;}]+"),
    re.compile(r"\bsk-[A-Za-z0-9]{12,}\b"),
]
ERROR_LINE_RE = re.compile(
    r"Traceback|Error|Exception|JSONDecodeError|timeout|Timed out|No such file|"
    r"FileNotFoundError|permission denied|missing result|required output",
    re.IGNORECASE,
)


def summarize_oracle_output(output_dir: Path | str, feedback_level: int) -> OracleFeedback:
    out = Path(output_dir)
    scores = load_public_score(out)
    logs = "\n".join([_read_text(out / "agent.log"), _read_text(out / "gateway.log")])
    feedback = build_feedback_from_failure(scores, logs, feedback_level)
    llm_judge_failure = detect_llm_judge_failure(out / "score.json")
    if llm_judge_failure:
        feedback.metadata["failure_type"] = llm_judge_failure
        feedback.summary = llm_judge_failure if feedback_level <= 0 else f"{llm_judge_failure}: {feedback.summary}"
    if feedback_level >= 2:
        feedback.produced_files = _produced_files(out)
        feedback.missing_files = _missing_files(scores)
        feedback.stderr_excerpt = _summarize_errors(logs)
    if feedback_level >= 3:
        feedback.traceback_summary = _summarize_traceback(logs)
        feedback.metadata["chat_summary"] = _summarize_chat(out / "chat.jsonl")
    return feedback


def load_public_score(output_dir: Path | str) -> dict[str, float]:
    path = Path(output_dir) / "score.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return sanitize_scores(payload)


def detect_llm_judge_failure(score_path: Path) -> str:
    if not score_path.exists():
        return ""
    try:
        payload = json.loads(score_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    text = json.dumps(payload, ensure_ascii=False).lower()
    if "skipped: no judge api config" in text or "\"llm_judge_model\": \"\"" in text:
        return "llm_judge_not_configured"
    if "llm_judge_failed" in text:
        return "llm_judge_failed"
    return ""


def build_feedback_from_failure(scores: dict[str, float], logs: str, feedback_level: int) -> OracleFeedback:
    failure_stage = classify_failure_from_payload(scores, logs, missing_score=not scores)
    summary = failure_stage if feedback_level <= 0 else _score_summary(scores, failure_stage)
    metadata = {"failure_type": failure_stage}
    if feedback_level >= 1:
        metadata["scores"] = scores
    return OracleFeedback(summary=summary, metadata=metadata)


def redact(text: str) -> str:
    safe = text
    for pattern in SECRET_PATTERNS:
        safe = pattern.sub(_redacted_match, safe)
    safe = re.sub(r"(?i)hidden[_ -]?answer[^,\n}]*", "hidden_answer=[REDACTED]", safe)
    safe = re.sub(r"(?i)ground[_ -]?truth[^,\n}]*", "ground_truth=[REDACTED]", safe)
    return safe


def sanitize_scores(payload: dict[str, Any]) -> dict[str, float]:
    sanitized: dict[str, float] = {}
    for key, value in payload.items():
        if str(key) not in SAFE_SCORE_KEYS or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            sanitized[str(key)] = float(value)
    return sanitized


def classify_failure_from_payload(scores: dict[str, Any], logs: str, missing_score: bool = False) -> str:
    if missing_score:
        return "grader_missing_score"
    if float(scores.get("overall_score", 0.0) or 0.0) >= 1.0:
        # A perfect grader score is authoritative: the word "error" shows up
        # in healthy agent transcripts and must not reclassify the run.
        return "success"
    combined = f"{json.dumps(scores, ensure_ascii=False)}\n{logs}".lower()
    if "docker" in combined and ("failed" in combined or "cannot connect" in combined):
        return "container_start"
    if "skill" in combined and ("not found" in combined or "load" in combined or "missing" in combined):
        return "skill_load"
    if "traceback" in combined or "exception" in combined or "error" in combined:
        return "agent_execution"
    if float(scores.get("overall_score", 0.0) or 0.0) < 1.0:
        return "task_low_score"
    return "success"


def _read_text(path: Path, limit: int = MAX_LOG_CHARS) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return redact(path.read_text(encoding="utf-8", errors="replace")[-limit:])


def _produced_files(output_dir: Path) -> list[str]:
    files: list[str] = []
    for root in [output_dir / "task_output", output_dir / "results"]:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and "gt" not in path.relative_to(root).parts:
                files.append(str(path.relative_to(root)))
    return sorted(files)[:200]


def _missing_files(scores: dict[str, float]) -> list[str]:
    if scores and scores.get("all_expected_files_present", 1.0) < 1.0:
        return ["required output files"]
    return []


def _summarize_errors(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if ERROR_LINE_RE.search(line)]
    return "\n".join(lines[-12:])[:MAX_EXCERPT_CHARS]


def _summarize_traceback(text: str) -> str:
    index = text.lower().rfind("traceback")
    return "" if index == -1 else text[index:index + MAX_EXCERPT_CHARS]


def _summarize_chat(path: Path) -> str:
    if not path.exists():
        return ""
    lines = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]:
        lines.append(redact(raw[:300]))
    return "\n".join(lines)[-MAX_EXCERPT_CHARS:]


def _score_summary(scores: dict[str, float], failure_stage: str) -> str:
    if not scores:
        return failure_stage
    parts = [f"{key}={value:.4g}" for key, value in sorted(scores.items())]
    return f"{failure_stage}: " + ", ".join(parts)


def _redacted_match(match: re.Match[str]) -> str:
    if match.lastindex:
        prefix = match.group(1)
        quote = match.group(2) if match.lastindex >= 2 and match.group(2) else ""
        return f"{prefix}{quote}[REDACTED]"
    return "[REDACTED]"
