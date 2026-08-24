from __future__ import annotations

import json
from pathlib import Path

from skilllift.schemas import EvoSkill, OracleFeedback, OracleScore, SkillKey, TaskRanking
from skilllift_eval.runners.skilllift_tau2 import (
    SkillLiftTau2TrainConfig,
    Tau2SkillBatchResult,
    run_master_skill_fusion_smoke,
    write_train_cluster_artifact,
)


def test_master_skill_fusion_smoke_writes_artifact_and_bundle(tmp_path: Path) -> None:
    config = SkillLiftTau2TrainConfig(
        run_root=tmp_path / "run",
        manifest_path=tmp_path / "tau2_train_cluster_manifest.json",
        model="fake-train-model",
    )
    cluster_artifact = _write_cluster_artifact(config)
    calls: list[str] = []

    def fake_fusion_llm(prompt: str, model: str) -> str:
        calls.append(prompt)
        assert model == "fake-fusion-model"
        assert "skilllift_tau2_master_skill_fusion_v1" not in prompt
        assert 'Merge the train-only cluster skills for domain "telecom"' in prompt
        assert "- Required action types: verify_account, resolve_issue" in prompt
        assert "Check visible account state before action." in prompt
        assert "## Disputed" in prompt
        assert "## Coverage Gap: <action_type>" in prompt
        return "\n".join(
            [
                "`````",
                "# MASTER_SKILL",
                "",
                "## Action Checkpoint Index",
                "- verify_account",
                "- resolve_issue",
                "",
                "## verify_account",
                "- Check visible account state before action.",
                "",
                "## resolve_issue",
                "- Verify preconditions before action and check results after actions.",
                "`````",
            ]
        )

    result = run_master_skill_fusion_smoke(
        config,
        cluster_artifact_paths=[cluster_artifact],
        domain="telecom",
        action_types=["verify_account", "resolve_issue"],
        fusion_model="fake-fusion-model",
        fusion_llm=fake_fusion_llm,
        repair_metrics={
            "parse_error_count": 1,
            "total_generation_attempts": 4,
        },
    )

    assert len(calls) == 1
    assert result.artifact_path.exists()
    assert result.master_skill_path.exists()
    assert result.best_skill_bundle_path.exists()
    master_skill = result.master_skill_path.read_text(encoding="utf-8")
    assert master_skill.startswith("# MASTER_SKILL")
    assert "<" not in master_skill
    assert ">" not in master_skill

    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert artifact["skilllift_phase"] == "train_evolve"
    assert artifact["tau2_split"] == "train"
    assert artifact["fusion_prompt_template_id"] == "skilllift_tau2_master_skill_fusion_v1"
    assert artifact["fusion_model"] == "fake-fusion-model"
    assert artifact["fusion_prompt_hash"].startswith("sha256:")
    assert artifact["output_skill_hash"].startswith("sha256:")
    assert set(artifact["input_skill_hashes"]) == {
        "telecom_billing_00/s000_v001",
        "telecom_billing_00/s001_v001",
    }
    assert artifact["action_checkpoint_coverage"]["covered"] == ["resolve_issue", "verify_account"]
    assert artifact["action_checkpoint_coverage"]["uncovered"] == []
    assert artifact["d12_repair_metrics"] == {
        "parse_error_count": 1,
        "total_generation_attempts": 4,
        "repair_trigger_rate": 0.25,
    }
    assert artifact["master_skill_path"] == str(result.master_skill_path.resolve())
    assert artifact["best_skill_bundle_path"] == str(result.best_skill_bundle_path.resolve())

    bundle = json.loads(result.best_skill_bundle_path.read_text(encoding="utf-8"))
    assert bundle["granularity"] == "domain"
    assert bundle["domain"] == "telecom"
    assert bundle["skills"] == [
        {
            "id": "MASTER_SKILL",
            "title": "MASTER_SKILL",
            "content": master_skill,
            "metadata": {"fusion_prompt_template_id": "skilllift_tau2_master_skill_fusion_v1"},
        }
    ]

    serialized = json.dumps(artifact, ensure_ascii=False)
    assert "evaluation_criteria" not in serialized
    assert "gold_action" not in serialized
    assert "target_db" not in serialized
    assert "messages" not in serialized
    assert "transcript" not in serialized
    assert "test_eval" not in serialized
    assert "held-out" not in serialized


def test_master_skill_fusion_records_coverage_gap_without_fabricating_rules(tmp_path: Path) -> None:
    config = SkillLiftTau2TrainConfig(
        run_root=tmp_path / "run",
        manifest_path=tmp_path / "tau2_train_cluster_manifest.json",
        model="fake-train-model",
    )
    cluster_artifact = _write_cluster_artifact(config)

    def fake_fusion_llm(prompt: str, model: str) -> str:
        assert model == "fake-fusion-model"
        assert "- Required action types: verify_account, resolve_issue" in prompt
        return "\n".join(
            [
                "`````",
                "# MASTER_SKILL",
                "",
                "## Action Checkpoint Index",
                "- verify_account",
                "- Coverage Gap: resolve_issue",
                "",
                "## verify_account",
                "- Check visible account state before action.",
                "",
                "## Coverage Gap: resolve_issue",
                "No fabricated rules are available from input train skills or public policy.",
                "`````",
            ]
        )

    result = run_master_skill_fusion_smoke(
        config,
        cluster_artifact_paths=[cluster_artifact],
        domain="telecom",
        action_types=["verify_account", "resolve_issue"],
        fusion_model="fake-fusion-model",
        fusion_llm=fake_fusion_llm,
        repair_metrics={},
    )

    master_skill = result.master_skill_path.read_text(encoding="utf-8")
    assert "## Coverage Gap: resolve_issue" in master_skill

    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert artifact["action_checkpoint_coverage"] == {
        "covered": ["verify_account"],
        "uncovered": ["resolve_issue"],
    }


def test_master_skill_fusion_allows_coverage_gap_placeholder_angle_brackets(tmp_path: Path) -> None:
    config = SkillLiftTau2TrainConfig(
        run_root=tmp_path / "run",
        manifest_path=tmp_path / "tau2_train_cluster_manifest.json",
        model="fake-train-model",
    )
    cluster_artifact = _write_cluster_artifact(config)

    def fake_fusion_llm(prompt: str, model: str) -> str:
        return "\n".join(
            [
                "`````",
                "# MASTER_SKILL",
                "",
                "## Action Checkpoint Index",
                "- verify_account",
                "- Coverage Gap: resolve_issue",
                "",
                "## verify_account",
                "- Check visible account state before action.",
                "",
                "## Coverage Gap: <action_type>",
                "No fabricated rules are available from input train skills or public policy.",
                "`````",
            ]
        )

    result = run_master_skill_fusion_smoke(
        config,
        cluster_artifact_paths=[cluster_artifact],
        domain="telecom",
        action_types=["verify_account", "resolve_issue"],
        fusion_model="fake-fusion-model",
        fusion_llm=fake_fusion_llm,
        repair_metrics={},
    )

    assert "## Coverage Gap: <action_type>" in result.master_skill_path.read_text(encoding="utf-8")


def test_master_skill_fusion_rejects_xml_tags(tmp_path: Path) -> None:
    config = SkillLiftTau2TrainConfig(
        run_root=tmp_path / "run",
        manifest_path=tmp_path / "tau2_train_cluster_manifest.json",
        model="fake-train-model",
    )
    cluster_artifact = _write_cluster_artifact(config)

    def fake_fusion_llm(prompt: str, model: str) -> str:
        return "\n".join(
            [
                "`````",
                "# MASTER_SKILL",
                "",
                "<receipt>do not emit xml</receipt>",
                "`````",
            ]
        )

    try:
        run_master_skill_fusion_smoke(
            config,
            cluster_artifact_paths=[cluster_artifact],
            domain="telecom",
            action_types=["verify_account"],
            fusion_model="fake-fusion-model",
            fusion_llm=fake_fusion_llm,
            repair_metrics={},
        )
    except ValueError as exc:
        assert "XML/HTML tags" in str(exc)
    else:
        raise AssertionError("fusion output with XML tags should be rejected")


def test_master_skill_fusion_rejects_six_backtick_fence(tmp_path: Path) -> None:
    config = SkillLiftTau2TrainConfig(
        run_root=tmp_path / "run",
        manifest_path=tmp_path / "tau2_train_cluster_manifest.json",
        model="fake-train-model",
    )
    cluster_artifact = _write_cluster_artifact(config)

    def fake_fusion_llm(prompt: str, model: str) -> str:
        return "\n".join(["``````", "# MASTER_SKILL", "``````"])

    try:
        run_master_skill_fusion_smoke(
            config,
            cluster_artifact_paths=[cluster_artifact],
            domain="telecom",
            action_types=["verify_account"],
            fusion_model="fake-fusion-model",
            fusion_llm=fake_fusion_llm,
            repair_metrics={},
        )
    except ValueError as exc:
        assert "fenced backticks" in str(exc)
    else:
        raise AssertionError("six-backtick fusion output should be rejected")


def test_master_skill_fusion_accepts_five_backtick_markdown_info_string(tmp_path: Path) -> None:
    config = SkillLiftTau2TrainConfig(
        run_root=tmp_path / "run",
        manifest_path=tmp_path / "tau2_train_cluster_manifest.json",
        model="fake-train-model",
    )
    cluster_artifact = _write_cluster_artifact(config)

    def fake_fusion_llm(prompt: str, model: str) -> str:
        return "\n".join(
            [
                "`````markdown",
                "# MASTER_SKILL",
                "",
                "## Action Checkpoint Index",
                "- verify_account",
                "",
                "## verify_account",
                "- Check visible account state before action.",
                "`````",
            ]
        )

    result = run_master_skill_fusion_smoke(
        config,
        cluster_artifact_paths=[cluster_artifact],
        domain="telecom",
        action_types=["verify_account"],
        fusion_model="fake-fusion-model",
        fusion_llm=fake_fusion_llm,
        repair_metrics={},
    )

    assert result.master_skill_path.read_text(encoding="utf-8").startswith("# MASTER_SKILL")


def _write_cluster_artifact(config: SkillLiftTau2TrainConfig) -> Path:
    skills = {
        SkillKey(0, 1): EvoSkill(
            SkillKey(0, 1),
            "seed",
            {"SKILL.md": "# Seed\n\n- Check visible account state before action."},
            None,
            {"prompt_hash": "prompt-seed"},
        ),
        SkillKey(1, 1): EvoSkill(
            SkillKey(1, 1),
            "variant",
            {"SKILL.md": "# Variant\n\n- Verify preconditions before action and check results after actions."},
            None,
            {"prompt_hash": "prompt-variant"},
        ),
    }
    scores = {
        SkillKey(0, 1): OracleScore(
            SkillKey(0, 1),
            0.5,
            1,
            feedback=OracleFeedback(summary="seed"),
        ),
        SkillKey(1, 1): OracleScore(
            SkillKey(1, 1),
            1.0,
            1,
            feedback=OracleFeedback(summary="variant"),
        ),
    }
    batch_result = Tau2SkillBatchResult(
        task_skill_scores={},
        per_task_rankings=[
            TaskRanking(
                task_id="telecom-train-task-1",
                domain="telecom",
                oracle_rank=[SkillKey(1, 1), SkillKey(0, 1)],
                skill_scores=scores,
                has_signal=True,
            )
        ],
        aggregate_oracle_scores=scores,
        aggregate_oracle_rank=[SkillKey(1, 1), SkillKey(0, 1)],
    )
    return write_train_cluster_artifact(
        config=config,
        cluster_id="telecom_billing_00",
        domain="telecom",
        task_ids=["telecom-train-task-1"],
        skills=skills,
        batch_result=batch_result,
        source_train_run_id="train-smoke-1",
    )
