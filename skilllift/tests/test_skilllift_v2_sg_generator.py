import pytest

from skilllift.errors import SkillPackageError
from skilllift.schemas import SkillLiftConfig, Receipt, RubricCriterion, SkillKey, TaskSpec, VerifierScore
from skilllift.baselines.sg import (
    generate_seed_skill,
    initialize_skill_group,
    parse_markdown_guide_output,
    update_skill,
    update_skill_group,
)


class BrokenLLM:
    def call_json(self, *args, **kwargs):
        raise RuntimeError("boom")


class GuidanceLLM:
    def call_json(self, *args, **kwargs):
        return {
            "guidance_items": ["Verify every updated deadline before writing results.md."],
            "metadata": {"summary": "deadline verification update"},
        }


def _mode_a_metadata(changed_files: list[str]) -> dict:
    return {
        "summary": "targeted verifier update",
        "strategy": "mode_a_update",
        "targeted_rubrics": ["r1"],
        "changed_files": changed_files,
        "expected_effect": "Updated files add the missing extra checks for r1.",
    }


class PatchLLM:
    def call_json(self, *args, **kwargs):
        return {
            "package_mode": "patch",
            "files": [{"path": "executor.py", "content": "def run_pipeline():\n    return 'patched'\n"}],
            "entrypoint": "executor.py",
            "metadata": _mode_a_metadata(["executor.py"]),
        }


class FullPackageLLM:
    def call_json(self, *args, **kwargs):
        return {
            "package_mode": "full",
            "files": [
                {"path": "SKILL.md", "content": "---\nname: demo\ndescription: Updated trigger\n---\n"},
                {"path": "executor.py", "content": "def run_pipeline():\n    return 'full'\n"},
            ],
            "entrypoint": "executor.py",
            "metadata": {**_mode_a_metadata(["SKILL.md", "executor.py"]), "strategy": "mode_a_rewrite"},
        }


class RepairingUpdateLLM:
    def __init__(self) -> None:
        self.calls = 0

    def call_json(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {"guidance_items": ["legacy shape"]}
        return {
            "package_mode": "patch",
            "files": [{"path": "executor.py", "content": "def run_pipeline():\n    return 'repaired'\n"}],
            "entrypoint": "executor.py",
            "metadata": _mode_a_metadata(["executor.py"]),
        }


class RepairingPackageLLM:
    def __init__(self) -> None:
        self.calls = 0

    def call_json(self, *args, **kwargs):
        self.calls += 1
        content = "def run_pipeline():n    return 'bad'\n" if self.calls == 1 else "def run_pipeline():\n    return 'ok'\n"
        return {
            "package_mode": "full",
            "files": [
                {"path": "SKILL.md", "content": "---\nname: demo\ndescription: Slack action extraction\n---\n"},
                {"path": "executor.py", "content": content},
            ],
            "entrypoint": "executor.py",
            "metadata": {"summary": "repair test"},
        }


class MissingTargetRepairLLM:
    def __init__(self) -> None:
        self.calls = 0
        self.repair_user = ""

    def call_json(self, system, user, *args, **kwargs):
        self.calls += 1
        payload = {
            "package_mode": "patch",
            "files": [{"path": "executor.py", "content": "def run_pipeline():\n    return 'targeted'\n"}],
            "entrypoint": "executor.py",
            "metadata": {"summary": "missing targeting", "strategy": "mode_a_update"},
        }
        if self.calls == 1:
            return payload
        self.repair_user = user
        payload["metadata"] = _mode_a_metadata(["executor.py"])
        return payload


class OracleMetricTargetRepairLLM:
    def __init__(self) -> None:
        self.calls = 0
        self.repair_user = ""

    def call_json(self, system, user, *args, **kwargs):
        self.calls += 1
        payload = {
            "package_mode": "patch",
            "files": [{"path": "executor.py", "content": "def run_pipeline():\n    return 'counted'\n"}],
            "entrypoint": "executor.py",
            "metadata": {
                "summary": "figure count repair",
                "strategy": "mode_a_update",
                "targeted_rubrics": ["figure_count_accuracy"],
                "changed_files": ["executor.py"],
                "expected_effect": "Improve figure counting.",
            },
        }
        if self.calls == 1:
            return payload
        self.repair_user = user
        payload["metadata"]["targeted_rubrics"] = ["r6"]
        return payload


class NonActionableTargetLLM:
    def call_json(self, *args, **kwargs):
        return {
            "package_mode": "patch",
            "files": [{"path": "executor.py", "content": "def run_pipeline():\n    return 'bad target'\n"}],
            "entrypoint": "executor.py",
            "metadata": {
                **_mode_a_metadata(["executor.py"]),
                "targeted_rubrics": ["r2"],
            },
        }


class MetadataOnlyUpdateLLM:
    def call_json(self, *args, **kwargs):
        return {
            "package_mode": "patch",
            "files": [],
            "entrypoint": "executor.py",
            "metadata": _mode_a_metadata(["executor.py"]),
        }


class SuccessfulUpdateLLM:
    def call_json(self, *args, **kwargs):
        return {
            "package_mode": "patch",
            "files": [{"path": "executor.py", "content": "def run_pipeline():\n    return 'clean'\n"}],
            "entrypoint": "executor.py",
            "metadata": _mode_a_metadata(["executor.py"]),
        }


def _receipt() -> Receipt:
    return Receipt(1, [RubricCriterion("r1", "Planning", "Has extra checks", 1)], 1, 0)


def _two_rubric_receipt() -> Receipt:
    return Receipt(
        1,
        [
            RubricCriterion("r1", "Planning", "Has extra checks", 1),
            RubricCriterion("r2", "Execution", "Uses execution checks", 1),
        ],
        2,
        0,
    )


def _figure_receipt() -> Receipt:
    return Receipt(1, [RubricCriterion("r6", "figure_counting", "Counts visible figures", 3)], 3, 0)


def test_initialize_skill_group_returns_k_slots() -> None:
    skills = initialize_skill_group(TaskSpec("task", "body"), SkillLiftConfig(skill_count=3), "reference", None)
    assert sorted(key.slot for key in skills) == [0, 1, 2]
    assert all("SKILL.md" in skill.files for skill in skills.values())
    assert all("description:" in skill.files["SKILL.md"] for skill in skills.values())
    assert all(any(path.endswith(".py") for path in skill.files) for skill in skills.values())


def test_markdown_guide_fallback_generates_only_skill_markdown() -> None:
    skills = initialize_skill_group(
        TaskSpec("task", "body"),
        SkillLiftConfig(skill_count=2, skill_format="markdown_guide"),
        "reference",
        None,
    )

    assert sorted(key.slot for key in skills) == [0, 1]
    assert all(set(skill.files) == {"SKILL.md"} for skill in skills.values())
    assert all(skill.entrypoint is None for skill in skills.values())


def test_variant_fallback_description_uses_task_signal() -> None:
    task = TaskSpec("03_Social_Interaction_task_2_chat_action_extraction", "Pull action items from Slack messages.")
    skills = initialize_skill_group(task, SkillLiftConfig(skill_count=2), "reference", None)
    variant = skills[SkillKey(1, 1)]
    assert "Slack" in variant.files["SKILL.md"]
    assert "action items" in variant.files["SKILL.md"]


def test_update_skill_keeps_slot_and_increments_version() -> None:
    task = TaskSpec("task", "body")
    skill = next(iter(initialize_skill_group(task, SkillLiftConfig(skill_count=1), "reference", None).values()))
    score = VerifierScore(skill.key, 1, {"r1": False}, 0, 0.0)
    updated = update_skill(task, skill, _receipt(), score, PatchLLM())
    assert updated.key.slot == skill.key.slot
    assert updated.key.version == skill.key.version + 1


def test_update_skill_group_rekeys_by_new_version() -> None:
    task = TaskSpec("task", "body")
    skills = initialize_skill_group(task, SkillLiftConfig(skill_count=2), "reference", None)
    scores = {key: VerifierScore(key, 1, {"r1": False}, 0, 0.0) for key in skills}
    updated = update_skill_group(task, skills, _receipt(), scores, PatchLLM())
    assert sorted(key.version for key in updated) == [2, 2]
    assert {key.slot for key in updated} == {0, 1}


def test_seed_fallback_preserves_initial_skill_workflow() -> None:
    reference = (
        "# Task Description\nbody\n\n"
        "# Initial Skill\n"
        "---\nname: 03_task2\ndescription: Slack task extractor\n---\n\n"
        "## Workflow\nCall `/slack/messages` and write `/tmp_workspace/results/results.md`.\n\n"
        "# Global Skills\nignored\n"
    )
    skill = generate_seed_skill(TaskSpec("task", "body"), reference, None)
    assert "/slack/messages" in skill.files["SKILL.md"]
    assert "/tmp_workspace/results/results.md" in skill.files["SKILL.md"]


def test_parse_markdown_guide_full_output_preserves_literal_xml_like_text() -> None:
    raw = """
<package mode="full" strategy="seed">
<summary>precondition guide</summary>
<file path="SKILL.md" content_block="skill_block_a1b2c3d4" />

<skill_block_a1b2c3d4>
# Guide

Keep these literal examples:
- </file>
- </package>
- <receipt>public rubric note</receipt>
```text
</file> inside a fence
```
</skill_block_a1b2c3d4>
</package>
"""

    package = parse_markdown_guide_output(raw, "markdown_guide", "skill_block_a1b2c3d4")

    assert package.entrypoint is None
    assert set(package.files) == {"SKILL.md"}
    assert "</file>" in package.files["SKILL.md"]
    assert "</package>" in package.files["SKILL.md"]
    assert "<receipt>public rubric note</receipt>" in package.files["SKILL.md"]
    assert package.metadata["summary"] == "precondition guide"
    assert package.metadata["strategy"] == "seed"


def test_parse_markdown_guide_full_output_preserves_same_content_block_tag_in_body() -> None:
    raw = """
<package mode="full" strategy="seed">
<summary>precondition guide</summary>
<file path="SKILL.md" content_block="skill_block_a1b2c3d4" />

<skill_block_a1b2c3d4>
# Guide

Tell the agent that `foo <skill_block_a1b2c3d4>nested</skill_block_a1b2c3d4> bar`
is just literal guide text.
</skill_block_a1b2c3d4>
</package>
"""

    package = parse_markdown_guide_output(raw, "markdown_guide", "skill_block_a1b2c3d4")

    assert "foo <skill_block_a1b2c3d4>nested</skill_block_a1b2c3d4> bar" in package.files["SKILL.md"]


def test_parse_markdown_guide_patch_output_extracts_mode_a_metadata() -> None:
    raw = """
<package mode="patch" strategy="mode_a_update">
<targeted_rubrics>r1, r3</targeted_rubrics>
<expected_effect>Add result checks.</expected_effect>
<changed_files>SKILL.md</changed_files>
<file path="SKILL.md" content_block="skill_block_patch" />

<skill_block_patch>
# Updated Guide
</skill_block_patch>
</package>
"""

    package = parse_markdown_guide_output(raw, "markdown_guide", "skill_block_patch")

    assert package.files["SKILL.md"] == "# Updated Guide\n"
    assert package.entrypoint is None
    assert package.metadata["strategy"] == "mode_a_update"
    assert package.metadata["targeted_rubrics"] == ["r1", "r3"]
    assert package.metadata["changed_files"] == ["SKILL.md"]
    assert package.metadata["expected_effect"] == "Add result checks."


@pytest.mark.parametrize(
    "raw, content_block",
    [
        ("<package mode=\"full\" strategy=\"seed\"></package>", "missing_block"),
        (
            "<package mode=\"full\" strategy=\"seed\"><file path=\"SKILL.md\" content_block=\"other\" />"
            "<skill_block>x</skill_block></package>",
            "skill_block",
        ),
        (
            "<package mode=\"full\" strategy=\"seed\"><file path=\"SKILL.md\" content_block=\"skill_block\" />"
            "<skill_block>x</package>",
            "skill_block",
        ),
        (
            "<package strategy=\"seed\"><file path=\"SKILL.md\" content_block=\"skill_block\" />"
            "<skill_block>x</skill_block></package>",
            "skill_block",
        ),
    ],
)
def test_parse_markdown_guide_rejects_invalid_structure(raw: str, content_block: str) -> None:
    with pytest.raises(SkillPackageError):
        parse_markdown_guide_output(raw, "markdown_guide", content_block)


def test_seed_llm_repairs_invalid_python_package_once() -> None:
    llm = RepairingPackageLLM()
    skill = generate_seed_skill(TaskSpec("task", "Pull action items from Slack messages."), "reference", llm)
    assert llm.calls == 2
    assert "def run_pipeline():\n" in skill.files["executor.py"]


def test_update_fallback_preserves_existing_skill_body() -> None:
    task = TaskSpec("task", "body")
    skill = generate_seed_skill(
        task,
        "# Initial Skill\n---\nname: demo\n---\n\nOriginal workflow uses /slack/messages.\n",
        None,
    )
    updated = update_skill(task, skill, _receipt(), VerifierScore(skill.key, 1, {"r1": False}, 0, 0.0), None)
    assert "Original workflow uses /slack/messages" in updated.files["SKILL.md"]
    assert updated.key.version == skill.key.version + 1
    assert updated.metadata["fallback_update"] is True
    assert updated.metadata["fallback_update_reason"] == "llm_disabled"


def test_update_llm_failure_is_not_silently_fallbacked() -> None:
    task = TaskSpec("task", "body")
    skill = generate_seed_skill(
        task,
        "# Initial Skill\n---\nname: demo\n---\n\nOriginal workflow uses /slack/messages.\n",
        None,
    )
    with pytest.raises(RuntimeError):
        update_skill(task, skill, _receipt(), VerifierScore(skill.key, 1, {"r1": False}, 0, 0.0), BrokenLLM())


def test_update_skill_skips_llm_when_no_actionable_rubrics() -> None:
    task = TaskSpec("task", "body")
    skill = generate_seed_skill(
        task,
        "# Initial Skill\n---\nname: demo\n---\n\nOriginal workflow uses /slack/messages.\n",
        None,
    )

    updated = update_skill(
        task,
        skill,
        _receipt(),
        VerifierScore(skill.key, 1, {"r1": True}, 1, 1.0),
        BrokenLLM(),
    )

    assert updated.key == skill.key
    assert updated.metadata["mode_a_update_skipped"] is True
    assert updated.metadata["mode_a_update_skip_reason"] == "no_actionable_rubrics"
    assert "mode_a_update_failed" not in updated.metadata


def test_update_llm_patch_package_merges_old_files() -> None:
    task = TaskSpec("task", "body")
    skill = generate_seed_skill(
        task,
        "# Initial Skill\n---\nname: demo\n---\n\nOriginal workflow uses /slack/messages.\n",
        None,
    )
    updated = update_skill(task, skill, _receipt(), VerifierScore(skill.key, 1, {"r1": False}, 0, 0.0), PatchLLM())
    assert updated.key.version == skill.key.version + 1
    assert updated.metadata["llm_update"] is True
    assert "Original workflow uses /slack/messages" in updated.files["SKILL.md"]
    assert "return 'patched'" in updated.files["executor.py"]


def test_update_llm_full_package_replaces_files() -> None:
    task = TaskSpec("task", "body")
    skill = generate_seed_skill(
        task,
        "# Initial Skill\n---\nname: demo\n---\n\nOriginal workflow uses /slack/messages.\n",
        None,
    )
    updated = update_skill(task, skill, _receipt(), VerifierScore(skill.key, 1, {"r1": False}, 0, 0.0), FullPackageLLM())
    assert updated.key.version == skill.key.version + 1
    assert updated.metadata["llm_update"] is True
    assert "return 'full'" in updated.files["executor.py"]
    assert "Original workflow uses /slack/messages" not in updated.files["SKILL.md"]


def test_update_llm_legacy_guidance_triggers_repair() -> None:
    task = TaskSpec("task", "body")
    skill = generate_seed_skill(
        task,
        "# Initial Skill\n---\nname: demo\n---\n\nOriginal workflow uses /slack/messages.\n",
        None,
    )
    llm = RepairingUpdateLLM()
    updated = update_skill(task, skill, _receipt(), VerifierScore(skill.key, 1, {"r1": False}, 0, 0.0), llm)
    assert llm.calls == 2
    assert updated.metadata["llm_update"] is True
    assert "return 'repaired'" in updated.files["executor.py"]


def test_update_llm_missing_target_metadata_triggers_repair() -> None:
    task = TaskSpec("task", "body")
    skill = generate_seed_skill(
        task,
        "# Initial Skill\n---\nname: demo\n---\n\nOriginal workflow uses /slack/messages.\n",
        None,
    )
    llm = MissingTargetRepairLLM()

    updated = update_skill(
        task,
        skill,
        _receipt(),
        VerifierScore(skill.key, 1, {"r1": False}, 0, 0.0),
        llm,
    )

    assert llm.calls == 2
    assert updated.metadata["targeted_rubrics"] == ["r1"]
    assert "return 'targeted'" in updated.files["executor.py"]
    assert "allowed_target_rubrics" in llm.repair_user
    assert '"rubric_id": "r1"' in llm.repair_user


def test_update_llm_repairs_oracle_metric_target_to_receipt_id() -> None:
    task = TaskSpec("task", "body")
    skill = generate_seed_skill(
        task,
        "# Initial Skill\n---\nname: demo\n---\n\nOriginal workflow counts figures.\n",
        None,
    )
    llm = OracleMetricTargetRepairLLM()

    updated = update_skill(
        task,
        skill,
        _figure_receipt(),
        VerifierScore(skill.key, 1, {"r6": False}, 0, 0.0),
        llm,
    )

    assert llm.calls == 2
    assert updated.metadata["targeted_rubrics"] == ["r6"]
    assert "figure_count_accuracy" in llm.repair_user
    assert '"rubric_id": "r6"' in llm.repair_user
    assert "return 'counted'" in updated.files["executor.py"]


def test_update_llm_rejects_non_actionable_target_after_repair() -> None:
    task = TaskSpec("task", "body")
    skill = generate_seed_skill(
        task,
        "# Initial Skill\n---\nname: demo\n---\n\nOriginal workflow uses /slack/messages.\n",
        None,
    )

    updated = update_skill(
        task,
        skill,
        _two_rubric_receipt(),
        VerifierScore(skill.key, 1, {"r1": False, "r2": True}, 1, 0.5),
        NonActionableTargetLLM(),
    )

    assert updated.key == skill.key
    assert updated.metadata["mode_a_update_failed"] is True


def test_update_llm_rejects_metadata_only_patch_after_repair() -> None:
    task = TaskSpec("task", "body")
    skill = generate_seed_skill(
        task,
        "# Initial Skill\n---\nname: demo\n---\n\nOriginal workflow uses /slack/messages.\n",
        None,
    )

    updated = update_skill(
        task,
        skill,
        _receipt(),
        VerifierScore(skill.key, 1, {"r1": False}, 0, 0.0),
        MetadataOnlyUpdateLLM(),
    )

    assert updated.key == skill.key
    assert updated.metadata["mode_a_update_failed"] is True


def test_update_llm_second_failure_preserves_old_skill_without_guidance_append() -> None:
    task = TaskSpec("task", "body")
    skill = generate_seed_skill(
        task,
        "# Initial Skill\n---\nname: demo\n---\n\nOriginal workflow uses /slack/messages.\n",
        None,
    )
    updated = update_skill(task, skill, _receipt(), VerifierScore(skill.key, 1, {"r1": False}, 0, 0.0), GuidanceLLM())
    assert updated.key == skill.key
    assert updated.metadata["mode_a_update_failed"] is True
    assert "Verify every updated deadline" not in updated.files["SKILL.md"]


def test_successful_update_clears_previous_mode_a_failure_metadata() -> None:
    task = TaskSpec("task", "body")
    skill = generate_seed_skill(
        task,
        "# Initial Skill\n---\nname: demo\n---\n\nOriginal workflow uses /slack/messages.\n",
        None,
    )
    failed = update_skill(
        task,
        skill,
        _receipt(),
        VerifierScore(skill.key, 1, {"r1": False}, 0, 0.0),
        GuidanceLLM(),
    )

    updated = update_skill(
        task,
        failed,
        _receipt(),
        VerifierScore(failed.key, 1, {"r1": False}, 0, 0.0),
        SuccessfulUpdateLLM(),
    )

    assert updated.metadata["llm_update"] is True
    assert "fallback_update" not in updated.metadata
    assert "fallback_update_reason" not in updated.metadata
    assert "mode_a_update_failed" not in updated.metadata


def test_fallback_update_replaces_guidance_block_instead_of_repeating() -> None:
    task = TaskSpec("task", "body")
    skill = generate_seed_skill(
        task,
        "# Initial Skill\n---\nname: demo\n---\n\nOriginal workflow uses /slack/messages.\n",
        None,
    )
    first = update_skill(task, skill, _receipt(), VerifierScore(skill.key, 1, {"r1": False}, 0, 0.0), None)
    second = update_skill(task, first, _receipt(), VerifierScore(first.key, 1, {"r1": False}, 0, 0.0), None)
    assert second.files["SKILL.md"].count("## CoEvo Learned Guidance") == 1
    assert second.files["SKILL.md"].count("Always produce the final report") == 1


def test_agent_skill_fallback_group_uses_one_stable_runtime_name() -> None:
    task = TaskSpec("media-content-production", "media tasks")
    config = SkillLiftConfig(skill_count=4, skill_format="agent_skill", use_llm=False)

    skills = initialize_skill_group(task, config, "", None)

    assert len(skills) == 4
    assert {skill.skill_name for skill in skills.values()} == {"skilllift-media-content-production"}
    assert all(skill.entrypoint is None for skill in skills.values())
    assert all("name: skilllift-media-content-production" in skill.files["SKILL.md"] for skill in skills.values())
