import json

from skilllift.experiment_trace import (
    classify_failure_from_payload,
    load_public_score,
    redact,
    summarize_oracle_output,
)


def test_classify_failure_from_payload_protects_perfect_score() -> None:
    # The word "error" is routine log noise; a perfect grader score wins.
    assert classify_failure_from_payload({"overall_score": 1.0}, "RuntimeError: error") == "success"


def test_redact_masks_secrets_and_hidden_answers() -> None:
    text = "api_key: abcdefghijklmnop hidden_answer is 42 bearer abcdefghijklmnop"
    redacted = redact(text)
    assert "abcdefghijklmnop" not in redacted
    assert "42" not in redacted
    assert "[REDACTED]" in redacted


def test_feedback_level_crops_public_feedback(tmp_path) -> None:
    out = tmp_path / "run"
    (out / "task_output").mkdir(parents=True)
    (out / "task_output" / "results.md").write_text("ok", encoding="utf-8")
    (out / "score.json").write_text(json.dumps({"overall_score": 0.5, "hidden_answer": "x"}), encoding="utf-8")
    (out / "agent.log").write_text("Traceback: Error api_key=secretsecretsecret\n", encoding="utf-8")
    (out / "gateway.log").write_text("", encoding="utf-8")
    (out / "chat.jsonl").write_text('{"role":"assistant","content":"hello"}\n', encoding="utf-8")

    level0 = summarize_oracle_output(out, 0)
    level3 = summarize_oracle_output(out, 3)
    assert level0.produced_files == []
    assert "overall_score" in level3.metadata["scores"]
    assert "hidden_answer" not in level3.metadata["scores"]
    assert level3.produced_files == ["results.md"]
    assert "secretsecretsecret" not in level3.stderr_excerpt
    assert level3.traceback_summary


def test_load_public_score_only_keeps_safe_numeric_keys(tmp_path) -> None:
    out = tmp_path / "run"
    out.mkdir()
    (out / "score.json").write_text(
        json.dumps({"overall_score": 1, "reason": "hidden", "debug_flag": True, "files_created": 2}),
        encoding="utf-8",
    )
    assert load_public_score(out) == {"overall_score": 1.0, "files_created": 2.0}

