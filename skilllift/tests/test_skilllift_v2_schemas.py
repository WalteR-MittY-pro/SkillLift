import json

from skilllift.normalization import recompute_receipt_bounds
from skilllift.schemas import (
    SkillLiftConfig,
    DEFAULT_FRAMEWORK_MODELS_CONFIG,
    DEFAULT_OPENCLAW_MODELS_CONFIG,
    EvoSkill,
    ExperimentResult,
    OracleFeedback,
    OracleScore,
    Receipt,
    ReceiptRevisionAttempt,
    ReceiptRevisionInput,
    RoundState,
    RubricCriterion,
    SkillKey,
    TaskRanking,
    TaskSpec,
    VerifierScore,
)


def _receipt() -> Receipt:
    return Receipt(
        version=1,
        rubrics=[
            RubricCriterion("r1", "Planning", "Has a plan", 2),
            RubricCriterion("r2", "Errors", "Ignores errors", -3),
        ],
        maximum_score=0,
        minimum_score=0,
    )


def _skill() -> EvoSkill:
    return EvoSkill(
        key=SkillKey(0, 1),
        skill_name="demo",
        files={"SKILL.md": "---\nname: demo\n---\n", "executor.py": "print('ok')\n"},
        entrypoint="executor.py",
    )


def test_schema_round_trip_for_core_records() -> None:
    task = TaskSpec("task", "Do the work", "task.md")
    receipt = recompute_receipt_bounds(_receipt())
    skill = _skill()
    verifier = VerifierScore(skill.key, receipt.version, {"r1": True, "r2": False}, 2, 1.0, 1, "ok")
    oracle = OracleScore(skill.key, 0.9, 1, 1, OracleFeedback(summary="ok"), "/tmp/out")
    state = RoundState(
        step=1,
        outer_round=0,
        mode="mode_b",
        mode_iter=0,
        skills={skill.key: skill},
        receipt=receipt,
        verifier_scores={skill.key: verifier},
        oracle_scores={skill.key: oracle},
        verifier_rank=[skill.key],
        oracle_rank=[skill.key],
        rank_alignment=1.0,
    )
    revision_input = ReceiptRevisionInput(
        task=task,
        receipt=receipt,
        skills={skill.key: skill},
        verifier_scores={skill.key: verifier},
        oracle_scores={skill.key: oracle},
        verifier_rank=[skill.key],
        oracle_rank=[skill.key],
        rank_alignment=1.0,
    )
    attempt = ReceiptRevisionAttempt("attempt_0", 1, True, [], receipt)
    config = SkillLiftConfig(exp_name="demo")
    assert config.use_llm is True
    result = ExperimentResult(task, config, receipt, skill, {skill.key: skill}, {"ok": True}, "/tmp/exp")

    for item in [task, receipt, skill, verifier, oracle, state, revision_input, attempt, config, result]:
        restored = type(item).from_dict(item.to_dict())
        assert restored.to_dict() == item.to_dict()


def test_skill_key_sorting_and_dict_key_decoding() -> None:
    assert sorted([SkillKey(1, 0), SkillKey(0, 2), SkillKey(0, 1)]) == [
        SkillKey(0, 1),
        SkillKey(0, 2),
        SkillKey(1, 0),
    ]
    skill = _skill()
    state = RoundState(0, 0, "mode_a", 0, {skill.key: skill}, recompute_receipt_bounds(_receipt()))
    restored = RoundState.from_dict(state.to_dict())
    assert list(restored.skills) == [SkillKey(0, 1)]


def test_receipt_bounds_recompute() -> None:
    receipt = recompute_receipt_bounds(_receipt())
    assert receipt.maximum_score == 2
    assert receipt.minimum_score == -3
    assert "hard_rules" not in receipt.to_dict()
    assert "anti_patterns" not in receipt.to_dict()


def test_config_defaults_decouple_openclaw_and_framework_model_files() -> None:
    config = SkillLiftConfig()
    assert config.openclaw_models_config_path() == DEFAULT_OPENCLAW_MODELS_CONFIG
    assert config.framework_models_config_path() == DEFAULT_FRAMEWORK_MODELS_CONFIG


def test_skilllift_config_rejects_invalid_normalized_thresholds() -> None:
    for field in (
        "mode_a_min_score_threshold",
        "rank_alignment_threshold",
        "oracle_threshold",
        "global_success_threshold",
    ):
        try:
            SkillLiftConfig(**{field: 1.5})
        except ValueError as exc:
            assert "must be in [0, 1]" in str(exc)
        else:
            raise AssertionError(f"{field} accepted an invalid threshold")


def test_legacy_models_config_no_longer_drives_framework_llm_by_default() -> None:
    config = SkillLiftConfig(models_config="models_config_openclaw.json")
    assert config.openclaw_models_config_path() == "models_config_openclaw.json"
    assert config.framework_models_config_path() == DEFAULT_FRAMEWORK_MODELS_CONFIG


def test_task_ranking_to_dict_uses_jsonable_skill_tokens() -> None:
    key_a = SkillKey(0, 1)
    key_b = SkillKey(1, 1)
    ranking = TaskRanking(
        task_id="task-001",
        domain="demo",
        oracle_rank=[key_b, key_a],
        skill_scores={
            key_a: OracleScore(key_a, 0.25, 0, feedback=OracleFeedback(summary="missed")),
            key_b: OracleScore(key_b, 0.75, 1, feedback=OracleFeedback(summary="passed")),
        },
        has_signal=True,
    )

    payload = ranking.to_dict()
    json.dumps(payload)

    assert payload["oracle_rank"] == ["s001_v001", "s000_v001"]
    assert sorted(payload["skill_scores"]) == ["s000_v001", "s001_v001"]
    assert payload["skill_scores"]["s001_v001"]["feedback"]["summary"] == "passed"


def test_receipt_revision_input_defaults_per_task_rankings_to_none() -> None:
    task = TaskSpec("task", "Do the work")
    receipt = recompute_receipt_bounds(_receipt())
    skill = _skill()
    verifier = VerifierScore(skill.key, receipt.version, {"r1": True}, 2, 1.0, 1, "ok")
    oracle = OracleScore(skill.key, 0.9, 1, 1, OracleFeedback(summary="ok"))

    revision_input = ReceiptRevisionInput(
        task,
        receipt,
        {skill.key: skill},
        {skill.key: verifier},
        {skill.key: oracle},
        [skill.key],
        [skill.key],
        1.0,
    )

    assert revision_input.per_task_rankings is None
    assert revision_input.to_dict()["per_task_rankings"] is None
