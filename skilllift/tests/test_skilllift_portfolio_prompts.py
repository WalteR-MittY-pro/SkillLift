from __future__ import annotations

import json
from pathlib import Path

import pytest

from skilllift.portfolio import PortfolioRef, portfolio_tree_hash
from skilllift.portfolio import (
    LLMPatchSkillGenerator,
    PortfolioContextLimitError,
    PublicTask,
    RubricHypothesis,
    ScalarOutcome,
    SearchDirection,
    SearchPlan,
    build_rubricator_prompt,
    build_skill_generator_prompt,
    parse_search_plan,
    parse_search_plan_text,
    serialize_search_history,
)


def _skill(name: str, body: str) -> str:
    return (
        f"---\nname: {name}\ndescription: Use for {name} tasks.\n"
        f"triggers:\n  - {name}\n---\n# {name}\n\n{body}\n"
    )


def _portfolio(tmp_path: Path) -> PortfolioRef:
    root = tmp_path / "portfolio"
    for name, body in (("alpha", "ALPHA_ONLY"), ("beta", "BETA_ONLY")):
        skill_root = root / name
        (skill_root / "references").mkdir(parents=True)
        (skill_root / "SKILL.md").write_text(_skill(name, body), encoding="utf-8")
        (skill_root / "references" / "notes.md").write_text(f"{name} notes\n", encoding="utf-8")
    (root / "shared-reference.md").write_text("shared notes\n", encoding="utf-8")
    return PortfolioRef.from_directory(
        task_id="task-1",
        root=root,
        seed_hash=portfolio_tree_hash(root),
        config_hash="cfg",
        curated_skill_names=("alpha", "beta"),
    )


def _payload() -> dict:
    return {
        "receipt_version": 2,
        "rubrics": [
            {
                "rubric_id": "r-tool",
                "requirement": "Use an executable capability",
                "evidence_refs": ["task.md", "alpha/SKILL.md"],
            },
            {
                "rubric_id": "r-check",
                "requirement": "Verify the output",
                "evidence_refs": ["task.md", "beta/SKILL.md"],
            },
        ],
        "directions": [
            {
                "direction_id": "d-0",
                "rubric_id": "r-tool",
                "hypothesis": "A helper will improve reward",
                "capability": "deterministic-helper",
                "target_scope": ["alpha/"],
                "cross_skill_rationale": None,
                "evidence_refs": ["task.md", "alpha/SKILL.md"],
                "novelty_key": "helper/v1",
            },
            {
                "direction_id": "d-1",
                "rubric_id": "r-check",
                "hypothesis": "A post-check will improve reward",
                "capability": "post-check",
                "target_scope": ["beta/"],
                "cross_skill_rationale": None,
                "evidence_refs": ["task.md", "beta/SKILL.md"],
                "novelty_key": "post-check/v1",
            },
        ],
    }


def test_search_plan_repairs_json_once_and_keeps_valid_directions(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    text = json.dumps(_payload())[:-1]

    plan = parse_search_plan_text(text, portfolio=portfolio, public_task_ref="task.md")

    assert plan.receipt_version == 2
    assert [direction.direction_id for direction in plan.directions] == ["d-0", "d-1"]


def test_search_plan_discards_invalid_nondiverse_and_repeated_directions(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    payload = _payload()
    duplicate = dict(payload["directions"][0], direction_id="d-duplicate", novelty_key="cosmetic-key")
    unknown_rubric = dict(payload["directions"][1], direction_id="d-secret", rubric_id="r-missing")
    repeated = dict(
        payload["directions"][1],
        direction_id="d-repeated",
        novelty_key="already-failed",
        capability="different",
    )
    payload["directions"] = [payload["directions"][0], duplicate, unknown_rubric, repeated]

    plan = parse_search_plan(
        payload,
        portfolio=portfolio,
        public_task_ref="task.md",
        prohibited_novelty_keys=("already-failed",),
    )

    assert [direction.direction_id for direction in plan.directions] == ["d-0"]


def test_search_plan_keeps_directions_with_task_source_scope(tmp_path: Path) -> None:
    # Scope containment is enforced when patches are applied
    # (apply_portfolio_patch raises on paths outside target_scope), not at parse time.
    portfolio = _portfolio(tmp_path)
    payload = _payload()
    payload["directions"] = [
        dict(
            payload["directions"][0],
            target_scope=["otp_src_27.3.2/lib/ssh/src/ssh_channel.erl"],
        )
    ]

    plan = parse_search_plan(payload, portfolio=portfolio, public_task_ref="task.md")

    assert len(plan.directions) == 1
    assert plan.directions[0].target_scope == ("otp_src_27.3.2/lib/ssh/src/ssh_channel.erl",)


def test_evidence_refs_accept_any_nonempty_string(tmp_path: Path) -> None:
    # evidence_refs are free-form citation strings; the parser must not reject
    # LLM-specific styles like "task.md:excerpt" or "Portfolio: skill/SKILL.md".
    portfolio = _portfolio(tmp_path)
    payload = _payload()
    payload["rubrics"][0]["evidence_refs"] = ["task.md:Expected Output", "Public Task: notes"]
    payload["directions"][0]["evidence_refs"] = ["Portfolio: alpha/SKILL.md helper API"]

    plan = parse_search_plan(payload, portfolio=portfolio, public_task_ref="task.md")

    assert plan.directions[0].evidence_refs == ("Portfolio: alpha/SKILL.md helper API",)
    rubric_tool = next(r for r in plan.rubrics if r.rubric_id == "r-tool")
    assert rubric_tool.evidence_refs == ("task.md:Expected Output", "Public Task: notes")


def test_cross_skill_direction_requires_atomic_rationale(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    payload = _payload()
    payload["directions"] = [
        dict(payload["directions"][0], target_scope=["alpha/", "beta/"], cross_skill_rationale=None)
    ]

    assert parse_search_plan(payload, portfolio=portfolio, public_task_ref="task.md").directions == ()

    payload["directions"][0]["cross_skill_rationale"] = "Both skills implement one end-to-end conversion."
    assert len(parse_search_plan(payload, portfolio=portfolio, public_task_ref="task.md").directions) == 1


def test_existing_rubric_id_cannot_change_requirement(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    previous = SearchPlan(
        receipt_version=1,
        rubrics=(RubricHypothesis("r-tool", "Original", ("task.md",)),),
        directions=(),
    )
    payload = _payload()
    payload["rubrics"][0]["requirement"] = "Silently changed"

    plan = parse_search_plan(
        payload,
        portfolio=portfolio,
        public_task_ref="task.md",
        previous_plan=previous,
    )

    assert next(rubric for rubric in plan.rubrics if rubric.rubric_id == "r-tool").requirement == "Original"


def test_search_history_schema_is_exact_and_rejects_raw_observations() -> None:
    outcome = ScalarOutcome(
        candidate_id="c-1",
        direction_id="d-1",
        reward=0.5,
        reward_delta=0.25,
        rank_group=1,
        is_valid=True,
        is_accepted=False,
    )
    indeterminate = ScalarOutcome.indeterminate("c-2", "d-2")

    serialized = serialize_search_history((outcome, indeterminate))

    expected = {
        "candidate_id",
        "direction_id",
        "reward",
        "reward_delta",
        "rank_group",
        "is_valid",
        "is_accepted",
    }
    assert all(set(item) == expected for item in serialized)
    assert serialized[1]["reward"] is None
    with pytest.raises(TypeError):
        serialize_search_history(({"trajectory": "SECRET_TRAJECTORY"},))  # type: ignore[arg-type]


def test_rubricator_prompt_contains_complete_evidence_and_only_scalar_history(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    task = PublicTask("task-1", "Do the public task", "task.md")
    history = (ScalarOutcome("c-1", "d-1", 0.5, 0.25, 1, True, False),)

    direction = SearchDirection(
        "d-old",
        "r-old",
        "Old hypothesis",
        "old-capability",
        ("alpha",),
        None,
        ("task.md",),
        "old/v1",
    )
    prompt = build_rubricator_prompt(
        task,
        portfolio,
        previous_plan=None,
        history=history,
        direction_history=(direction,),
    )

    assert "ALPHA_ONLY" in prompt.user
    assert "BETA_ONLY" in prompt.user
    assert "alpha notes" in prompt.user
    assert '"reward_delta": 0.25' in prompt.user
    assert '"direction_id": "d-old"' in prompt.user
    assert "target_scope must be paths from Portfolio Manifest" in prompt.system
    assert "never task source code paths" in prompt.system
    for forbidden in ("SECRET_TRAJECTORY", "artifact_path", "stderr", "verifier_data"):
        assert forbidden not in prompt.user


def test_rubricator_prompt_rejects_oversized_evidence_instead_of_truncating(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    task = PublicTask("task-1", "Do the public task", "task.md")

    with pytest.raises(PortfolioContextLimitError, match="context"):
        build_rubricator_prompt(task, portfolio, previous_plan=None, history=(), max_input_chars=10)


def test_rubricator_prompt_uses_core_evidence_only_when_complete_prompt_overflows(
    tmp_path: Path,
) -> None:
    portfolio = _portfolio(tmp_path)
    (portfolio.root / "alpha" / "references" / "notes.md").write_text(
        "NESTED_REFERENCE_ONLY\n" * 1000,
        encoding="utf-8",
    )
    portfolio = PortfolioRef.from_directory(
        task_id="task-1",
        root=portfolio.root,
        seed_hash=portfolio_tree_hash(portfolio.root),
        config_hash="cfg",
        curated_skill_names=("alpha", "beta"),
    )
    task = PublicTask("task-1", "Do the public task", "task.md")

    prompt = build_rubricator_prompt(
        task,
        portfolio,
        previous_plan=None,
        history=(),
        max_input_chars=8_000,
        core_evidence_on_overflow=True,
    )

    assert "ALPHA_ONLY" in prompt.user
    assert "BETA_ONLY" in prompt.user
    assert "shared notes" in prompt.user
    assert "NESTED_REFERENCE_ONLY" not in prompt.user
    assert "alpha/references/notes.md" in prompt.user


def test_rubricator_prompt_keeps_complete_evidence_when_it_fits(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    task = PublicTask("task-1", "Do the public task", "task.md")

    prompt = build_rubricator_prompt(
        task,
        portfolio,
        previous_plan=None,
        history=(),
        max_input_chars=120_000,
        core_evidence_on_overflow=True,
    )

    assert "alpha notes" in prompt.user


def test_skill_generator_prompt_includes_manifest_but_only_target_file_contents(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    task = PublicTask("task-1", "Do the public task", "task.md")
    direction = SearchDirection(
        direction_id="d-0",
        rubric_id="r-tool",
        hypothesis="Improve alpha",
        capability="helper",
        target_scope=("alpha",),
        cross_skill_rationale=None,
        evidence_refs=("task.md", "alpha/SKILL.md"),
        novelty_key="helper/v1",
    )

    prompt = build_skill_generator_prompt(task, direction, portfolio)

    assert "ALPHA_ONLY" in prompt.user
    assert "alpha notes" in prompt.user
    assert "BETA_ONLY" not in prompt.user
    assert "beta/SKILL.md" in prompt.user
    assert '"origin": "curated_asset"' in prompt.user
    assert "Return exactly one unified diff" in prompt.system
    assert "full Portfolio JSON" not in prompt.user


def test_skill_generator_uses_creative_temperature(tmp_path: Path) -> None:
    class RecordingClient:
        temperature = None

        def call_text(self, system_prompt, user_prompt, temperature=0.0):
            self.temperature = temperature
            return "generated output"

    portfolio = _portfolio(tmp_path)
    task = PublicTask("task-1", "Do the public task", "task.md")
    direction = SearchDirection(
        direction_id="d-0",
        rubric_id="r-tool",
        hypothesis="Improve alpha",
        capability="helper",
        target_scope=("alpha",),
        cross_skill_rationale=None,
        evidence_refs=("task.md", "alpha/SKILL.md"),
        novelty_key="helper/v1",
    )
    client = RecordingClient()

    output = LLMPatchSkillGenerator(client).generate(task, direction, portfolio)

    assert output == "generated output"
    assert client.temperature == 0.3
