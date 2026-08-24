import json

import pytest

from skilllift.errors import LLMOutputError
from skilllift.portfolio import PortfolioContextLimitError
from skilllift.persistence import ExperimentStore
from skilllift.rubricator import (
    _revision_temperature,
    build_fallback_receipt,
    build_skill_evidence_package,
    generate_initial_receipt,
    normalize_receipt_candidate,
    record_revision_attempt,
    revise_receipt,
    validate_receipt,
)
from skilllift.schemas import EvidenceBudgetConfig, EvoSkill, OracleFeedback, OracleScore, Receipt, RubricCriterion, SkillKey, TaskSpec, VerifierScore


def test_fallback_receipt_is_valid() -> None:
    receipt = build_fallback_receipt(TaskSpec("task", "body"))
    report = validate_receipt(receipt)
    assert report.ok
    assert 5 <= len(receipt.rubrics) <= 15


def test_validation_collects_errors() -> None:
    receipt = Receipt(
        1,
        [
            RubricCriterion("r1", "A", "", 0),
            RubricCriterion("r1", "B", "hidden_answer leak", 10),
        ],
        1,
        1,
    )
    report = validate_receipt(receipt)
    assert not report.ok
    assert "duplicate_rubric_id" in report.error_codes
    assert "hidden_answer_leak" in report.error_codes


def test_normalize_receipt_recomputes_bounds() -> None:
    receipt = normalize_receipt_candidate(
        {
            "rubrics": [
                {"rubric_id": "r1", "category": "A", "criterion": "Has plan", "points": 2},
                {"rubric_id": "r2", "category": "B", "criterion": "Bad thing", "points": -1},
            ],
            "maximum_score": 99,
            "minimum_score": 99,
        },
        old_receipt=None,
    )
    assert receipt.maximum_score == 2
    assert receipt.minimum_score == -1


def test_receipt_revision_requires_structured_deletion_evidence() -> None:
    old = build_fallback_receipt(TaskSpec("task", "body"))
    payload = {
        "version": old.version + 1,
        "rubrics": [rubric.to_dict() for rubric in old.rubrics[:-1]],
    }
    candidate = normalize_receipt_candidate(payload, old_receipt=old)

    report = validate_receipt(candidate, old_receipt=old)

    assert not report.ok
    assert "removed_rubric_declaration_missing" in report.error_codes


def test_receipt_revision_accepts_declared_deletion_with_evidence() -> None:
    old = build_fallback_receipt(TaskSpec("task", "body"))
    removed = old.rubrics[-1]
    payload = {
        "version": old.version + 1,
        "rubrics": [rubric.to_dict() for rubric in old.rubrics[:-1]],
        "removed_rubrics": [
            {
                "rubric_id": removed.rubric_id,
                "reason": "The criterion is not discriminative for the public task.",
                "evidence_refs": ["oracle_contrast.oracle_rank_groups"],
            }
        ],
    }
    candidate = normalize_receipt_candidate(payload, old_receipt=old)

    assert validate_receipt(candidate, old_receipt=old).ok


def test_removal_declarations_apply_only_to_one_revision() -> None:
    original = build_fallback_receipt(TaskSpec("task", "body"))
    removed = original.rubrics[-1]
    revised = normalize_receipt_candidate(
        {
            "rubrics": [rubric.to_dict() for rubric in original.rubrics[:-1]],
            "removed_rubrics": [{
                "rubric_id": removed.rubric_id,
                "reason": "not discriminative",
                "evidence_refs": ["oracle_contrast.oracle_rank_groups"],
            }],
        },
        old_receipt=original,
    )
    next_revision = normalize_receipt_candidate(
        {
            "rubrics": [rubric.to_dict() for rubric in revised.rubrics],
            "removed_rubrics": [],
        },
        old_receipt=revised,
    )

    assert validate_receipt(next_revision, old_receipt=revised).ok


def test_receipt_revision_rejects_fractional_version() -> None:
    old = build_fallback_receipt(TaskSpec("task", "body"))
    payload = {
        "version": old.version + 1.5,
        "rubrics": [rubric.to_dict() for rubric in old.rubrics],
    }
    candidate = normalize_receipt_candidate(payload, old_receipt=old)

    assert "receipt_version_must_increment_by_one" in validate_receipt(
        candidate, old_receipt=old
    ).error_codes


def test_revision_temperature_adapts_to_alignment_and_retry() -> None:
    assert _revision_temperature(0.0, 0) == 0.6
    assert _revision_temperature(0.5, 0) == 0.4
    assert _revision_temperature(0.85, 0) == 0.15
    assert _revision_temperature(None, 0) == 0.4
    assert _revision_temperature(0.0, 1) == 0.15


def test_fractional_points_are_rejected_for_repair() -> None:
    receipt = normalize_receipt_candidate(
        {
            "rubrics": [
                {"rubric_id": f"r{i}", "category": "A", "criterion": f"Has item {i}", "points": 1}
                for i in range(1, 5)
            ]
            + [{"rubric_id": "r5", "category": "B", "criterion": "Uses tools", "points": 0.5}],
        },
        old_receipt=None,
    )
    report = validate_receipt(receipt)
    assert not report.ok
    assert "points_out_of_range" in report.error_codes


def test_generate_initial_receipt_repairs_invalid_llm_candidate() -> None:
    class FakeLLM:
        def __init__(self):
            self.calls = 0

        def call_json(self, system_prompt, user_prompt, temperature):
            self.calls += 1
            if self.calls == 1:
                return {
                    "rubrics": [
                        {"rubric_id": f"r{i}", "category": "A", "criterion": f"Has item {i}", "points": 1}
                        for i in range(1, 16)
                    ]
                    + [{"rubric_id": "r16", "category": "B", "criterion": "Uses tools", "points": 0.5}],
                }
            return {
                "rubrics": [
                    {"rubric_id": "r1", "category": "Planning", "criterion": "Has plan", "points": 1},
                    {"rubric_id": "r2", "category": "Tools", "criterion": "Uses required tools", "points": 2},
                    {"rubric_id": "r3", "category": "Output", "criterion": "Writes required output", "points": 2},
                    {"rubric_id": "r4", "category": "Safety", "criterion": "Avoids forbidden actions", "points": 1},
                    {"rubric_id": "r5", "category": "Noise", "criterion": "Includes irrelevant noise", "points": -1},
                ],
                "metadata": {"source": "fake"},
            }

    llm = FakeLLM()
    receipt = generate_initial_receipt(TaskSpec("task", "body"), "reference", llm)  # type: ignore[arg-type]
    assert llm.calls == 2
    assert receipt.metadata["llm_repaired"] is True
    assert validate_receipt(receipt).ok


def test_generate_initial_receipt_falls_back_when_llm_and_repair_fail() -> None:
    class BrokenLLM:
        def call_json(self, system_prompt, user_prompt, temperature):
            return {"rubrics": []}

    receipt = generate_initial_receipt(TaskSpec("task", "body"), "reference", BrokenLLM())  # type: ignore[arg-type]

    assert receipt.metadata["llm_initial_failed"] is True
    assert receipt.metadata["fallback_stage"] == "repair_validation"
    assert validate_receipt(receipt).ok


def test_generate_initial_receipt_falls_back_when_llm_call_fails() -> None:
    class BrokenLLM:
        def call_json(self, system_prompt, user_prompt, temperature):
            raise LLMOutputError("timeout")

    receipt = generate_initial_receipt(TaskSpec("task", "body"), "reference", BrokenLLM())  # type: ignore[arg-type]

    assert receipt.metadata["llm_initial_failed"] is True
    assert receipt.metadata["fallback_stage"] == "initial_call"
    assert receipt.metadata["fallback_reason"] == "timeout"
    assert validate_receipt(receipt).ok


def test_revision_attempt_is_written(tmp_path) -> None:
    store = ExperimentStore(tmp_path / "exp")
    old = build_fallback_receipt(TaskSpec("task", "body"))
    attempt = record_revision_attempt(
        store,
        old,
        old,
        validate_receipt(old),
        0,
        {"prompt.txt": "prompt"},
    )
    assert attempt.accepted
    assert (tmp_path / "exp" / "receipt_revision_attempts" / "receipt_v1_attempt_0" / "metadata.json").exists()


def test_skill_evidence_package_links_scores() -> None:
    key = SkillKey(0, 1)
    skill = EvoSkill(key, "demo", {"SKILL.md": "x", "a.py": "print(1)"}, "a.py")
    feedback = OracleFeedback(
        stderr_excerpt="SECRET_STDERR",
        traceback_summary="SECRET_TRACEBACK",
        metadata={
            "chat_summary": "SECRET_CHAT",
            "skill_observation": {"skill_key": "s000_v001"},
        },
    )
    evidence = build_skill_evidence_package(
        TaskSpec("task", "body"),
        {key: skill},
        {key: VerifierScore(key, 1, {}, 0, 0.0)},
        {key: OracleScore(key, 0.5, 0, feedback=feedback, output_dir="/private/output")},
    )
    assert evidence["skills"][0]["key"] == "s000_v001"
    assert evidence["skills"][0]["skill_md"] == "x"
    assert evidence["skills"][0]["entrypoint_code"] == "print(1)"
    assert evidence["skills"][0]["oracle_score"]["oracle_score"] == 0.5
    assert "skill_observation" not in evidence["skills"][0]["oracle_score"]
    assert "SECRET_STDERR" not in str(evidence)
    assert "SECRET_TRACEBACK" not in str(evidence)
    assert "SECRET_CHAT" not in str(evidence)
    assert "/private/output" not in str(evidence)


def test_skill_evidence_budget_is_global_and_preserves_all_skill_markdown() -> None:
    skills = {}
    verifier = {}
    oracle = {}
    for slot in range(4):
        key = SkillKey(slot, 1)
        skill_md = f"skill-{slot}:" + "x" * 11_000
        skills[key] = EvoSkill(
            key,
            "skilllift-demo",
            {"SKILL.md": skill_md, "scripts/process.py": "y" * 8_000},
            "scripts/process.py",
        )
        verifier[key] = VerifierScore(key, 1, {}, 0, 0.0)
        oracle[key] = OracleScore(key, 0.5, 0)

    evidence = build_skill_evidence_package(
        TaskSpec("task", "body"),
        skills,
        verifier,
        oracle,
        EvidenceBudgetConfig(),
    )

    assert [item["skill_md"] for item in evidence["skills"]] == [
        skills[key].files["SKILL.md"] for key in sorted(skills)
    ]
    evidence_chars = sum(
        len(item["skill_md"])
        + sum(len(aux["content"]) for aux in item["aux_files"])
        for item in evidence["skills"]
    )
    assert evidence_chars <= 60_000


def test_skill_evidence_default_keeps_every_file() -> None:
    key = SkillKey(0, 1)
    skill = EvoSkill(
        key,
        "demo",
        {"SKILL.md": "skill", "references/full.md": "complete reference"},
        "references/full.md",
    )
    evidence = build_skill_evidence_package(
        TaskSpec("task", "body"),
        {key: skill},
        {key: VerifierScore(key, 1, {}, 0, 0.0)},
        {key: OracleScore(key, 0.5, 0)},
    )
    assert evidence["evidence_scope"]["seed"] == "complete_portfolio"
    assert evidence["skills"][0]["aux_files"] == [
        {"path": "references/full.md", "content": "complete reference"}
    ]


def test_initial_receipt_stops_before_llm_when_full_input_exceeds_limit() -> None:
    class NeverCalled:
        def call_json(self, *args, **kwargs):
            raise AssertionError("the rubricator must not be called")

    with pytest.raises(PortfolioContextLimitError):
        generate_initial_receipt(
            TaskSpec("task", "body"),
            "x" * 10_000,
            NeverCalled(),
            max_input_chars=1,
        )


def test_revise_receipt_accepts_valid_llm_candidate(tmp_path) -> None:
    class FakeLLM:
        def __init__(self):
            self.temperatures = []

        def call_json(self, system_prompt, user_prompt, temperature):
            self.temperatures.append(temperature)
            return {
                "version": 2,
                "rubrics": [
                    {"rubric_id": "r1", "category": "Planning", "criterion": "Has plan", "points": 1},
                    {"rubric_id": "r2", "category": "Tools", "criterion": "Uses required tools", "points": 2},
                    {"rubric_id": "r3", "category": "Output", "criterion": "Writes required output", "points": 2},
                    {"rubric_id": "r4", "category": "Safety", "criterion": "Avoids forbidden actions", "points": 1},
                    {"rubric_id": "r5", "category": "Noise", "criterion": "Includes irrelevant noise", "points": -1},
                ],
                "removed_rubrics": [{
                    "rubric_id": "r6",
                    "reason": "not discriminative",
                    "evidence_refs": ["oracle_contrast.oracle_rank_groups"],
                }],
                "metadata": {"source": "fake"},
            }

    from skilllift.schemas import ReceiptRevisionInput

    store = ExperimentStore(tmp_path / "exp")
    task = TaskSpec("task", "body")
    old = build_fallback_receipt(task)
    key = SkillKey(0, 1)
    skill = EvoSkill(key, "demo", {"SKILL.md": "x", "a.py": "print(1)"}, "a.py")
    verifier = {key: VerifierScore(key, old.version, {r.rubric_id: True for r in old.rubrics}, 1, 1.0)}
    oracle = {key: OracleScore(key, 1.0, 1)}
    revision_input = ReceiptRevisionInput(task, old, {key: skill}, verifier, oracle, [key], [key], 1.0)
    llm = FakeLLM()
    revised = revise_receipt(revision_input, llm, store)  # type: ignore[arg-type]
    assert revised.version == old.version + 1
    assert llm.temperatures == [0.15]
    metadata_path = tmp_path / "exp" / "receipt_revision_attempts" / "receipt_v1_attempt_0" / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    assert metadata["metadata"]["temperature"] == 0.15


def test_revise_receipt_retry_prompt_contains_validation_errors(tmp_path) -> None:
    class FakeLLM:
        def __init__(self):
            self.calls = 0
            self.prompts = []
            self.temperatures = []

        def call_json(self, system_prompt, user_prompt, temperature):
            self.calls += 1
            self.prompts.append(user_prompt)
            self.temperatures.append(temperature)
            if self.calls == 1:
                return {"rubrics": []}
            return {
                "version": 2,
                "rubrics": [
                    {"rubric_id": "r1", "category": "Planning", "criterion": "Has plan", "points": 1},
                    {"rubric_id": "r2", "category": "Tools", "criterion": "Uses required tools", "points": 2},
                    {"rubric_id": "r3", "category": "Output", "criterion": "Writes required output", "points": 2},
                    {"rubric_id": "r4", "category": "Safety", "criterion": "Avoids forbidden actions", "points": 1},
                    {"rubric_id": "r5", "category": "Noise", "criterion": "Includes irrelevant noise", "points": -1},
                ],
                "removed_rubrics": [{
                    "rubric_id": "r6",
                    "reason": "not discriminative",
                    "evidence_refs": ["oracle_contrast.oracle_rank_groups"],
                }],
                "metadata": {"source": "fake"},
            }

    from skilllift.schemas import ReceiptRevisionInput

    store = ExperimentStore(tmp_path / "exp")
    task = TaskSpec("task", "body")
    old = build_fallback_receipt(task)
    key = SkillKey(0, 1)
    skill = EvoSkill(key, "demo", {"SKILL.md": "x", "a.py": "print(1)"}, "a.py")
    verifier = {key: VerifierScore(key, old.version, {r.rubric_id: True for r in old.rubrics}, 1, 1.0)}
    oracle = {key: OracleScore(key, 1.0, 1)}
    revision_input = ReceiptRevisionInput(task, old, {key: skill}, verifier, oracle, [key], [key], 0.0)
    llm = FakeLLM()
    revised = revise_receipt(revision_input, llm, store)  # type: ignore[arg-type]
    assert revised.version == old.version + 1
    assert llm.calls == 2
    assert llm.temperatures == [0.6, 0.15]
    assert "validation_report" in llm.prompts[1]
    assert "rubric_count_out_of_range" in llm.prompts[1]
