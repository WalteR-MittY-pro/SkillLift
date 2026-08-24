from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import SkillPackageError
from .schemas import SkillVersion

MAX_FILE_BYTES = 250_000
MAX_PACKAGE_BYTES = 1_000_000


def parse_skill_payload(
    payload: dict[str, Any],
    *,
    version: int,
    base: SkillVersion | None = None,
) -> SkillVersion:
    mode = str(payload.get("package_mode") or "full").strip().lower()
    if mode not in {"full", "patch"}:
        raise SkillPackageError("package_mode must be full or patch")
    if mode == "patch" and base is None:
        raise SkillPackageError("patch package requires a base skill")

    files = dict(base.files) if base is not None and mode == "patch" else {}
    raw_files = payload.get("files")
    if isinstance(raw_files, dict):
        items = [{"path": key, "content": value} for key, value in raw_files.items()]
    elif isinstance(raw_files, list):
        items = raw_files
    else:
        raise SkillPackageError("files must be an object or an array")

    for item in items:
        if not isinstance(item, dict):
            raise SkillPackageError("each file must be an object")
        rel_path = safe_relative_path(str(item.get("path") or ""))
        content = item.get("content")
        if not isinstance(content, str):
            raise SkillPackageError(f"{rel_path}: content must be text")
        files[rel_path] = _ensure_newline(content)

    for raw_path in payload.get("delete_files") or []:
        files.pop(safe_relative_path(str(raw_path)), None)

    name = str(payload.get("skill_name") or (base.name if base else "evolved-skill")).strip()
    if not name:
        raise SkillPackageError("skill_name must not be empty")
    raw_entrypoint = payload.get("entrypoint", base.entrypoint if base else None)
    entrypoint = safe_relative_path(str(raw_entrypoint)) if raw_entrypoint else None
    skill = SkillVersion(
        version=version,
        name=name,
        files=files,
        entrypoint=entrypoint,
        metadata=dict(payload.get("metadata") or {}),
    )
    validate_skill(skill)
    return skill


def validate_skill(skill: SkillVersion) -> None:
    if "SKILL.md" not in skill.files:
        raise SkillPackageError("skill package must contain SKILL.md")
    if len(skill.files) < 2:
        raise SkillPackageError("skill package must contain SKILL.md and at least one supporting file")
    total = 0
    for raw_path, content in skill.files.items():
        rel_path = safe_relative_path(raw_path)
        if not isinstance(content, str):
            raise SkillPackageError(f"{rel_path}: content must be text")
        size = len(content.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            raise SkillPackageError(f"{rel_path}: file exceeds {MAX_FILE_BYTES} bytes")
        total += size
        if rel_path.endswith(".py"):
            try:
                ast.parse(content, filename=rel_path)
            except SyntaxError as exc:
                raise SkillPackageError(f"{rel_path}: invalid Python: {exc}") from exc
    if total > MAX_PACKAGE_BYTES:
        raise SkillPackageError(f"skill package exceeds {MAX_PACKAGE_BYTES} bytes")
    if skill.entrypoint and skill.entrypoint not in skill.files:
        raise SkillPackageError(f"entrypoint not found: {skill.entrypoint}")


def materialize_skill(skill: SkillVersion, target: Path) -> Path:
    validate_skill(skill)
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()
    for rel_path, content in skill.files.items():
        destination = (target / safe_relative_path(rel_path)).resolve()
        if root not in destination.parents:
            raise SkillPackageError(f"file escapes target: {rel_path}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(_ensure_newline(content), encoding="utf-8")
    return target


def skill_hash(skill: SkillVersion) -> str:
    payload = {
        "name": skill.name,
        "files": {key: skill.files[key] for key in sorted(skill.files)},
        "entrypoint": skill.entrypoint,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def safe_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise SkillPackageError(f"unsafe relative path: {value}")
    if any(not part for part in path.parts):
        raise SkillPackageError(f"unsafe relative path: {value}")
    return path.as_posix()


def _ensure_newline(value: str) -> str:
    return value if value.endswith("\n") else value + "\n"
