"""Tests for skilllift v2 SG prompts (seed / variant / update).

Covers the markdown_guide and code_package paths of all three build_sg_*_prompt
functions. Locks in the agent-facing workflow discipline rules so regressions
to the telecom-style "agent loops asking user to call schema-internal tools"
failure mode are caught at prompt level.
"""

from __future__ import annotations

import pytest

from skilllift.baselines.prompts import (
    _agent_facing_workflow_discipline_rules,
    _agent_facing_workflow_repair_rules,
    build_sg_seed_prompt,
    build_sg_update_prompt,
    build_sg_variant_prompt,
)
from skilllift.schemas import (
    EvoSkill,
    Receipt,
    SkillKey,
    TaskSpec,
    VerifierScore,
)


# Rules added 2026-07-05 to fix the "agent loops asking user to call
# schema-internal tools" failure mode (telecom max_steps death loop).
DISCIPLINE_RULE_MARKERS = [
    "Action ownership",
    "Delegated steps",
    "No surrogate tool execution",
    "No loop stalls",
]
REPAIR_RULE_MARKER = "fix action ownership first"

# Words that must NOT appear in the new rules (SDD §10.3 benchmark-agnostic).
BANNED_BENCHMARK_WORDS = [
    "tau2",
    "telecom",
    "airline",
    "retail",
    "customer",
    "order",
    "reservation",
]


@pytest.fixture
def task_spec() -> TaskSpec:
    return TaskSpec(task_name="tau2_test", task_description="public task description")


@pytest.fixture
def seed_skill() -> EvoSkill:
    return EvoSkill(
        skill_name="seed",
        key=SkillKey(0, 1),
        files={"SKILL.md": "# seed skill"},
        entrypoint="SKILL.md",
    )


@pytest.fixture
def receipt() -> Receipt:
    return Receipt(
        baseline_score=0.0,
        maximum_score=1.0,
        minimum_score=0.0,
        rubrics=[],
        version=1,
    )


@pytest.fixture
def verifier_score() -> VerifierScore:
    return VerifierScore(
        skill=SkillKey(0, 1),
        receipt_version=1,
        criterion_hits={},
        raw_score=1,
        normalized_score=0.5,
        rationale="test",
    )


def _full_prompt(system_user_tuple) -> str:
    system, user = system_user_tuple
    return f"{system}\n{user}"


@pytest.mark.parametrize("skill_format", ["markdown_guide", "code_package"])
def test_build_sg_seed_prompt_contains_discipline_rules(task_spec, skill_format):
    content_block = "cb_token" if skill_format == "markdown_guide" else None
    txt = _full_prompt(
        build_sg_seed_prompt(
            task_spec,
            "reference material",
            skill_format=skill_format,
            content_block=content_block,
        )
    )
    for marker in DISCIPLINE_RULE_MARKERS:
        assert marker in txt, f"seed/{skill_format} missing discipline rule: {marker}"


@pytest.mark.parametrize("skill_format", ["markdown_guide", "code_package"])
def test_build_sg_variant_prompt_contains_discipline_rules(
    task_spec, seed_skill, skill_format
):
    content_block = "cb_token" if skill_format == "markdown_guide" else None
    txt = _full_prompt(
        build_sg_variant_prompt(
            task_spec,
            seed_skill,
            1,
            skill_format=skill_format,
            content_block=content_block,
        )
    )
    for marker in DISCIPLINE_RULE_MARKERS:
        assert marker in txt, (
            f"variant/{skill_format} missing discipline rule: {marker}"
        )


@pytest.mark.parametrize("skill_format", ["markdown_guide", "code_package"])
def test_build_sg_update_prompt_contains_repair_rule(
    task_spec, seed_skill, receipt, verifier_score, skill_format
):
    """update prompt uses _repair_rules (not _discipline_rules); assert the
    'fix action ownership first' clause is present."""
    content_block = "cb_token" if skill_format == "markdown_guide" else None
    txt = _full_prompt(
        build_sg_update_prompt(
            task_spec,
            seed_skill,
            receipt,
            verifier_score,
            skill_format=skill_format,
            content_block=content_block,
        )
    )
    assert REPAIR_RULE_MARKER in txt, (
        f"update/{skill_format} missing repair rule: {REPAIR_RULE_MARKER}"
    )


def test_discipline_rules_are_benchmark_agnostic():
    """SDD §10.3: rules must not contain benchmark/domain-specific words."""
    all_rules = (
        _agent_facing_workflow_discipline_rules()
        + _agent_facing_workflow_repair_rules()
    )
    for rule in all_rules:
        low = rule.lower()
        for banned in BANNED_BENCHMARK_WORDS:
            assert banned.lower() not in low, (
                f"rule contains benchmark word {banned!r}: {rule[:100]}"
            )
