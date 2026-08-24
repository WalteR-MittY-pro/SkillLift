from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from skilllift_eval.runners.wildclaw_engine.docker_utils import AGENT_SKILLS_DIR
from skilllift_eval.schemas import LoadedSkillContext, SkillBundle, stable_hash


LOADER_VERSION = "wildclaw_task_skills_v3"
MANIFEST_SCHEMA_VERSION = "wildclaw_loader_manifest_v3"
CONTAINER_SKILL_DIR = AGENT_SKILLS_DIR
MARKER_FILE = ".skilllift_eval_source.json"


class WildClawBenchSkillLoader:
    def __init__(
        self,
        wildclaw_root: Path,
        artifact_root: Path,
        *,
        copy_or_mount_mode: str = "copy",
        cleanup_policy: str = "manual",
    ) -> None:
        self.wildclaw_root = wildclaw_root.resolve()
        self.artifact_root = artifact_root.resolve()
        self.host_skills_parent_dir = self.artifact_root / "runtime_skills"
        self.copy_or_mount_mode = copy_or_mount_mode
        self.cleanup_policy = cleanup_policy

    def prepare(self, original_task_path: Path, bundle: SkillBundle) -> LoadedSkillContext:
        loaded_skill_names = [_skill_name(skill) for skill in bundle.skills]
        if len(set(loaded_skill_names)) != len(loaded_skill_names):
            raise ValueError("SkillBundle contains duplicate WildClawBench skill names")
        normalized = self.artifact_root / "bundle_portfolio"
        if normalized.exists():
            shutil.rmtree(normalized)
        normalized.mkdir(parents=True)
        for skill, name in zip(bundle.skills, loaded_skill_names):
            skill_dir = normalized / name
            skill_dir.mkdir()
            for rel_path, content in _normalized_skill_files(skill, name).items():
                destination = (skill_dir / rel_path).resolve()
                if skill_dir.resolve() not in destination.parents:
                    raise ValueError(f"skill file escapes package directory: {rel_path}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(_ensure_newline(content), encoding="utf-8")
        return self.prepare_portfolio(
            original_task_path,
            normalized,
            skill_hash=bundle.skill_hash or stable_hash({"skills": bundle.skills}),
            skill_bundle_id=bundle.skill_bundle_id,
        )

    def prepare_portfolio(
        self,
        original_task_path: Path,
        portfolio_root: Path,
        *,
        skill_hash: str,
        skill_bundle_id: str | None = None,
    ) -> LoadedSkillContext:
        portfolio_root = portfolio_root.resolve()
        if not portfolio_root.is_dir():
            raise ValueError(f"WildClaw Portfolio root is not a directory: {portfolio_root}")
        loaded_skill_names = sorted(
            path.name for path in portfolio_root.iterdir() if path.is_dir() and (path / "SKILL.md").is_file()
        )
        if len(set(loaded_skill_names)) != len(loaded_skill_names):
            raise ValueError("WildClaw Portfolio contains duplicate skill names")
        if self.host_skills_parent_dir.exists():
            shutil.rmtree(self.host_skills_parent_dir)
        shutil.copytree(portfolio_root, self.host_skills_parent_dir, copy_function=shutil.copy2)
        generated_task_path = self._write_generated_task(original_task_path, loaded_skill_names)
        runtime_skill_dirs = [str((self.host_skills_parent_dir / name).resolve()) for name in loaded_skill_names]
        loaded_files = sorted(
            path.relative_to(self.host_skills_parent_dir).as_posix()
            for path in self.host_skills_parent_dir.rglob("*")
            if path.is_file()
        )
        manifest_path = self._write_manifest(
            original_task_path=original_task_path,
            generated_task_path=generated_task_path,
            skill_hash=skill_hash,
            skill_bundle_id=skill_bundle_id,
            loaded_skill_names=loaded_skill_names,
            runtime_skill_dirs=runtime_skill_dirs,
            loaded_files=loaded_files,
            kind="wildclaw_file_context",
        )
        return LoadedSkillContext(
            kind="wildclaw_file_context",
            benchmark="wildclawbench",
            loader_type="wildclaw_task_skills",
            skill_bundle_id=skill_bundle_id,
            skill_hash=skill_hash,
            original_task_path=str(original_task_path.resolve()),
            generated_task_path=str(generated_task_path),
            host_skills_parent_dir=str(self.host_skills_parent_dir.resolve()),
            loaded_skill_names=loaded_skill_names,
            runtime_skill_dirs=runtime_skill_dirs,
            container_skill_dir=CONTAINER_SKILL_DIR,
            loaded_files=loaded_files,
            copy_or_mount_mode=self.copy_or_mount_mode,
            manifest_path=str(manifest_path),
            extra_run_args=["--task", str(generated_task_path)],
            extra_env={},
        )

    def prepare_empty(self, original_task_path: Path) -> LoadedSkillContext:
        if self.host_skills_parent_dir.exists():
            shutil.rmtree(self.host_skills_parent_dir)
        self.host_skills_parent_dir.mkdir(parents=True)
        generated_task_path = self._write_generated_task(original_task_path, [])
        skill_hash = stable_hash({"skills": []})
        manifest_path = self._write_manifest(
            original_task_path=original_task_path,
            generated_task_path=generated_task_path,
            skill_hash=skill_hash,
            skill_bundle_id=None,
            loaded_skill_names=[],
            runtime_skill_dirs=[],
            loaded_files=[],
            kind="empty_context",
        )
        return LoadedSkillContext(
            kind="empty_context",
            benchmark="wildclawbench",
            loader_type="empty",
            skill_hash=skill_hash,
            manifest_path=str(manifest_path),
            original_task_path=str(original_task_path.resolve()),
            generated_task_path=str(generated_task_path),
            host_skills_parent_dir=str(self.host_skills_parent_dir),
            loaded_skill_names=[],
            runtime_skill_dirs=[],
            container_skill_dir=CONTAINER_SKILL_DIR,
            loaded_files=[],
            copy_or_mount_mode=self.copy_or_mount_mode,
            extra_run_args=["--task", str(generated_task_path)],
            extra_env={},
        )

    def _write_generated_task(self, original_task_path: Path, skill_names: list[str]) -> Path:
        original_task_path = original_task_path.resolve()
        body = original_task_path.read_text(encoding="utf-8")
        skills_text = "\n".join(skill_names)
        replacement = f"## Skills\n\n{skills_text}\n"
        if re.search(r"^##\s+Skills\s*$", body, flags=re.MULTILINE):
            generated = re.sub(
                r"^##\s+Skills\s*$.*?(?=^##\s+|\Z)",
                replacement,
                body,
                count=1,
                flags=re.MULTILINE | re.DOTALL,
            )
        else:
            generated = body.rstrip() + "\n\n" + replacement

        # Add skill reading instructions to the Prompt section (inline, not as new section)
        if skill_names:
            skill_instructions = self._build_skill_instructions_inline(skill_names)
            generated = self._inject_skill_instructions_inline(generated, skill_instructions)

        task_dir = self.artifact_root / "generated_tasks"
        task_dir.mkdir(parents=True, exist_ok=True)
        generated_task_path = (task_dir / f"{original_task_path.stem}.generated.md").resolve()
        generated_task_path.write_text(generated, encoding="utf-8")
        return generated_task_path

    def _build_skill_instructions_inline(self, skill_names: list[str]) -> str:
        """Build inline instructions telling the agent to read skill documents."""
        if not skill_names:
            return ""
        
        lines = [
            "**IMPORTANT: Before starting, you MUST read and follow the skill documents below:**",
            ""
        ]
        for skill_name in skill_names:
            skill_path = f"{CONTAINER_SKILL_DIR}/{skill_name}/SKILL.md"
            lines.append(f"- Read the skill at `{skill_path}` using the read tool")
            lines.append("- Follow ALL instructions in that skill document")
            lines.append("- The skill contains critical information you must use")
            lines.append("")
        
        lines.append("After reading all skills, proceed with the task as described below.")
        lines.append("")
        return "\n".join(lines)

    def _inject_skill_instructions_inline(self, task_content: str, instructions: str) -> str:
        """Inject skill instructions inline at the beginning of the Prompt section content."""
        if not instructions:
            return task_content
        
        # Find the ## Prompt section and inject instructions right after the header line
        prompt_pattern = r"(^##\s+Prompt\s*\n)"
        match = re.search(prompt_pattern, task_content, flags=re.MULTILINE)
        
        if match:
            # Insert instructions right after ## Prompt header
            insert_pos = match.end()
            return task_content[:insert_pos] + "\n" + instructions + "\n" + task_content[insert_pos:]
        else:
            # If no Prompt section found, add instructions at the beginning
            return instructions + "\n\n" + task_content

    def _write_manifest(
        self,
        *,
        original_task_path: Path,
        generated_task_path: Path,
        skill_hash: str,
        skill_bundle_id: str | None,
        loaded_skill_names: list[str],
        runtime_skill_dirs: list[str],
        loaded_files: list[str],
        kind: str,
    ) -> Path:
        manifest = {
            "loader_version": LOADER_VERSION,
            "loader_manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "kind": kind,
            "benchmark": "wildclawbench",
            "skill_bundle_id": skill_bundle_id,
            "skill_hash": skill_hash,
            "original_task_path": str(original_task_path.resolve()),
            "generated_task_path": str(generated_task_path),
            "host_skills_parent_dir": str(self.host_skills_parent_dir),
            "loaded_skill_names": loaded_skill_names,
            "runtime_skill_dirs": runtime_skill_dirs,
            "container_skill_dir": CONTAINER_SKILL_DIR,
            "loaded_files": loaded_files,
            "copy_or_mount_mode": self.copy_or_mount_mode,
            "cleanup_policy": self.cleanup_policy,
            "extra_run_args": ["--task", str(generated_task_path)],
        }
        manifest_path = self.artifact_root / "loader_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifest_path.resolve()


def _skill_name(skill: dict[str, Any]) -> str:
    raw = str(skill.get("id") or skill.get("name") or skill.get("title") or "").strip()
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-._")
    if not name:
        raise ValueError("WildClawBench skill requires id, name, or title")
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError(f"invalid WildClawBench skill name: {raw}")
    return name


def _normalized_skill_files(skill: dict[str, Any], name: str) -> dict[str, str]:
    raw_files = skill.get("files")
    if raw_files is None:
        title = str(skill.get("title") or name)
        content = str(skill.get("content") or "")
        return {"SKILL.md": f"# {title}\n\n{content.rstrip()}\n"}
    if not isinstance(raw_files, dict) or not raw_files:
        raise ValueError("multi-file skill entry must provide a non-empty files object")

    files: dict[str, str] = {}
    total_bytes = 0
    for raw_path, raw_content in raw_files.items():
        rel_path = _safe_skill_file_path(str(raw_path))
        if rel_path in files:
            raise ValueError(f"duplicate normalized skill file path: {rel_path}")
        if not isinstance(raw_content, str):
            raise ValueError(f"skill file content must be text: {rel_path}")
        size = len(raw_content.encode("utf-8"))
        if size > 250_000:
            raise ValueError(f"skill file exceeds 250000 bytes: {rel_path}")
        total_bytes += size
        if rel_path.endswith(".py"):
            try:
                ast.parse(raw_content, filename=rel_path)
            except SyntaxError as exc:
                raise ValueError(f"invalid Python skill file {rel_path}: {exc}") from exc
        files[rel_path] = raw_content
    if "SKILL.md" not in files:
        raise ValueError("multi-file skill entry must contain SKILL.md")
    if total_bytes > 1_000_000:
        raise ValueError("skill package exceeds 1000000 bytes")
    entrypoint = skill.get("entrypoint")
    if entrypoint and _safe_skill_file_path(str(entrypoint)) not in files:
        raise ValueError(f"skill entrypoint not found in files: {entrypoint}")
    return files


def _safe_skill_file_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"unsafe skill file path: {value}")
    return path.as_posix()


def _ensure_newline(value: str) -> str:
    return value if value.endswith("\n") else value + "\n"
