from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .tokens import TokenUsage


def parse_score_payload(payload: dict[str, Any], usage: TokenUsage | None = None) -> float:
    if "score" in payload:
        return float(payload["score"])
    if "scores" in payload and isinstance(payload["scores"], dict) and "score" in payload["scores"]:
        return float(payload["scores"]["score"])
    raise ValueError("score payload missing score")


@dataclass
class RoundMetric:
    matrix_cell_id: str
    baseline: str
    benchmark: str
    model_label: str
    model_endpoint_id: str
    provider_model_id: str
    round_id: str
    round_type: str
    skill_hash: str
    prompt_hash: str | None
    token_usage: TokenUsage
    evaluation_mode: str
    context_budget_profile: str
    visible_feedback_policy_id: str
    oracle_calls_used: int
    oracle_call_budget: str
    task_sample_policy_id: str
    round_budget: str
    token_budget: str
    context_input_tokens_estimate: int
    context_truncated: bool
    algorithm_param_profile: str
    created_at: str
    train_score: float | None = None
    validation_score: float | None = None
    eval_score: float | None = None
    score_delta: float | None = None
    artifact_paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        usage = self.token_usage
        data = asdict(self)
        data.pop("token_usage")
        data["actual_usage_status"] = usage.actual_usage_status
        data["token_estimate_total"] = self.context_input_tokens_estimate
        data["token_actual_total"] = usage.total_tokens if usage.actual_usage_status == "available" else "unavailable"
        data["cost_actual_total"] = usage.cost_actual
        return {k: v for k, v in data.items() if v is not None}
