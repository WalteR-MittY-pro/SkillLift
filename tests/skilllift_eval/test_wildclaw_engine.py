from __future__ import annotations

import json
from pathlib import Path

from skilllift_eval.runners.wildclaw_engine import docker_utils, run_batch


def _write_assistant_message(path: Path, *, content: list[dict], **extra: object) -> None:
    payload = {
        "type": "message",
        "message": {"role": "assistant", "content": content, **extra},
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_collect_output_copies_results_without_complete_workspace(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_copy(task_id: str, source: str, destination: str) -> bool:
        calls.append((task_id, source, destination))
        return True

    monkeypatch.setattr(docker_utils, "_copy_dir_from_container", fake_copy)

    docker_utils.collect_output_from_container("task", tmp_path)

    assert calls == [
        ("task", "/tmp/openclaw/.", str(tmp_path / "task_output")),
        (
            "task",
            "/tmp_workspace/results/.",
            str(tmp_path / "task_output" / "workspace" / "results"),
        ),
    ]


def test_remove_large_model_checkpoints_keeps_small_files(tmp_path: Path) -> None:
    large_checkpoint = tmp_path / "model.pt"
    small_checkpoint = tmp_path / "small.pt"
    large_checkpoint.write_bytes(b"x" * 101)
    small_checkpoint.write_bytes(b"x")

    original_limit = docker_utils.MODEL_CHECKPOINT_MIN_BYTES
    docker_utils.MODEL_CHECKPOINT_MIN_BYTES = 100
    try:
        docker_utils._remove_large_model_checkpoints(tmp_path)
    finally:
        docker_utils.MODEL_CHECKPOINT_MIN_BYTES = original_limit

    assert not large_checkpoint.exists()
    assert small_checkpoint.exists()


def test_run_single_task_retries_503_then_returns_success(monkeypatch, tmp_path: Path) -> None:
    calls = 0

    def fake_run_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        output_dir = tmp_path / f"attempt-{calls}"
        output_dir.mkdir()
        if calls == 1:
            _write_assistant_message(
                output_dir / "chat.jsonl",
                content=[],
                stopReason="error",
                errorMessage="503 Service temporarily unavailable",
            )
        else:
            _write_assistant_message(
                output_dir / "chat.jsonl",
                content=[{"type": "text", "text": "done"}],
                stopReason="stop",
            )
        return {
            "task_id": f"attempt-{calls}",
            "scores": {"overall_score": 1.0 if calls == 2 else 0.0},
            "error": None,
            "output_dir": str(output_dir),
        }

    monkeypatch.setattr(run_batch, "_run_single_task_once", fake_run_once)

    result = run_batch.run_single_task(
        {"task_id": "task"},
        "model",
        rate_limit_retries=2,
        rate_limit_wait_seconds=0,
    )

    assert calls == 2
    assert result["error"] is None
    assert result["retry_attempts"][0]["reason"] == "transient_provider_error"


def test_run_single_task_fails_after_empty_response_retries(monkeypatch, tmp_path: Path) -> None:
    calls = 0

    def fake_run_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        output_dir = tmp_path / f"attempt-{calls}"
        output_dir.mkdir()
        _write_assistant_message(
            output_dir / "chat.jsonl",
            content=[],
            stopReason="stop",
        )
        return {
            "task_id": f"attempt-{calls}",
            "scores": {"overall_score": 0.0},
            "error": None,
            "output_dir": str(output_dir),
        }

    monkeypatch.setattr(run_batch, "_run_single_task_once", fake_run_once)

    result = run_batch.run_single_task(
        {"task_id": "task"},
        "model",
        rate_limit_retries=1,
        rate_limit_wait_seconds=0,
    )

    assert calls == 2
    assert "empty_assistant_response retry condition matched" in result["error"]
    assert len(result["retry_attempts"]) == 2


def test_run_single_task_does_not_retry_non_transient_runner_error(
    monkeypatch, tmp_path: Path
) -> None:
    calls = 0

    def fake_run_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {
            "task_id": "attempt-1",
            "scores": {},
            "error": "Container startup failed",
            "output_dir": str(tmp_path / "attempt-1"),
        }

    monkeypatch.setattr(run_batch, "_run_single_task_once", fake_run_once)

    result = run_batch.run_single_task(
        {"task_id": "task"},
        "model",
        rate_limit_retries=2,
        rate_limit_wait_seconds=0,
    )

    assert calls == 1
    assert result["error"] == "Container startup failed"


def test_run_single_task_retries_transient_runner_error(monkeypatch, tmp_path: Path) -> None:
    calls = 0

    def fake_run_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        output_dir = tmp_path / f"attempt-{calls}"
        output_dir.mkdir()
        if calls == 2:
            _write_assistant_message(
                output_dir / "chat.jsonl",
                content=[{"type": "text", "text": "done"}],
                stopReason="stop",
            )
        return {
            "task_id": f"attempt-{calls}",
            "scores": {"overall_score": 1.0 if calls == 2 else 0.0},
            "error": "apt update failed: 502 Bad Gateway" if calls == 1 else None,
            "output_dir": str(output_dir),
        }

    monkeypatch.setattr(run_batch, "_run_single_task_once", fake_run_once)

    result = run_batch.run_single_task(
        {"task_id": "task"},
        "model",
        rate_limit_retries=2,
        rate_limit_wait_seconds=0,
    )

    assert calls == 2
    assert result["error"] is None
    assert result["retry_attempts"][0]["reason"] == "transient_runner_error"
