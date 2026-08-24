import json

import pytest

from skilllift.errors import SkillPackageError
from skilllift.schemas import EvoSkill, SkillKey, TaskSpec
from skilllift.baselines.skill_package import (
    build_runtime_skill_markdown,
    ensure_openclaw_skill_frontmatter,
    evo_skill_to_runtime_dir,
    merge_skill_patch,
    parse_skill_package,
    validate_skill_package,
    GeneratedSkillPackage,
)


def _payload() -> dict:
    return {
        "files": [
            {"path": "SKILL.md", "content": "---\nname: demo\n---\n"},
            {"path": "executor.py", "content": "```python\nprint('ok')\n```"},
        ],
        "entrypoint": "executor.py",
        "metadata": {"strategy": "test"},
    }


def test_parse_full_package_strips_wrapping_fence() -> None:
    package = parse_skill_package(_payload())
    assert package.files["executor.py"] == "print('ok')\n"
    assert package.entrypoint == "executor.py"


def test_markdown_guide_package_accepts_only_skill_markdown() -> None:
    package = parse_skill_package(
        {
            "files": [{"path": "SKILL.md", "content": "# Guide\n\nCheck preconditions.\n"}],
            "entrypoint": None,
            "metadata": {"strategy": "seed"},
        },
        skill_format="markdown_guide",
    )

    assert package.files == {"SKILL.md": "# Guide\n\nCheck preconditions.\n"}
    assert package.entrypoint is None
    validate_skill_package(
        GeneratedSkillPackage({"SKILL.md": "# Guide\n"}, None),
        skill_format="markdown_guide",
    )


def test_patch_package_merges_base_files() -> None:
    base = parse_skill_package(_payload()).files
    package = merge_skill_patch(
        base,
        {
            "files": [{"path": "helper.py", "content": "VALUE = 1\n"}],
            "delete_files": ["executor.py"],
            "entrypoint": "helper.py",
        },
    )
    assert "executor.py" not in package.files
    assert "helper.py" in package.files


@pytest.mark.parametrize(
    "payload",
    [
        {"files": [{"path": "../x.py", "content": "print(1)"}]},
        {"files": [{"path": "SKILL.md", "content": "x"}]},
        {"files": [{"path": "SKILL.md", "content": "x"}, {"path": "x.py", "content": "```bad```"}]},
    ],
)
def test_invalid_package_rejected(payload) -> None:
    with pytest.raises(SkillPackageError):
        parse_skill_package(payload)


def test_runtime_skill_dir_written_with_manifest(tmp_path) -> None:
    package = parse_skill_package(_payload())
    skill = EvoSkill(SkillKey(1, 2), "demo skill", package.files, package.entrypoint, package.metadata)
    runtime_dir = evo_skill_to_runtime_dir(skill, tmp_path)
    assert runtime_dir.name == "s001_v002_demo_skill"
    assert (runtime_dir / "SKILL.md").exists()
    skill_md = (runtime_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "name: s001_v002_demo_skill" in skill_md
    assert "description:" in skill_md
    assert "Use when" in skill_md
    assert (runtime_dir / "executor.py").exists()
    assert (runtime_dir / "skill_package.json").exists()


def test_markdown_guide_runtime_skill_dir_accepts_skill_markdown_only(tmp_path) -> None:
    skill = EvoSkill(
        SkillKey(1, 2),
        "tau2 guide",
        {"SKILL.md": "# Guide\n\nCheck public preconditions.\n"},
        None,
        {"strategy": "seed"},
    )

    runtime_dir = evo_skill_to_runtime_dir(skill, tmp_path, skill_format="markdown_guide")

    assert runtime_dir.name == "s001_v002_tau2_guide"
    assert sorted(path.name for path in runtime_dir.iterdir()) == ["SKILL.md", "skill_package.json"]
    assert "Check public preconditions." in (runtime_dir / "SKILL.md").read_text(encoding="utf-8")


def test_openclaw_frontmatter_preserves_existing_description() -> None:
    text = ensure_openclaw_skill_frontmatter(
        "---\nname: demo\ndescription: Existing trigger.\n---\nBody\n",
        "runtime_demo",
        "Generated summary",
    )
    assert "name: runtime_demo" in text
    assert "description: Existing trigger." in text
    assert "Generated summary" not in text


def test_openclaw_frontmatter_replaces_blank_description() -> None:
    text = ensure_openclaw_skill_frontmatter(
        "---\nname: demo\ndescription: \"\"\n---\nBody\n",
        "runtime_demo",
        "Slack action item extractor",
    )
    assert "name: runtime_demo" in text
    assert "Slack action item extractor" in text
    assert "Use when" in text


def test_openclaw_frontmatter_replaces_generic_description() -> None:
    text = ensure_openclaw_skill_frontmatter(
        "---\nname: demo\ndescription: Task-specific guidance for demo.\n---\nBody\n",
        "runtime_demo",
        "Slack action item extractor",
    )
    assert "Task-specific guidance for demo" not in text
    assert "Slack action item extractor" in text


def test_runtime_skill_markdown_appends_context() -> None:
    package = parse_skill_package(_payload())
    skill = EvoSkill(SkillKey(0, 1), "demo", package.files, package.entrypoint)
    text = build_runtime_skill_markdown(skill, TaskSpec("task", "description"))
    assert "CoEvo Runtime Context" in text
    assert "s000_v001" in text


def test_agent_skill_accepts_text_auxiliary_files_without_python() -> None:
    package = parse_skill_package(
        {
            "files": [
                {
                    "path": "SKILL.md",
                    "content": "---\nname: skilllift-media-content-production\ndescription: Media workflow.\n---\n# Workflow\n",
                },
                {"path": "references/workflow.md", "content": "# Details\n"},
            ]
        },
        skill_format="agent_skill",
    )

    assert set(package.files) == {"SKILL.md", "references/workflow.md"}


def test_agent_skill_rejects_duplicate_paths() -> None:
    with pytest.raises(SkillPackageError, match="duplicate file path"):
        parse_skill_package(
            {
                "files": [
                    {
                        "path": "SKILL.md",
                        "content": "---\nname: skilllift-demo\ndescription: Demo.\n---\n# Demo\n",
                    },
                    {"path": "SKILL.md", "content": "duplicate"},
                ]
            },
            skill_format="agent_skill",
        )


@pytest.mark.parametrize(
    "skill_md",
    [
        "# Missing frontmatter\n",
        "---\nname: Bad_Name\ndescription: Demo.\n---\n# Demo\n",
        "---\nname: skilllift-demo\n---\n# Demo\n",
    ],
)
def test_agent_skill_rejects_invalid_frontmatter(skill_md) -> None:
    with pytest.raises(SkillPackageError):
        parse_skill_package(
            {"files": [{"path": "SKILL.md", "content": skill_md}]},
            skill_format="agent_skill",
        )


def test_agent_skill_runtime_uses_stable_name_and_hashed_manifest(tmp_path) -> None:
    skill = EvoSkill(
        SkillKey(1, 2),
        "skilllift-media-content-production",
        {
            "SKILL.md": "---\nname: skilllift-media-content-production\ndescription: Media workflow.\n---\n# Workflow\n",
            "references/workflow.md": "# Details\n",
        },
    )

    runtime_dir = evo_skill_to_runtime_dir(skill, tmp_path, skill_format="agent_skill")
    manifest = json.loads((runtime_dir / "skill_package.json").read_text(encoding="utf-8"))

    assert runtime_dir.name == "skilllift-media-content-production"
    assert manifest["files"][0].keys() == {"path", "sha256"}
    assert {item["path"] for item in manifest["files"]} == {"SKILL.md", "references/workflow.md"}
