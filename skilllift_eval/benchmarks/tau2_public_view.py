from __future__ import annotations

from typing import Any

from skilllift_eval.schemas import stable_hash


TAU2_PUBLIC_TASK_FIELDS = ("id", "domain", "user_scenario", "ticket")
TAU2_PUBLIC_VIEW_VERSION = "tau2_public_task_view_v1"
FORBIDDEN_DOMAIN = "banking_knowledge"


def tau2_public_task_view(task: dict[str, Any]) -> dict[str, Any]:
    domain = str(task.get("domain", ""))
    if domain == FORBIDDEN_DOMAIN:
        raise ValueError("banking_knowledge is not allowed in Phase 2 native task set")
    public_fields = {field: task[field] for field in TAU2_PUBLIC_TASK_FIELDS if field in task}
    removed = ["non_public_tau2_task_fields"] if set(task) - set(public_fields) else []
    view = {
        "benchmark": "tau2",
        "task_id": str(task.get("id", task.get("task_id", ""))),
        "domain": domain,
        "public_task_fields": public_fields,
        "visible_transcript_policy": "public_conversation_summary_only",
        "hidden_fields_removed": removed,
        "sanitizer_version": TAU2_PUBLIC_VIEW_VERSION,
    }
    view["feedback_hash"] = stable_hash(view)
    return view
