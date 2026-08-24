from __future__ import annotations

from pathlib import Path

from skilllift_eval.resume import ResumeInputs, plan_resume
from skilllift_eval.schemas import TaskRunRecord


def make_record(tmp_path: Path, **overrides: object) -> TaskRunRecord:
    manifest = tmp_path / "manifest.json"
    raw = tmp_path / "raw.json"
    score = tmp_path / "score.json"
    for p in (manifest, raw, score):
        p.write_text("{}")
    data = dict(
        run_id="r1",
        matrix_cell_id="cell",
        baseline="autoskill",
        benchmark="wildclawbench",
        model_label="gpt-5.4",
        model_endpoint_id="gpt-5.4",
        provider_model_id="gpt-5.4",
        endpoint_config_hash="endpoint",
        algorithm_param_hash="algo",
        benchmark_source_hash="bench",
        task_set_hash="taskset",
        loader_version="loader",
        loader_manifest_schema_version="manifest-schema",
        score_parser_version="parser",
        run_config_hash="runconfig",
        task_id="task",
        skill_hash="skill",
        prompt_hash=None,
        loader_manifest_path=str(manifest),
        raw_output_path=str(raw),
        score_path=str(score),
        status="succeeded",
        evaluation_mode="native_end_to_end",
        context_budget_profile="native_default",
        visible_feedback_policy_id="public_sanitized_v1",
        oracle_call_budget="native_default",
        task_sample_policy_id="full_native_v1",
        round_budget="paper_default",
        token_budget="native_default",
    )
    data.update(overrides)
    return TaskRunRecord(**data)


def make_inputs(**overrides: object) -> ResumeInputs:
    data = dict(
        provider_model_id="gpt-5.4",
        endpoint_config_hash="endpoint",
        algorithm_param_hash="algo",
        benchmark_source_hash="bench",
        task_set_hash="taskset",
        loader_version="loader",
        loader_manifest_schema_version="manifest-schema",
        score_parser_version="parser",
        run_config_hash="runconfig",
        skill_hash="skill",
        prompt_hash=None,
        evaluation_mode="native_end_to_end",
        context_budget_profile="native_default",
        visible_feedback_policy_id="public_sanitized_v1",
        oracle_call_budget="native_default",
        task_sample_policy_id="full_native_v1",
        round_budget="paper_default",
        token_budget="native_default",
    )
    data.update(overrides)
    return ResumeInputs(**data)


def test_resume_skips_only_when_all_artifacts_and_consistency_fields_match(tmp_path: Path) -> None:
    decision = plan_resume(make_record(tmp_path), make_inputs())
    assert decision.action == "skip"


def test_resume_reruns_when_required_artifact_missing(tmp_path: Path) -> None:
    record = make_record(tmp_path)
    Path(record.score_path).unlink()
    decision = plan_resume(record, make_inputs())
    assert decision.action == "rerun"
    assert "score_path missing" in decision.reasons


def test_resume_reruns_on_hash_version_or_budget_changes(tmp_path: Path) -> None:
    assert plan_resume(make_record(tmp_path), make_inputs(provider_model_id="other")).action == "rerun"
    assert plan_resume(make_record(tmp_path), make_inputs(algorithm_param_hash="other")).action == "rerun"
    assert plan_resume(make_record(tmp_path), make_inputs(context_budget_profile="matched_v1")).action == "rerun"


def test_tau2_prompt_hash_change_reruns_even_with_native_checkpoint(tmp_path: Path) -> None:
    record = make_record(tmp_path, benchmark="tau2", prompt_hash="old")
    decision = plan_resume(record, make_inputs(prompt_hash="new"))
    assert decision.action == "rerun"
    assert any("prompt_hash" in reason for reason in decision.reasons)
