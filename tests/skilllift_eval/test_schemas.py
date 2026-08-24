from __future__ import annotations

import pytest

from skilllift_eval.schemas import (
    AlgorithmParamProfile,
    LoadedSkillContext,
    MatrixCellKey,
    SkillBundle,
    TaskRunRecord,
)


def test_skill_bundle_hash_is_stable_and_shared_across_benchmarks() -> None:
    skills = [{"id": "s1", "title": "Do X", "content": "Steps", "metadata": {"z": 1}}]
    wild = SkillBundle(
        skill_bundle_id="bundle-1",
        baseline="autoskill",
        benchmark_target="wildclawbench",
        granularity="benchmark",
        skills=skills,
        created_at="2026-06-22T00:00:00-07:00",
    )
    tau2 = SkillBundle(
        skill_bundle_id="bundle-2",
        baseline="autoskill",
        benchmark_target="tau2",
        granularity="benchmark",
        skills=skills,
        created_at="2026-06-22T00:00:00-07:00",
    )

    assert wild.skill_hash == tau2.skill_hash
    assert wild.skill_hash.startswith("sha256:")
    assert SkillBundle.from_dict(wild.to_dict()).skill_hash == wild.skill_hash


def test_loaded_skill_context_is_explicit_variant_and_rejects_runner_mismatch() -> None:
    wild = LoadedSkillContext.from_dict(
        {
            "kind": "wildclaw_file_context",
            "benchmark": "wildclawbench",
            "skill_bundle_id": "b1",
            "skill_hash": "sha256:abc",
            "loader_type": "wildclaw_task_skills",
            "original_task_path": "orig.md",
            "generated_task_path": "gen.md",
            "host_skills_parent_dir": "WildClawBench/skills",
            "loaded_skill_names": ["skill-a"],
            "runtime_skill_dirs": ["artifacts/skill-a"],
            "container_skill_dir": "/root/skills",
            "loaded_files": ["SKILL.md"],
            "copy_or_mount_mode": "copy",
            "manifest_path": "manifest.json",
            "extra_run_args": ["--task", "gen.md"],
            "extra_env": {},
        }
    )
    wild.validate_for_runner("wildclawbench")
    with pytest.raises(ValueError, match="wildclaw_file_context"):
        wild.validate_for_runner("tau2")

    with pytest.raises(ValueError, match="tau2_prompt_context"):
        LoadedSkillContext.from_dict(
            {
                "kind": "tau2_prompt_context",
                "benchmark": "wildclawbench",
                "skill_bundle_id": "b1",
                "skill_hash": "sha256:abc",
                "loader_type": "tau2_prompt_context",
                "bundle_path": "bundle.json",
                "prompt_template_id": "tmpl",
                "prompt_payload_path": "payload.txt",
                "prompt_hash": "sha256:p",
                "skills_block_hash": "sha256:s",
                "domain": "airline",
                "granularity": "domain",
                "batch_size_k": 1,
                "batch_task_ids": ["t1"],
                "manifest_path": "manifest.json",
                "extra_run_args": [],
                "extra_env": {},
            }
        )


def test_algorithm_profile_hash_and_override_diff_are_canonical() -> None:
    profile = AlgorithmParamProfile(
        baseline="textgrad",
        profile_id="paper_default",
        paper_reference="TextGrad/paper.pdf",
        paper_default_params={"epochs": 4, "batch": 40},
        resolved_params={"epochs": 4, "batch": 40},
        source_notes=[{"parameter": "epochs", "page": 8}],
    )
    override = profile.with_overrides({"epochs": 5})

    assert profile.override_diff == {}
    assert override.override_diff == {"epochs": {"from": 4, "to": 5}}
    assert override.param_hash != profile.param_hash
    assert AlgorithmParamProfile.from_dict(profile.to_dict()).param_hash == profile.param_hash


def test_task_run_record_contains_resume_and_context_budget_fields() -> None:
    record = TaskRunRecord(
        run_id="r1",
        matrix_cell_id="cell",
        baseline="no_skill",
        benchmark="tau2",
        model_label="gpt-5.4",
        model_endpoint_id="gpt-5.4",
        provider_model_id="gpt-5.4",
        endpoint_config_hash="e",
        algorithm_param_hash="a",
        benchmark_source_hash="b",
        task_set_hash="t",
        loader_version="l",
        loader_manifest_schema_version="m",
        score_parser_version="s",
        run_config_hash="r",
        task_id="task",
        skill_hash="sha256:empty",
        prompt_hash="sha256:p",
        loader_manifest_path="manifest.json",
        raw_output_path="raw.json",
        score_path="score.json",
        status="succeeded",
        evaluation_mode="native_end_to_end",
        context_budget_profile="native_default",
        visible_feedback_policy_id="public_sanitized_v1",
        oracle_call_budget="native_default",
        task_sample_policy_id="full_native_v1",
        round_budget="paper_default",
        token_budget="native_default",
    )
    data = record.to_dict()
    for field in TaskRunRecord.resume_consistency_fields():
        assert field in data
    assert TaskRunRecord.from_dict(data).run_config_hash == "r"


def test_matrix_cell_key_separates_native_and_budget_matched_ids() -> None:
    native = MatrixCellKey("native_end_to_end", "autoskill", "tau2", "gpt-5.4")
    matched = MatrixCellKey("budget_matched", "autoskill", "tau2", "gpt-5.4")
    assert native.matrix_cell_id != matched.matrix_cell_id
    assert native.matrix_cell_id.startswith("native_end_to_end__")
    assert matched.matrix_cell_id.startswith("budget_matched__")
