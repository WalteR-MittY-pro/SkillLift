from __future__ import annotations

from skilllift_eval.metrics import RoundMetric, parse_score_payload
from skilllift_eval.tokens import TokenUsage


def test_score_parser_does_not_depend_on_usage() -> None:
    assert parse_score_payload({"score": 0.75}, usage=None) == 0.75


def test_round_metric_records_context_budget_and_unavailable_usage() -> None:
    metric = RoundMetric(
        matrix_cell_id="cell",
        baseline="autoskill",
        benchmark="tau2",
        model_label="gpt-5.4",
        model_endpoint_id="gpt-5.4",
        provider_model_id="gpt-5.4",
        round_id="round-1",
        round_type="train",
        skill_hash="sha256:s",
        prompt_hash="sha256:p",
        token_usage=TokenUsage(actual_usage_status="unavailable"),
        evaluation_mode="native_end_to_end",
        context_budget_profile="native_default",
        visible_feedback_policy_id="public_sanitized_v1",
        oracle_calls_used=0,
        oracle_call_budget="native_default",
        task_sample_policy_id="full_native_v1",
        round_budget="paper_default",
        token_budget="native_default",
        context_input_tokens_estimate=12,
        context_truncated=False,
        algorithm_param_profile="paper_default",
        created_at="2026-06-22T00:00:00-07:00",
    )
    data = metric.to_dict()
    assert data["token_actual_total"] == "unavailable"
    assert data["actual_usage_status"] == "unavailable"
    assert data["context_budget_profile"] == "native_default"
