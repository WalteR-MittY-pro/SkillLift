from pathlib import Path

from skilllift.schemas import EvoSkill, SkillKey
from skilllift.task_loader import (
    extract_task_short_id,
    find_repo_skill_markdown,
    load_reference_material,
    load_task_spec,
    rewrite_task_id_for_skill,
)


def _task(path: Path) -> Path:
    path.write_text(
        "---\n"
        "id: 03_Social_Interaction_task_2_chat_action_extraction\n"
        "timeout_seconds: 300\n"
        "---\n"
        "## Prompt\n\nDo this.\n\n## Expected Behavior\n\nKeep me.\n",
        encoding="utf-8",
    )
    return path


def test_load_task_spec_returns_full_body(tmp_path) -> None:
    path = _task(tmp_path / "task.md")
    task = load_task_spec(path)
    assert task.task_name == "03_Social_Interaction_task_2_chat_action_extraction"
    assert "## Expected Behavior" in task.task_description
    assert task.task_doc_path == str(path)


def test_rewrite_task_id_for_skill_only_changes_frontmatter_id(tmp_path) -> None:
    path = _task(tmp_path / "task.md")
    skill = EvoSkill(SkillKey(2, 3), "demo", {"SKILL.md": "x\n", "a.py": "print(1)\n"}, "a.py")
    rewritten = rewrite_task_id_for_skill(path, skill, tmp_path / "out")
    text = rewritten.read_text(encoding="utf-8")
    assert "id: 03_Social_Interaction_task_2_chat_action_extraction__skilllift_s002_v003" in text
    assert "## Prompt\n\nDo this." in text
    assert "## Expected Behavior\n\nKeep me." in text


def test_reference_material_loads_initial_skill(tmp_path) -> None:
    task_path = _task(tmp_path / "task.md")
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("---\nname: demo\n---\nbody\n", encoding="utf-8")
    reference = load_reference_material(load_task_spec(task_path), skill_path)
    assert "# Task Description" in reference
    assert "# Initial Skill" in reference
    assert "name: demo" in reference


def test_numeric_id_with_leading_zeros_is_preserved(tmp_path) -> None:
    path = tmp_path / "task.md"
    path.write_text(
        "---\nid: 00123\ntimeout_seconds: 300\n---\n\n## Prompt\n\nDo this.\n",
        encoding="utf-8",
    )
    task = load_task_spec(path)
    assert task.task_name == "00123"
    skill = EvoSkill(SkillKey(1, 1), "demo", {"SKILL.md": "x\n", "a.py": "print(1)\n"}, "a.py")
    rewritten = rewrite_task_id_for_skill(path, skill, tmp_path / "out")
    assert "id: 00123__skilllift_s001_v001" in rewritten.read_text(encoding="utf-8")


def test_short_id_and_repo_skill_lookup_for_real_task() -> None:
    root = Path(__file__).resolve().parents[2]
    task = load_task_spec(
        root
        / "WildClawBench"
        / "tasks"
        / "03_Social_Interaction"
        / "03_Social_Interaction_task_2_chat_action_extraction.md"
    )
    assert extract_task_short_id(task.task_name) == "03_task2"
    path = find_repo_skill_markdown(task)
    assert path is not None
    assert path.exists()
