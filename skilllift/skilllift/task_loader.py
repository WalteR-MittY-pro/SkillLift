from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from .schemas import EvoSkill, TaskSpec


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def load_task_spec(task_path: Path | str) -> TaskSpec:
    path = Path(task_path)
    metadata, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    task_name = str(metadata.get("id") or path.stem)
    return TaskSpec(
        task_name=task_name,
        task_description=body.strip() + "\n",
        task_doc_path=str(path),
    )


def load_reference_material(task: TaskSpec, initial_skill_path: Path | str | None) -> str:
    parts = ["# Task Description", task.task_description.strip()]
    skill_path = Path(initial_skill_path) if initial_skill_path else find_repo_skill_markdown(task)
    if skill_path and skill_path.exists():
        parts.extend(["# Initial Skill", skill_path.read_text(encoding="utf-8").strip()])
    skills_md = load_skills_md()
    if skills_md:
        parts.extend(["# Global Skills", skills_md.strip()])
    return "\n\n".join(parts).strip() + "\n"


def find_repo_skill_markdown(task: TaskSpec) -> Path | None:
    task_id = task.task_name
    short_id = extract_task_short_id(task_id)
    candidates = [Path("skills") / task_id / "SKILL.md", Path("skills") / short_id / "SKILL.md"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def rewrite_task_id_for_skill(
    task_path: Path | str,
    skill: EvoSkill,
    target_dir: Path | str,
    agent_timeout_override: int = 0,
) -> Path:
    source = Path(task_path)
    target_root = Path(target_dir)
    metadata, body = _split_frontmatter(source.read_text(encoding="utf-8"))
    original_id = str(metadata.get("id") or source.stem)
    metadata["id"] = f"{original_id}__skilllift_s{skill.key.slot:03d}_v{skill.key.version:03d}"
    if agent_timeout_override > 0:
        metadata["timeout_seconds"] = agent_timeout_override
    output = target_root / (
        f"task_s{skill.key.slot:03d}_v{skill.key.version:03d}_{uuid.uuid4().hex[:8]}.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(f"---\n{_dump_frontmatter(metadata)}\n---\n{body}", encoding="utf-8")
    return output


def parse_task_metadata(task_path: Path | str) -> dict[str, Any]:
    path = Path(task_path)
    metadata, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
    return {
        "task_id": metadata.get("id", path.stem),
        "category": path.parent.name,
        "frontmatter_category": metadata.get("category", path.parent.name),
        "timeout_seconds": int(metadata.get("timeout_seconds", 120)),
        "metadata": metadata,
        "file_path": str(path.resolve()),
    }


def parse_simple_frontmatter(raw: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"Unsupported frontmatter line: {line}")
        key, value = line.split(":", 1)
        metadata[key.strip()] = _parse_frontmatter_value(value.strip())
    return metadata


def extract_task_short_id(task_id: str) -> str:
    match = re.match(r"(\d+)_.*?_task_(\d+)", task_id)
    if not match:
        return task_id
    category, task_num = match.groups()
    return f"{category}_task{task_num}"


def parse_markdown_frontmatter(path: Path | str) -> tuple[dict[str, Any], str]:
    return _split_frontmatter(Path(path).read_text(encoding="utf-8"))


def extract_task_description(task_path: Path | str) -> str:
    return load_task_spec(task_path).task_description


def load_skills_md() -> str:
    for path in [Path("skills.md"), Path("docs/skills.md")]:
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""


def _split_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(markdown)
    if not match:
        raise ValueError("YAML frontmatter not found")
    return parse_simple_frontmatter(match.group(1)), match.group(2)


def _dump_frontmatter(metadata: dict[str, Any]) -> str:
    return "\n".join(f"{key}: {_format_frontmatter_value(value)}" for key, value in metadata.items()).strip()


def _parse_frontmatter_value(value: str) -> Any:
    if not value:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if re.fullmatch(r"-?\d+", value):
        digits = value.lstrip("-")
        if len(digits) > 1 and digits.startswith("0"):
            # Preserve leading zeros ("00123"): int() would silently drop
            # them and corrupt task ids on the rewrite round-trip.
            return value
        return int(value)
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return value


def _format_frontmatter_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
