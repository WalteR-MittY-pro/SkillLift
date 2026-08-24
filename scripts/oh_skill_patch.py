"""Monkey-patch OpenHands CLI to inject /skills/*/SKILL.md into the agent context.

Problem
-------
OpenHands ACP mode (LocalOpenHandsACPAgent._setup_conversation in
local_agent.py) hard-codes ``skills=[RESOURCE_SKILL]`` when calling
``load_agent_specs``. It never reads the ``/skills`` directory that benchflow
symlinks into the container, so custom skills are invisible to the agent.

Fix
---
This module patches ``openhands_cli.setup.load_agent_specs`` so that every call
transparently prepends any ``Skill`` objects discovered under ``/skills``.
Loaded skills are cached per-process. It is installed as a ``.pth`` entry so the
Python interpreter auto-imports it at startup, before ``run_acp_server`` runs.

Trigger handling: SKILL.md files with no ``trigger`` front-matter (or
``trigger: null``) become ``trigger=None`` skills = always active, injected into
the system prompt ``<REPO_CONTEXT>`` every step — exactly the behaviour the
AgentSkills standard calls "Repository Skills".
"""

from __future__ import annotations

import logging
import os
import hashlib
import json
import stat
from pathlib import Path

logger = logging.getLogger("oh_skill_patch")

SKILLS_ROOT = Path(os.environ.get("OH_SKILLS_ROOT", "/skills"))
RUNTIME_SKILLS_ROOT = Path(os.environ.get("OH_RUNTIME_SKILLS_ROOT", "/skills"))
RECEIPT_ROOT = Path(os.environ.get("OH_SKILLS_RECEIPT_ROOT", str(SKILLS_ROOT / ".skilllift-receipts")))
_PATCHED = False
_CACHED_SKILLS: list | None = None
_CACHED_SKILL_RECEIPT: list[dict] | None = None


def _load_custom_skills() -> list:
    """Return Skill objects for every SKILL.md found under SKILLS_ROOT."""
    global _CACHED_SKILLS, _CACHED_SKILL_RECEIPT
    if _CACHED_SKILLS is not None:
        return _CACHED_SKILLS

    skills: list = []
    if not SKILLS_ROOT.is_dir():
        logger.info("oh_skill_patch: %s is not a directory; no skills loaded", SKILLS_ROOT)
        _CACHED_SKILLS = skills
        return skills

    try:
        from openhands.sdk.context import Skill
    except Exception as exc:  # pragma: no cover - SDK must be installed
        logger.warning("oh_skill_patch: cannot import openhands.sdk.context.Skill: %s", exc)
        _CACHED_SKILLS = skills
        return skills

    # Two layouts supported:
    #   /skills/<name>/SKILL.md            (AgentSkills standard)
    #   /skills/<name>.md                  (flat)
    md_files = sorted(
        list(SKILLS_ROOT.rglob("SKILL.md"))
        + [path for path in SKILLS_ROOT.glob("*.md") if _is_flat_skill(path)]
    )
    seen: set[Path] = set()
    seen_names: set[str] = set()
    loaded_receipt: list[dict] = []
    for md in md_files:
        md = md.resolve()
        if md in seen:
            continue
        seen.add(md)
        try:
            content = md.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("oh_skill_patch: cannot read %s: %s", md, exc)
            continue

        name = md.parent.name if md.name == "SKILL.md" else md.stem
        if name in seen_names:
            raise RuntimeError(f"oh_skill_patch: duplicate canonical skill name: {name}")
        seen_names.add(name)
        runtime_root = RUNTIME_SKILLS_ROOT / name
        runtime_note = (
            f"[Runtime Skill Root]\nThis skill's scripts and references are available under `{runtime_root}`. "
            "Resolve relative file paths from that directory.\n\n"
        )
        # trigger=None => always active (Repository Skill semantics)
        injected = runtime_note + content
        skills.append(Skill(name=name, content=injected, trigger=None))
        loaded_receipt.append(
            {
                "name": name,
                "skill_md_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "source_chars": len(content),
                "injected_chars": len(injected),
            }
        )
        logger.info("oh_skill_patch: loaded skill %r from %s (%d chars)", name, md, len(content))

    logger.info("oh_skill_patch: loaded %d skill(s) from %s", len(skills), SKILLS_ROOT)
    _write_receipt("deployed_tree_receipt.json", _deployed_tree_receipt(md_files))
    _CACHED_SKILL_RECEIPT = loaded_receipt
    _CACHED_SKILLS = skills
    return skills


def _is_flat_skill(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        return False
    frontmatter = text.split("---", 2)[1]
    return any(line.strip().startswith("name:") for line in frontmatter.splitlines())


def _deployed_tree_receipt(skill_md_files: list[Path]) -> dict:
    del skill_md_files
    tracked = {
        path.resolve()
        for path in SKILLS_ROOT.rglob("*")
        if path.is_file()
        and path.name != "oh_skill_patch.py"
        and ".skilllift-receipts" not in path.relative_to(SKILLS_ROOT).parts
    }
    rows = []
    digest = hashlib.sha256()
    for path in sorted(tracked, key=lambda item: item.relative_to(SKILLS_ROOT.resolve()).as_posix()):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError(f"oh_skill_patch: non-regular portfolio file: {path}")
        data = path.read_bytes()
        rel = path.relative_to(SKILLS_ROOT.resolve()).as_posix()
        mode = stat.S_IMODE(info.st_mode)
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(f"{mode:o}".encode("ascii"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
        rows.append(
            {
                "path": rel,
                "mode": mode,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return {"schema_version": 1, "tree_hash": digest.hexdigest(), "files": rows}


def _write_receipt(name: str, payload: dict) -> None:
    RECEIPT_ROOT.mkdir(parents=True, exist_ok=True)
    target = RECEIPT_ROOT / name
    temporary = RECEIPT_ROOT / f".{name}.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def apply_patch() -> None:
    """Wrap load_agent_specs so custom skills are merged into every call."""
    global _PATCHED
    if _PATCHED:
        return
    try:
        import openhands_cli.setup as setup_mod
    except Exception as exc:
        logger.warning("oh_skill_patch: openhands_cli.setup not importable: %s", exc)
        return

    original = getattr(setup_mod, "load_agent_specs", None)
    if original is None or getattr(original, "_oh_patched", False):
        _PATCHED = True
        return

    def patched_load_agent_specs(*args, **kwargs):  # type: ignore[override]
        custom = _load_custom_skills()
        existing = list(kwargs.get("skills") or [])
        # Candidate Portfolio skills take precedence over built-in skills with the same name.
        custom_names = {skill.name for skill in custom}
        kwargs["skills"] = [*custom, *(skill for skill in existing if skill.name not in custom_names)]
        logger.info(
            "oh_skill_patch: load_agent_specs called with %d skill(s) (%d custom)",
            len(kwargs["skills"]),
            len(custom),
        )
        result = original(*args, **kwargs)
        _write_receipt(
            "loaded_skill_receipt.json",
            {"schema_version": 1, "skills": list(_CACHED_SKILL_RECEIPT or [])},
        )
        return result

    patched_load_agent_specs._oh_patched = True  # type: ignore[attr-defined]
    setup_mod.load_agent_specs = patched_load_agent_specs
    _PATCHED = True
    logger.info("oh_skill_patch: patch applied to openhands_cli.setup.load_agent_specs")


# Auto-apply on import (the .pth entry does ``import oh_skill_patch``).
try:
    apply_patch()
except Exception as exc:  # pragma: no cover - never break interpreter startup
    logger.warning("oh_skill_patch: failed to apply patch: %s", exc)
