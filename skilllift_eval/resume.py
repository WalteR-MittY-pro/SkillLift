from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .schemas import TaskRunRecord


@dataclass(frozen=True)
class ResumeInputs:
    provider_model_id: str
    endpoint_config_hash: str
    algorithm_param_hash: str
    benchmark_source_hash: str
    task_set_hash: str
    loader_version: str
    loader_manifest_schema_version: str
    score_parser_version: str
    run_config_hash: str
    skill_hash: str
    prompt_hash: str | None
    evaluation_mode: str
    context_budget_profile: str
    visible_feedback_policy_id: str
    oracle_call_budget: str
    task_sample_policy_id: str
    round_budget: str
    token_budget: str


@dataclass(frozen=True)
class ResumeDecision:
    action: str
    reasons: list[str]


# Only fields that change the *meaning* of a completed result are compared for
# resume. Fields that track code/manifest evolution (run_config_hash,
# task_set_hash, loader/score-parser versions, benchmark source hash, prompt
# hash) are intentionally excluded: they change as skilllift_eval evolves but do
# not invalidate an already-recorded task result. This lets artifacts produced
# by an earlier code revision be reused, while still refusing to reuse a result
# across models, skills, algorithm params, or evaluation modes.
RESUME_SEMANTIC_FIELDS = (
    "provider_model_id",        # never reuse a result across different models
    "endpoint_config_hash",     # never reuse across different endpoints
    "algorithm_param_hash",     # algorithm params changed -> result invalid
    "skill_hash",               # skill changed -> result invalid
    "prompt_hash",              # prompt changed -> result invalid (tau2 prompt fingerprint)
    "evaluation_mode",          # native vs budget_matched results are not comparable
    "context_budget_profile",
    "visible_feedback_policy_id",
    "oracle_call_budget",
    "task_sample_policy_id",
    "round_budget",
    "token_budget",
)


def plan_resume(record: TaskRunRecord | None, current: ResumeInputs) -> ResumeDecision:
    if record is None:
        return ResumeDecision("run", ["no previous TaskRunRecord"])
    reasons: list[str] = []
    if record.status != "succeeded":
        reasons.append(f"status is {record.status}")
    for path_field in ["loader_manifest_path", "raw_output_path", "score_path"]:
        value = getattr(record, path_field)
        if not value or not Path(value).exists():
            reasons.append(f"{path_field} missing")
    for field in RESUME_SEMANTIC_FIELDS:
        if getattr(record, field, None) != getattr(current, field, None):
            reasons.append(f"{field} changed")
    return ResumeDecision("rerun", reasons) if reasons else ResumeDecision("skip", [])
