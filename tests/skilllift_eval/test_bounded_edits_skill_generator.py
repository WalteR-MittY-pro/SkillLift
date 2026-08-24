from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
AGENTCLAW_ROOT = ROOT / "skilllift"
BOUNDED_EDITS_SRC = ROOT / "packages" / "bounded-edits" / "src"
for source_root in (AGENTCLAW_ROOT, BOUNDED_EDITS_SRC):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from skilllift.portfolio import (  # noqa: E402
    PortfolioError,
    PortfolioRef,
    apply_portfolio_patch,
    portfolio_tree_hash,
)
from skilllift.portfolio import (  # noqa: E402
    PortfolioContextLimitError,
    PublicTask,
    SearchDirection,
)
from skilllift_eval.runners.bounded_edits_skill_generator import (  # noqa: E402
    BoundedEditsSkillGenerator,
)


def test_structured_generator_produces_existing_skilllift_patch_contract(
    tmp_path: Path,
) -> None:
    parent_root = tmp_path / "parent"
    skill_root = parent_root / "alpha"
    skill_root.mkdir(parents=True)
    skill_md = (
        "---\n"
        "name: alpha\n"
        "description: Use for alpha tasks.\n"
        "triggers:\n"
        "  - alpha\n"
        "---\n"
        "# alpha\n\n"
        "Follow the workflow.\n"
    )
    (skill_root / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (skill_root / "reference.md").write_text("Reference material.\n", encoding="utf-8")
    parent = PortfolioRef.from_directory(
        task_id="task-1",
        root=parent_root,
        config_hash="config-1",
        curated_skill_names=("alpha",),
    )
    parent_hash = portfolio_tree_hash(parent_root)

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def call_text(self, system, user, temperature=0.0, **kwargs):
            self.calls.append((system, user, temperature, kwargs))
            return json.dumps(
                {
                    "schema_version": 1,
                    "edits": [
                        {
                            "file_id": "f0",
                            "op": "insert_after",
                            "start_anchor": "L0009",
                            "end_anchor": None,
                            "content": "\nValidate the final artifact before delivery.",
                        }
                    ],
                }
            )

    client = FakeClient()
    generator = BoundedEditsSkillGenerator(client)
    patch = generator.generate(
        PublicTask("task-1", "Improve final-output reliability."),
        SearchDirection(
            direction_id="d1",
            rubric_id="r1",
            hypothesis="A validation step prevents incomplete artifacts.",
            capability="Add a concise validation step.",
            target_scope=("alpha/",),
            cross_skill_rationale=None,
            evidence_refs=("task.md",),
            novelty_key="validation-step",
        ),
        parent,
    )

    result = apply_portfolio_patch(
        parent,
        patch,
        tmp_path / "candidate",
        target_scope=("alpha/",),
    )

    assert result.changed_paths == ("alpha/SKILL.md",)
    assert (
        "Validate the final artifact"
        in (result.portfolio.root / "alpha" / "SKILL.md").read_text()
    )
    assert portfolio_tree_hash(parent_root) == parent_hash
    system, user, temperature, kwargs = client.calls[0]
    assert "file_id" in system
    assert "Improve final-output reliability." in user
    assert kwargs["response_format"]["type"] == "json_schema"
    schema = kwargs["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["edits"]["items"]["anyOf"][0]["properties"]["file_id"][
        "enum"
    ] == ["f0", "f1"]
    assert temperature == 0.0
    assert len(client.calls) == 1


def _large_parent(tmp_path: Path) -> PortfolioRef:
    parent_root = tmp_path / "parent"
    skill_root = parent_root / "alpha"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\n"
        "name: alpha\n"
        "description: Use for alpha tasks.\n"
        "triggers:\n"
        "  - alpha\n"
        "---\n"
        "# alpha\n\n"
        "Follow the workflow.\n",
        encoding="utf-8",
    )
    for index in range(21):
        (skill_root / f"reference-{index:02d}.md").write_text(
            f"REFERENCE_{index:02d}\n",
            encoding="utf-8",
        )
    return PortfolioRef.from_directory(
        task_id="task-1",
        root=parent_root,
        config_hash="config-1",
        curated_skill_names=("alpha",),
    )


def _direction(scope: str = "alpha/") -> SearchDirection:
    return SearchDirection(
        direction_id="d1",
        rubric_id="r1",
        hypothesis="A validation step prevents incomplete artifacts.",
        capability="Add a concise validation step.",
        target_scope=(scope,),
        cross_skill_rationale=None,
        evidence_refs=("task.md",),
        novelty_key="validation-step",
    )


def _insert_after_response(anchor: str) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "edits": [
                {
                    "file_id": "f0",
                    "op": "insert_after",
                    "start_anchor": anchor,
                    "end_anchor": None,
                    "content": "\nValidate the final artifact before delivery.",
                }
            ],
        }
    )


def _overlapping_response() -> str:
    response = json.loads(_insert_after_response("Follow the workflow."))
    response["edits"].append(dict(response["edits"][0]))
    return json.dumps(response)


@pytest.mark.parametrize(
    ("invalid_response", "error_code", "edit_index"),
    (
        (
            _insert_after_response("Paraphrased workflow instruction."),
            "anchor_not_found",
            0,
        ),
        (_overlapping_response(), "overlapping_edits", 1),
    ),
    ids=("anchor-not-found", "overlapping-edits"),
)
def test_editor_repairs_response_failure_once(
    tmp_path: Path,
    invalid_response: str,
    error_code: str,
    edit_index: int,
) -> None:
    parent = _large_parent(tmp_path)

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def call_text(self, system, user, temperature=0.0, **kwargs):
            self.calls.append((system, user, temperature, kwargs))
            return (
                invalid_response
                if len(self.calls) == 1
                else _insert_after_response("Follow the workflow.")
            )

    client = FakeClient()
    patch = BoundedEditsSkillGenerator(client).generate(
        PublicTask("task-1", "Improve final-output reliability."),
        _direction("alpha/SKILL.md"),
        parent,
    )

    assert "Validate the final artifact before delivery." in patch
    assert len(client.calls) == 2
    first_system, first_user, _, first_kwargs = client.calls[0]
    second_system, second_user, second_temperature, second_kwargs = client.calls[1]
    assert second_system == first_system
    assert first_user not in second_user
    assert "[Failed File: alpha/SKILL.md]" in second_user
    assert "L0001 | ---" in second_user
    assert f'"code":"{error_code}"' in second_user
    assert f'"edit_index":{edit_index}' in second_user
    assert '"file_id":"f0"' in second_user
    assert "multiple edits are valid when their ranges do not overlap" in second_user
    assert json.dumps(invalid_response, ensure_ascii=False) in second_user
    assert second_temperature == 0.0
    assert second_kwargs == first_kwargs


def test_editor_repairs_invalid_json_transport_once(tmp_path: Path) -> None:
    parent = _large_parent(tmp_path)

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def call_text(self, system, user, temperature=0.0, **kwargs):
            self.calls.append((system, user, temperature, kwargs))
            return (
                "not valid JSON"
                if len(self.calls) == 1
                else _insert_after_response("Follow the workflow.")
            )

    client = FakeClient()
    patch = BoundedEditsSkillGenerator(client).generate(
        PublicTask("task-1", "Improve final-output reliability."),
        _direction("alpha/SKILL.md"),
        parent,
    )

    assert "Validate the final artifact before delivery." in patch
    assert len(client.calls) == 2
    assert '"code":"invalid_json_transport"' in client.calls[1][1]


def test_editor_rejects_candidate_after_failed_repair(tmp_path: Path) -> None:
    parent = _large_parent(tmp_path)

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def call_text(self, system, user, temperature=0.0, **kwargs):
            self.calls.append((system, user, temperature, kwargs))
            return _insert_after_response("Paraphrased workflow instruction.")

    client = FakeClient()
    with pytest.raises(PortfolioError, match="anchor_not_found"):
        BoundedEditsSkillGenerator(client).generate(
            PublicTask("task-1", "Improve final-output reliability."),
            _direction("alpha/SKILL.md"),
            parent,
        )

    assert len(client.calls) == 3


def test_editor_retries_a_second_repair_after_overlapping_edits(tmp_path: Path) -> None:
    parent = _large_parent(tmp_path)

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def call_text(self, system, user, temperature=0.0, **kwargs):
            self.calls.append((system, user, temperature, kwargs))
            if len(self.calls) < 3:
                return _overlapping_response()
            return _insert_after_response("Follow the workflow.")

    client = FakeClient()
    patch = BoundedEditsSkillGenerator(client).generate(
        PublicTask("task-1", "Improve final-output reliability."),
        _direction("alpha/SKILL.md"),
        parent,
    )

    assert "Validate the final artifact before delivery." in patch
    assert len(client.calls) == 3
    assert '"code":"overlapping_edits"' in client.calls[2][1]


def test_deepseek_uses_prompt_json_for_selector_editor_and_repair(
    tmp_path: Path,
) -> None:
    parent = _large_parent(tmp_path)

    class FakeClient:
        provider = "deepseek"
        model = "deepseek-v4-pro"

        def __init__(self) -> None:
            self.calls = []

        def call_text(self, system, user, temperature=0.0, **kwargs):
            self.calls.append((system, user, temperature, kwargs))
            if len(self.calls) == 1:
                return "Selected files:\n{file_ids: [],}\nDone."
            if len(self.calls) == 2:
                return (
                    "Proposed edit:\n"
                    + _insert_after_response("Paraphrased workflow instruction.")
                    + "\nEnd."
                )
            return (
                "Corrected edit:\n"
                + _insert_after_response("Follow the workflow.")
                + "\nEnd."
            )

    client = FakeClient()
    patch = BoundedEditsSkillGenerator(client, max_input_chars=120_000).generate(
        PublicTask("task-1", "Improve final-output reliability."),
        _direction(),
        parent,
    )

    assert "Validate the final artifact before delivery." in patch
    assert len(client.calls) == 3
    assert all(call[3]["response_format"] is None for call in client.calls)


def test_deepseek_normalizes_single_edit_object_from_live_shape(
    tmp_path: Path,
) -> None:
    parent = _large_parent(tmp_path)

    class FakeClient:
        provider = "deepseek"
        model = "deepseek-v4-pro"

        def __init__(self) -> None:
            self.calls = []

        def call_text(self, system, user, temperature=0.0, **kwargs):
            self.calls.append((system, user, temperature, kwargs))
            return json.dumps(
                {
                    "file_id": "f0",
                    "operation": "append",
                    "start_anchor": None,
                    "end_anchor": None,
                    "content": "2. Verify the artifact is complete.\n",
                }
            )

    client = FakeClient()
    patch = BoundedEditsSkillGenerator(client).generate(
        PublicTask("task-1", "Add a final artifact verification step."),
        _direction("alpha/SKILL.md"),
        parent,
    )

    assert "Verify the artifact is complete." in patch
    assert len(client.calls) == 1
    assert client.calls[0][3]["response_format"] is None


def test_editor_does_not_retry_source_drift(tmp_path: Path) -> None:
    parent = _large_parent(tmp_path)
    skill_md = parent.root / "alpha" / "SKILL.md"

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def call_text(self, system, user, temperature=0.0, **kwargs):
            self.calls += 1
            skill_md.write_text(skill_md.read_text() + "Drift.\n", encoding="utf-8")
            return _insert_after_response("Follow the workflow.")

    client = FakeClient()
    with pytest.raises(PortfolioError, match="source_changed"):
        BoundedEditsSkillGenerator(client).generate(
            PublicTask("task-1", "Improve final-output reliability."),
            _direction("alpha/SKILL.md"),
            parent,
        )

    assert client.calls == 1


def test_large_scope_selects_files_before_bounded_edit(tmp_path: Path) -> None:
    parent = _large_parent(tmp_path)

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def call_text(self, system, user, temperature=0.0, **kwargs):
            self.calls.append((system, user, temperature, kwargs))
            if len(self.calls) == 1:
                return json.dumps({"file_ids": ["s1"]})
            return json.dumps(
                {
                    "schema_version": 1,
                    "edits": [
                        {
                            "file_id": "f1",
                            "op": "append",
                            "start_anchor": None,
                            "end_anchor": None,
                            "content": "Validate the selected reference.\n",
                        }
                    ],
                }
            )

    client = FakeClient()
    patch = BoundedEditsSkillGenerator(client, max_input_chars=120_000).generate(
        PublicTask("task-1", "Improve final-output reliability."),
        _direction(),
        parent,
    )

    result = apply_portfolio_patch(
        parent,
        patch,
        tmp_path / "candidate",
        target_scope=("alpha/",),
    )
    assert result.changed_paths == ("alpha/reference-00.md",)
    assert len(client.calls) == 2
    selection_system, selection_user, _, selection_kwargs = client.calls[0]
    assert "Select the smallest set" in selection_system
    assert "REFERENCE_00" not in selection_user
    assert '"editor_input_char_limit":120000' in selection_user
    assert '"optional_file_bytes_budget":' in selection_user
    assert selection_kwargs["response_format"]["json_schema"]["name"] == "bounded_edit_file_selection"
    _, edit_user, _, _ = client.calls[1]
    assert "REFERENCE_00" in edit_user
    assert "REFERENCE_01" not in edit_user


def test_context_overflow_selects_from_small_scope(tmp_path: Path) -> None:
    parent_root = tmp_path / "parent"
    skill_root = parent_root / "alpha"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Use for alpha tasks.\ntriggers:\n  - alpha\n---\n"
        "# alpha\n\nFollow the workflow.\n",
        encoding="utf-8",
    )
    (skill_root / "large-reference.md").write_text("LARGE_REFERENCE\n" * 2000, encoding="utf-8")
    parent = PortfolioRef.from_directory(
        task_id="task-1",
        root=parent_root,
        config_hash="config-1",
        curated_skill_names=("alpha",),
    )

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def call_text(self, system, user, temperature=0.0, **kwargs):
            self.calls.append((system, user, temperature, kwargs))
            if len(self.calls) == 1:
                return json.dumps({"file_ids": []})
            return json.dumps(
                {
                    "schema_version": 1,
                    "edits": [
                        {
                            "file_id": "f0",
                            "op": "append",
                            "start_anchor": None,
                            "end_anchor": None,
                            "content": "Validate the final artifact.\n",
                        }
                    ],
                }
            )

    client = FakeClient()
    BoundedEditsSkillGenerator(client, max_input_chars=8_000).generate(
        PublicTask("task-1", "Improve final-output reliability."),
        _direction(),
        parent,
    )

    assert len(client.calls) == 2
    assert "LARGE_REFERENCE" not in client.calls[0][1]
    assert "LARGE_REFERENCE" not in client.calls[1][1]


def test_selected_files_must_fit_context_budget(tmp_path: Path) -> None:
    parent_root = tmp_path / "parent"
    skill_root = parent_root / "alpha"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Use for alpha tasks.\ntriggers:\n  - alpha\n---\n"
        "# alpha\n\nFollow the workflow.\n",
        encoding="utf-8",
    )
    (skill_root / "large-reference.md").write_text("LARGE_REFERENCE\n" * 2000, encoding="utf-8")
    parent = PortfolioRef.from_directory(
        task_id="task-1",
        root=parent_root,
        config_hash="config-1",
        curated_skill_names=("alpha",),
    )

    class FakeClient:
        def call_text(self, system, user, temperature=0.0, **kwargs):
            return json.dumps({"file_ids": ["s1"]})

    with pytest.raises(PortfolioContextLimitError, match="selected"):
        BoundedEditsSkillGenerator(FakeClient(), max_input_chars=8_000).generate(
            PublicTask("task-1", "Improve final-output reliability."),
            _direction(),
            parent,
        )


def test_mandatory_only_overflow_skips_file_selector(tmp_path: Path) -> None:
    parent_root = tmp_path / "parent"
    skill_root = parent_root / "alpha"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Use for alpha tasks.\ntriggers:\n  - alpha\n---\n",
        encoding="utf-8",
    )
    (skill_root / "large-reference.md").write_text(
        "LARGE_REFERENCE\n" * 2000,
        encoding="utf-8",
    )
    parent = PortfolioRef.from_directory(
        task_id="task-1",
        root=parent_root,
        config_hash="config-1",
        curated_skill_names=("alpha",),
    )

    class FakeClient:
        def call_text(self, *args, **kwargs):
            raise AssertionError("LLM must not be called")

    with pytest.raises(PortfolioContextLimitError, match="selected"):
        BoundedEditsSkillGenerator(FakeClient(), max_input_chars=8_000).generate(
            PublicTask("task-1", "Improve final-output reliability."),
            _direction("alpha/large-reference.md"),
            parent,
        )


@pytest.mark.parametrize("invalid_file", ["too-large", "mixed-newlines"])
def test_file_selector_excludes_snapshot_incompatible_optional_files(
    tmp_path: Path,
    invalid_file: str,
) -> None:
    parent_root = tmp_path / "parent"
    skill_root = parent_root / "alpha"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha.\ntriggers: [alpha]\n---\n# Alpha\n",
        encoding="utf-8",
    )
    invalid_path = skill_root / "invalid-reference.md"
    if invalid_file == "too-large":
        invalid_path.write_text("x" * 2_000_001, encoding="utf-8")
    else:
        invalid_path.write_bytes(b"first\r\nsecond\n")
    (skill_root / "useful-reference.md").write_text(
        "Useful reference.\n",
        encoding="utf-8",
    )
    parent = PortfolioRef.from_directory(
        task_id="task-1",
        root=parent_root,
        config_hash="config-1",
        curated_skill_names=("alpha",),
    )

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []
            self.selector_schema = None

        def call_text(self, system, user, temperature=0.0, **kwargs):
            self.calls.append((system, user, temperature, kwargs))
            if len(self.calls) == 1:
                self.selector_schema = kwargs["response_format"]["json_schema"]["schema"]
                return json.dumps({"file_ids": []})
            return json.dumps(
                {
                    "schema_version": 1,
                    "edits": [
                        {
                            "file_id": "f0",
                            "op": "append",
                            "start_anchor": None,
                            "end_anchor": None,
                            "content": "Validate the result.\n",
                        }
                    ],
                }
            )

    client = FakeClient()
    patch = BoundedEditsSkillGenerator(client, max_input_chars=120_000).generate(
        PublicTask("task-1", "Improve final-output reliability."),
        _direction(),
        parent,
    )

    result = apply_portfolio_patch(
        parent,
        patch,
        tmp_path / "candidate",
        target_scope=("alpha/",),
    )
    assert result.changed_paths == ("alpha/SKILL.md",)
    assert len(client.calls) == 2
    assert client.selector_schema["properties"]["file_ids"]["items"]["enum"] == [
        "s2"
    ]
    assert '"path":"alpha/invalid-reference.md","selectable":false' in client.calls[0][1]
    assert "invalid-reference.md" not in client.calls[1][1]


def test_file_selector_prompt_must_fit_context_budget(tmp_path: Path) -> None:
    parent = _large_parent(tmp_path)

    class FakeClient:
        def call_text(self, *args, **kwargs):
            raise AssertionError("LLM must not be called")

    with pytest.raises(PortfolioContextLimitError, match="selector"):
        BoundedEditsSkillGenerator(FakeClient(), max_input_chars=500).generate(
            PublicTask("task-1", "Improve final-output reliability."),
            _direction(),
            parent,
        )


@pytest.mark.parametrize("file_ids", [["missing"], ["s1", "s1"]])
def test_file_selector_rejects_invalid_ids(tmp_path: Path, file_ids: list[str]) -> None:
    parent = _large_parent(tmp_path)

    class FakeClient:
        def call_text(self, system, user, temperature=0.0, **kwargs):
            return json.dumps({"file_ids": file_ids})

    with pytest.raises(PortfolioError, match="selector"):
        BoundedEditsSkillGenerator(FakeClient()).generate(
            PublicTask("task-1", "Improve final-output reliability."),
            _direction(),
            parent,
        )


def test_nested_scope_selector_requires_a_file(tmp_path: Path) -> None:
    parent_root = tmp_path / "parent"
    skill_root = parent_root / "alpha"
    references = skill_root / "references"
    references.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha.\ntriggers: [alpha]\n---\n# Alpha\n",
        encoding="utf-8",
    )
    for index in range(21):
        (references / f"note-{index:02d}.md").write_text(
            f"NOTE_{index:02d}\n",
            encoding="utf-8",
        )
    parent = PortfolioRef.from_directory(
        task_id="task-1",
        root=parent_root,
        config_hash="config-1",
        curated_skill_names=("alpha",),
    )

    class FakeClient:
        def __init__(self) -> None:
            self.schema = None

        def call_text(self, system, user, temperature=0.0, **kwargs):
            self.schema = kwargs["response_format"]["json_schema"]["schema"]
            return json.dumps({"file_ids": []})

    client = FakeClient()
    with pytest.raises(PortfolioError, match="selected no files"):
        BoundedEditsSkillGenerator(client).generate(
            PublicTask("task-1", "Improve final-output reliability."),
            _direction("alpha/references/"),
            parent,
        )
    assert client.schema["properties"]["file_ids"]["minItems"] == 1


def test_new_skill_scope_remains_unsupported(tmp_path: Path) -> None:
    parent = _large_parent(tmp_path)

    class FakeClient:
        def call_text(self, *args, **kwargs):
            raise AssertionError("LLM must not be called")

    with pytest.raises(PortfolioError, match="existing portfolio skill files"):
        BoundedEditsSkillGenerator(FakeClient()).generate(
            PublicTask("task-1", "Improve final-output reliability."),
            _direction("brand-new-skill/"),
            parent,
        )
