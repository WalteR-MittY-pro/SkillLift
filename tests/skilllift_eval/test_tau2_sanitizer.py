from __future__ import annotations

import pytest

from skilllift_eval.benchmarks.tau2_feedback import sanitize_tau2_feedback
from skilllift_eval.benchmarks.tau2_public_view import tau2_public_task_view


def test_tau2_public_view_rejects_banking_knowledge_and_omits_gold_fields() -> None:
    with pytest.raises(ValueError, match="banking_knowledge"):
        tau2_public_task_view({"id": "k1", "domain": "banking_knowledge"})

    view = tau2_public_task_view(
        {
            "id": "airline-1",
            "domain": "airline",
            "user_scenario": "Need to change a flight.",
            "ticket": "Change flight ticket.",
            "evaluation_criteria": {"actions": ["gold"]},
            "target_db": {"secret": True},
            "hidden_assertions": ["must do x"],
            "gt_resolution_steps": ["step 1"],
        }
    )

    encoded = str(view)
    assert "Need to change a flight." in encoded
    assert "evaluation_criteria" not in encoded
    assert "target_db" not in encoded
    assert "hidden_assertions" not in encoded
    assert "gt_resolution_steps" not in encoded


def test_tau2_feedback_removes_sensitive_fields() -> None:
    feedback = sanitize_tau2_feedback(
        {
            "task_id": "airline-1",
            "domain": "airline",
            "score": 0.5,
            "messages": [
                {"role": "assistant", "content": "Asked for booking reference."},
                {"role": "evaluator", "content": "gold actions and hidden assertions"},
            ],
            "evaluation_criteria": {"actions": ["gold"]},
            "target_db": {"secret": True},
            "gt_resolution_steps": ["private"],
        },
        token_budget="native_default",
    )

    encoded = str(feedback)
    assert "Asked for booking reference." in encoded
    assert "evaluation_criteria" not in encoded
    assert "target_db" not in encoded
    assert "gold actions" not in encoded
    assert "hidden assertions" not in encoded
    assert "gt_resolution_steps" not in encoded


def test_tau2_feedback_is_capped_by_token_budget() -> None:
    feedback = sanitize_tau2_feedback(
        {
            "task_id": "airline-2",
            "domain": "airline",
            "messages": [{"role": "assistant", "content": "x" * 200}],
        },
        token_budget="8",
    )

    content = feedback["public_feedback"]["messages"][0]["content"]
    assert len(content) <= 32
    assert feedback["context_truncated"] is True
