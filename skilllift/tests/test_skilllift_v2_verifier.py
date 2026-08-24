import pytest

from skilllift.errors import LLMOutputError, VerifierError
from skilllift.schemas import EvoSkill, Receipt, RubricCriterion, SkillKey, TaskSpec
from skilllift.verifier import fallback_score_skill, parse_verifier_output, score_skill, score_skills


def _receipt() -> Receipt:
    return Receipt(
        1,
        [
            RubricCriterion("r1", "Planning", "Includes validation", 2),
            RubricCriterion("r2", "Safety", "Calls forbidden send tool", -3),
        ],
        2,
        -3,
    )


def _skill() -> EvoSkill:
    return EvoSkill(SkillKey(0, 1), "demo", {"SKILL.md": "verify output and never send\n", "a.py": "print(1)\n"}, "a.py")


def test_parse_bool_list_and_recompute_score() -> None:
    with pytest.raises(VerifierError, match="criterion_hits"):
        parse_verifier_output({"judgement": [True, False], "raw_score": 99}, _receipt(), SkillKey(0, 1))


def test_parse_map_requires_exact_keys() -> None:
    score = parse_verifier_output({"criterion_hits": {"r1": True, "r2": True}}, _receipt(), SkillKey(0, 1))
    assert score.raw_score == -1
    assert score.positive_requirement_missing == []
    assert score.negative_violation_present == ["r2"]
    with pytest.raises(VerifierError):
        parse_verifier_output({"criterion_hits": {"r1": True}}, _receipt(), SkillKey(0, 1))


def test_verifier_feedback_separates_missing_merits_from_present_violations() -> None:
    score = parse_verifier_output(
        {"criterion_hits": {"r1": False, "r2": False}},
        _receipt(),
        SkillKey(0, 1),
    )

    assert score.positive_requirement_missing == ["r1"]
    assert score.negative_violation_present == []


def test_parse_map_rejects_sequential_keys_for_named_rubrics() -> None:
    receipt = Receipt(
        1,
        [
            RubricCriterion("tool_list_messages", "Tools", "Calls list messages", 1),
            RubricCriterion("tool_get_messages", "Tools", "Calls get messages", 1),
        ],
        2,
        0,
    )
    with pytest.raises(VerifierError, match="criterion_hits keys must match receipt rubrics"):
        parse_verifier_output({"criterion_hits": {"r1": True, "r2": False}}, receipt, SkillKey(0, 1))


def test_parse_recomputes_mismatched_derived_scores() -> None:
    score = parse_verifier_output(
        {
            "criterion_hits": {"r1": True, "r2": False},
            "raw_score": 99,
            "normalized_score": 0.123,
        },
        _receipt(),
        SkillKey(0, 1),
    )

    assert score.raw_score == 2
    assert score.normalized_score == 1.0


def test_fallback_verifier_returns_all_hits() -> None:
    score = fallback_score_skill(_skill(), _receipt())
    assert set(score.criterion_hits) == {"r1", "r2"}
    assert score.receipt_version == 1


def test_score_skills_assigns_rank() -> None:
    skills = {_skill().key: _skill()}
    scores = score_skills(TaskSpec("task", "body"), skills, _receipt(), None)
    assert scores[SkillKey(0, 1)].rank == 1


def test_score_skill_falls_back_when_llm_call_fails() -> None:
    class BrokenLLM:
        def call_json(self, system_prompt, user_prompt, temperature):
            raise LLMOutputError("429")

    score = score_skill(TaskSpec("task", "body"), _skill(), _receipt(), BrokenLLM())  # type: ignore[arg-type]

    assert set(score.criterion_hits) == {"r1", "r2"}
    assert "fallback_invalid_verifier_output" in score.rationale
    assert "429" in score.rationale


def test_score_skill_falls_back_when_llm_output_is_invalid() -> None:
    class BrokenLLM:
        def call_json(self, system_prompt, user_prompt, temperature):
            return {"criterion_hits": {"r1": True}}

    score = score_skill(TaskSpec("task", "body"), _skill(), _receipt(), BrokenLLM())  # type: ignore[arg-type]

    assert set(score.criterion_hits) == {"r1", "r2"}
    assert "fallback_invalid_verifier_output" in score.rationale


def test_score_skill_passes_mode_and_skill_format_to_prompt_builder(monkeypatch) -> None:
    calls = []

    class GoodLLM:
        def call_json(self, system_prompt, user_prompt, temperature):
            return {"criterion_hits": {"r1": True, "r2": False}, "raw_score": 2}

    def fake_build_verifier_prompt(task, skill, receipt, mode="mode_a", skill_format="code_package"):
        calls.append((mode, skill_format))
        return "system", "user"

    monkeypatch.setattr("skilllift.verifier.build_verifier_prompt", fake_build_verifier_prompt)

    score = score_skill(
        TaskSpec("task", "body"),
        _skill(),
        _receipt(),
        GoodLLM(),  # type: ignore[arg-type]
        mode="mode_b",
        skill_format="markdown_guide",
    )

    assert calls == [("mode_b", "markdown_guide")]
    assert score.raw_score == 2
