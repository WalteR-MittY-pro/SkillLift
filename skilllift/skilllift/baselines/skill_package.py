from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from ..errors import SkillPackageError
from ..schemas import EvoSkill, TaskSpec

OPENCLAW_TRIGGER_SUFFIX = "Use when this task-specific skill matches the user's request; read SKILL.md before acting."
MAX_DESCRIPTION_CHARS = 500
MAX_AGENT_SKILL_FILES = 12
MAX_AGENT_SKILL_MD_CHARS = 12_000
MAX_AGENT_SKILL_AUX_CHARS = 8_000
MAX_AGENT_SKILL_TOTAL_CHARS = 40_000


@dataclass
class GeneratedSkillPackage:
    files: dict[str, str]
    entrypoint: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_skill_package(
    payload: dict[str, Any],
    base_files: dict[str, str] | None = None,
    skill_format: str = "code_package",
) -> GeneratedSkillPackage:
    if not isinstance(payload, dict):
        raise SkillPackageError("skill package payload must be an object")
    mode = str(payload.get("package_mode", "full")).strip().lower()
    if mode not in {"full", "patch"}:
        raise SkillPackageError("package_mode must be 'full' or 'patch'")
    if mode == "patch" and base_files is None:
        raise SkillPackageError("patch mode requires base_files")
    files = dict(base_files or {}) if mode == "patch" else {}
    seen_paths: set[str] = set()
    for item in payload.get("files", []):
        if not isinstance(item, dict):
            raise SkillPackageError("each files item must be an object")
        path = _safe_rel_path(item.get("path"))
        if path in seen_paths:
            raise SkillPackageError(f"duplicate file path: {path}")
        seen_paths.add(path)
        content = item.get("content")
        if not isinstance(content, str):
            raise SkillPackageError(f"{path}: content must be a string")
        files[path] = _clean_file_content(path, content, skill_format=skill_format)
    for raw_path in payload.get("delete_files", []):
        files.pop(_safe_rel_path(raw_path), None)
    entrypoint = payload.get("entrypoint")
    if entrypoint is not None:
        entrypoint = _safe_rel_path(entrypoint)
        if entrypoint not in files:
            raise SkillPackageError(f"entrypoint not found in files: {entrypoint}")
    package = GeneratedSkillPackage(files=files, entrypoint=entrypoint, metadata=_metadata(payload))
    validate_skill_package(package, skill_format=skill_format)
    return package


def validate_skill_package(package: GeneratedSkillPackage, skill_format: str = "code_package") -> None:
    if "SKILL.md" not in package.files:
        raise SkillPackageError("files must include SKILL.md")
    normalized_format = _normalize_skill_format(skill_format)
    if normalized_format == "markdown_guide":
        extra_files = sorted(path for path in package.files if path != "SKILL.md")
        if extra_files:
            raise SkillPackageError(f"markdown_guide files must only include SKILL.md: {extra_files}")
        if package.entrypoint is not None:
            raise SkillPackageError("markdown_guide package must not include entrypoint")
        for path in package.files:
            _safe_rel_path(path)
        return
    if normalized_format == "agent_skill":
        _validate_agent_skill_package(package)
        return
    python_files = [path for path in package.files if path.endswith(".py")]
    if not python_files:
        raise SkillPackageError("files must include at least one .py file")
    if package.entrypoint is not None and package.entrypoint not in package.files:
        raise SkillPackageError(f"entrypoint not found in files: {package.entrypoint}")
    for path, content in package.files.items():
        _safe_rel_path(path)
        if path.endswith(".py"):
            _validate_python(path, content)


def validate_agent_skill_name(package: GeneratedSkillPackage, expected_name: str) -> None:
    _validate_agent_skill_frontmatter(package.files.get("SKILL.md", ""), expected_name)


def merge_skill_patch(
    base_files: dict[str, str],
    patch_payload: dict[str, Any],
    skill_format: str = "code_package",
) -> GeneratedSkillPackage:
    payload = dict(patch_payload)
    payload["package_mode"] = "patch"
    return parse_skill_package(payload, base_files=base_files, skill_format=skill_format)


def store_skill_package(package: GeneratedSkillPackage, target_dir: Path | str) -> None:
    root = Path(target_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    file_manifest = []
    for rel_path, content in sorted(package.files.items()):
        target = (root / rel_path).resolve()
        if root not in target.parents and target != root:
            raise SkillPackageError(f"path escapes target_dir: {rel_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        stored_content = _ensure_newline(content)
        target.write_text(stored_content, encoding="utf-8")
        file_manifest.append(
            {
                "path": rel_path,
                "sha256": hashlib.sha256(stored_content.encode("utf-8")).hexdigest(),
            }
        )
    manifest = {"entrypoint": package.entrypoint, "metadata": package.metadata, "files": file_manifest}
    (root / "skill_package.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def evo_skill_to_runtime_dir(
    skill: EvoSkill,
    target_dir: Path | str,
    skill_format: str = "code_package",
) -> Path:
    normalized_format = _normalize_skill_format(skill_format)
    runtime_name = (
        skill.skill_name
        if normalized_format == "agent_skill"
        else f"s{skill.key.slot:03d}_v{skill.key.version:03d}_{_safe_name(skill.skill_name)}"
    )
    runtime_dir = Path(target_dir) / runtime_name
    files = dict(skill.files)
    if normalized_format == "agent_skill":
        _validate_agent_skill_frontmatter(files.get("SKILL.md", ""), runtime_name)
    else:
        files["SKILL.md"] = ensure_openclaw_skill_frontmatter(
            files.get("SKILL.md", ""),
            runtime_name,
            _description_hint(skill),
        )
    package = GeneratedSkillPackage(files=files, entrypoint=skill.entrypoint, metadata=skill.metadata)
    validate_skill_package(package, skill_format=skill_format)
    store_skill_package(package, runtime_dir)
    return runtime_dir


def build_runtime_skill_markdown(skill: EvoSkill, task: TaskSpec) -> str:
    existing = skill.files.get("SKILL.md", "").strip()
    context = [
        "## CoEvo Runtime Context",
        "",
        f"- Task: `{task.task_name}`",
        f"- Skill key: `{skill.key.token()}`",
        f"- Entrypoint: `{skill.entrypoint or ''}`",
        "- Use this skill as task-specific guidance and verify required outputs before finishing.",
    ]
    return f"{existing}\n\n{chr(10).join(context)}\n"


def evo_skill_prompt_payload(skill: EvoSkill) -> dict[str, Any]:
    files = {path: _ensure_newline(content) for path, content in sorted(skill.files.items())}
    return {
        "skill_key": skill.key.token(),
        "skill_name": skill.skill_name,
        "skill_md": files.get("SKILL.md", ""),
        "entrypoint": skill.entrypoint,
        "file_manifest": [
            {
                "path": path,
                "chars": len(content),
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
            for path, content in files.items()
        ],
        "aux_files": [
            {"path": path, "content": content}
            for path, content in files.items()
            if path != "SKILL.md"
        ],
    }


def _safe_rel_path(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise SkillPackageError("file path must be a non-empty string")
    normalized = raw.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise SkillPackageError(f"unsafe file path: {raw}")
    return str(path)


def _clean_file_content(path: str, content: str, skill_format: str = "code_package") -> str:
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    if _normalize_skill_format(skill_format) == "code_package" and path.endswith(".py"):
        text = _strip_full_markdown_fence(text)
        if "```" in text:
            raise SkillPackageError(f"{path}: python content contains markdown fence")
        _validate_python(path, text)
    return _ensure_newline(text)


def _normalize_skill_format(skill_format: str) -> str:
    normalized = str(skill_format or "code_package").strip()
    if normalized not in {"code_package", "markdown_guide", "agent_skill"}:
        raise SkillPackageError(f"unsupported skill_format: {skill_format}")
    return normalized


def _validate_agent_skill_package(package: GeneratedSkillPackage) -> None:
    if len(package.files) > MAX_AGENT_SKILL_FILES:
        raise SkillPackageError(f"agent_skill supports at most {MAX_AGENT_SKILL_FILES} files")
    total_chars = sum(len(content) for content in package.files.values())
    if total_chars > MAX_AGENT_SKILL_TOTAL_CHARS:
        raise SkillPackageError(f"agent_skill package exceeds {MAX_AGENT_SKILL_TOTAL_CHARS} characters")
    for path, content in package.files.items():
        _safe_rel_path(path)
        limit = MAX_AGENT_SKILL_MD_CHARS if path == "SKILL.md" else MAX_AGENT_SKILL_AUX_CHARS
        if len(content) > limit:
            raise SkillPackageError(f"{path}: content exceeds {limit} characters")
        if path.endswith(".py"):
            _validate_python(path, content)
    if package.entrypoint is not None and package.entrypoint not in package.files:
        raise SkillPackageError(f"entrypoint not found in files: {package.entrypoint}")
    _validate_agent_skill_frontmatter(package.files["SKILL.md"])


def _validate_agent_skill_frontmatter(markdown: str, expected_name: str | None = None) -> None:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", markdown, re.S)
    if not match:
        raise SkillPackageError("SKILL.md must contain YAML frontmatter")
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line[:1].isspace():
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip("\"'")
    name = fields.get("name", "")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise SkillPackageError("SKILL.md name must use lowercase letters, numbers, and hyphens")
    if expected_name is not None and name != expected_name:
        raise SkillPackageError(f"SKILL.md name {name!r} must match runtime directory {expected_name!r}")
    if not fields.get("description"):
        raise SkillPackageError("SKILL.md frontmatter must include description")


def _strip_full_markdown_fence(text: str) -> str:
    match = re.fullmatch(r"\s*```(?:python|py)?\s*\n(.*?)\n```\s*", text, re.S | re.I)
    return match.group(1) if match else text


def _validate_python(path: str, text: str) -> None:
    if "```" in text:
        raise SkillPackageError(f"{path}: python content contains markdown fence")
    try:
        ast.parse(text)
    except SyntaxError as exc:
        raise SkillPackageError(f"{path}: Python syntax error: {exc}") from exc


def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata", {})
    return metadata if isinstance(metadata, dict) else {"raw_metadata": metadata}


def _ensure_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "skill"


def ensure_openclaw_skill_frontmatter(
    markdown: str,
    name: str,
    description_hint: str | None = None,
) -> str:
    normalized_name = _safe_name(name)
    description = _build_description(description_hint or normalized_name)
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", markdown, re.S)
    if not match:
        return f"---\nname: {normalized_name}\ndescription: {json.dumps(description)}\n---\n\n{markdown.strip()}\n"
    frontmatter, body = match.groups()
    lines = []
    name_replaced = False
    description_replaced = False
    for line in frontmatter.splitlines():
        if line.strip().startswith("name:"):
            lines.append(f"name: {normalized_name}")
            name_replaced = True
            continue
        if line.strip().startswith("description:"):
            value = line.split(":", 1)[1].strip()
            if _description_is_usable(value):
                lines.append(line)
            else:
                lines.append(f"description: {json.dumps(description)}")
            description_replaced = True
        else:
            lines.append(line)
    if not name_replaced:
        lines.insert(0, f"name: {normalized_name}")
    if not description_replaced:
        insert_at = 1 if lines and lines[0].strip().startswith("name:") else len(lines)
        lines.insert(insert_at, f"description: {json.dumps(description)}")
    return f"---\n{chr(10).join(lines).strip()}\n---\n{body.strip()}\n"


def _description_hint(skill: EvoSkill) -> str:
    summary = skill.metadata.get("summary") if isinstance(skill.metadata, dict) else None
    return str(summary).strip() if summary else skill.skill_name


def _build_description(hint: str) -> str:
    cleaned = " ".join(str(hint).replace("_", " ").split()).strip()
    if not cleaned:
        cleaned = "Task-specific OpenClaw skill"
    if "use when" not in cleaned.lower():
        cleaned = f"{cleaned.rstrip('.')}. {OPENCLAW_TRIGGER_SUFFIX}"
    return cleaned[:MAX_DESCRIPTION_CHARS].rstrip()


def _description_is_usable(raw_value: str) -> bool:
    value = raw_value.strip().strip("\"'").lower()
    if not value:
        return False
    generic_fragments = [
        "task-specific guidance for",
        "task specific guidance for",
        "openclaw trigger for when to use",
    ]
    return not any(fragment in value for fragment in generic_fragments)
