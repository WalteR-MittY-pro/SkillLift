from __future__ import annotations

from skilllift_eval.benchmarks.wildclaw_feedback import sanitize_wildclaw_feedback
from skilllift_eval.benchmarks.wildclaw_public_view import wildclaw_public_task_view


def test_wildclaw_public_view_uses_explicit_whitelist() -> None:
    view = wildclaw_public_task_view(
        {
            "task_id": "task-1",
            "prompt": "Visible prompt",
            "workspace_path": "/Users/user/private/workspace",
            "automated_checks": "hidden evaluator implementation",
            "category": "cat",
            "timeout_seconds": 60,
            "gold_workspace_state": "secret",
        }
    )

    assert view["public_task_fields"] == {
        "task_id": "task-1",
        "prompt": "Visible prompt",
        "category": "cat",
        "timeout_seconds": 60,
    }
    encoded = str(view)
    assert "/Users/user/private" not in encoded
    assert "gold_workspace_state" not in encoded


def test_wildclaw_feedback_removes_hidden_evaluator_and_private_state() -> None:
    feedback = sanitize_wildclaw_feedback(
        {
            "task_id": "task-1",
            "score": 0.0,
            "transcript": [
                {"visibility": "agent_visible", "role": "assistant", "content": "I used CANARY_WILDCLAW_SKILL_42."},
                {"visibility": "evaluator_only", "content": "hidden assertion: exact answer"},
            ],
            "automated_check": {
                "status": "failed",
                "category": "assertion_failed",
                "stderr": "AssertionError at /Users/user/private/check.py with API_KEY=secret",
                "hidden_check_implementation": "gold workspace state",
            },
            "container_env": {"TOKEN": "secret"},
            "workspace_state": {"gold": "answer"},
        }
    )

    encoded = str(feedback)
    assert "CANARY_WILDCLAW_SKILL_42" in encoded
    assert "hidden assertion" not in encoded
    assert "gold workspace" not in encoded
    assert "/Users/user/private" not in encoded
    assert "API_KEY=secret" not in encoded
    assert "container_env" not in encoded
    assert feedback["public_feedback"]["automated_check"]["category"] == "assertion_failed"
    assert "public_error_excerpt" in feedback["public_feedback"]["automated_check"]


def test_wildclaw_feedback_keeps_only_public_transcript_granularity() -> None:
    feedback = sanitize_wildclaw_feedback(
        {
            "task_id": "task-2",
            "transcript": [
                {"visibility": "agent_visible", "role": "user", "content": "Public prompt"},
                {"visibility": "agent_action", "action_summary": "Edited README.md"},
                {"visibility": "public_stream", "stdout": "ok", "stderr": "minor warning"},
                {"visibility": "evaluator_only", "content": "target db"},
            ],
        }
    )

    transcript = feedback["public_feedback"]["transcript"]
    assert transcript == [
        {"role": "user", "content": "Public prompt"},
        {"action_summary": "Edited README.md"},
        {"stdout": "ok", "stderr": "minor warning"},
    ]
    assert "target db" not in str(feedback)
