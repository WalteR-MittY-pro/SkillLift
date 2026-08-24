from __future__ import annotations

from typing import Any

from skilllift_eval.schemas import stable_hash


WILDCLAW_PUBLIC_TASK_FIELDS = ("task_id", "prompt", "category", "timeout_seconds")
WILDCLAW_PUBLIC_VIEW_VERSION = "wildclaw_public_task_view_v1"


def wildclaw_public_task_view(task: dict[str, Any]) -> dict[str, Any]:
    public_fields = {field: task[field] for field in WILDCLAW_PUBLIC_TASK_FIELDS if field in task}
    removed = ["non_public_task_fields"] if set(task) - set(public_fields) else []
    view = {
        "benchmark": "wildclawbench",
        "task_id": str(task.get("task_id", "")),
        "domain": task.get("category"),
        "public_task_fields": public_fields,
        "visible_transcript_policy": "agent_prompt_and_public_action_summary_only",
        "hidden_fields_removed": removed,
        "sanitizer_version": WILDCLAW_PUBLIC_VIEW_VERSION,
    }
    view["feedback_hash"] = stable_hash(view)
    return view
