from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from skilllift_eval.schemas import LoadedSkillContext, SkillBundle, stable_hash


LOADER_VERSION = "tau2_prompt_context_v1"
MANIFEST_SCHEMA_VERSION = "tau2_loader_manifest_v1"
PROMPT_TEMPLATE_ID = "tau2_skill_injected_prompt_v1"
FORBIDDEN_DOMAIN = "banking_knowledge"
# Patterns that indicate the prompt is leaking tau2's private oracle fields.
# Note: "evaluation_criteria" was removed (2026-07-05). It caused false positives
# because LLM-generated skill text legitimately references the phrase when guiding
# the agent on how to interpret tasks. The real guard against oracle leakage is in
# task_spec construction (which never reads evaluation_criteria), not this regex.
FORBIDDEN_PROMPT_PATTERNS = {
    "gold actions": re.compile(r"\bgold\s+actions?\b", re.IGNORECASE),
    "target DB": re.compile(r"\btarget[_ -]?db\b", re.IGNORECASE),
    "hidden assertions": re.compile(r"\bhidden\s+assertions?\b", re.IGNORECASE),
    "GT resolution steps": re.compile(r"\b(gt|ground\s*truth)[_ -]?resolution[_ -]?steps\b", re.IGNORECASE),
}


class Tau2SkillLoader:
    def __init__(self, artifact_root: Path) -> None:
        self.artifact_root = artifact_root.resolve()

    def prepare(
        self,
        *,
        bundle: SkillBundle,
        domain: str,
        task_ids: list[str],
        domain_policy: str,
    ) -> LoadedSkillContext:
        if domain == FORBIDDEN_DOMAIN:
            raise ValueError("banking_knowledge is not allowed in Phase 2 native task set")
        if not task_ids:
            raise ValueError("Tau2SkillLoader requires at least one task id")

        skills_block = render_skills_block(bundle)
        prompt = render_prompt(domain_policy=domain_policy, skills_block=skills_block)
        _assert_prompt_public(prompt)

        prompt_hash = stable_hash({"prompt_template_id": PROMPT_TEMPLATE_ID, "prompt": prompt})
        skills_block_hash = stable_hash({"skills_block": skills_block})
        self.artifact_root.mkdir(parents=True, exist_ok=True)

        bundle_path = self.artifact_root / "skill_bundle.json"
        bundle_path.write_text(
            json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        payload_path = self.artifact_root / "tau2_prompt_payload.json"
        payload = {
            "prompt_template_id": PROMPT_TEMPLATE_ID,
            "domain": domain,
            "granularity": bundle.granularity,
            "prompt": prompt,
            "skills_block": skills_block,
            "skill_hash": bundle.skill_hash,
            "prompt_hash": prompt_hash,
            "skills_block_hash": skills_block_hash,
        }
        payload_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        manifest_path = self.artifact_root / "loader_manifest.json"
        manifest = {
            "loader_version": LOADER_VERSION,
            "loader_manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "kind": "tau2_prompt_context",
            "benchmark": "tau2",
            "skill_bundle_id": bundle.skill_bundle_id,
            "skill_hash": bundle.skill_hash,
            "bundle_path": str(bundle_path.resolve()),
            "prompt_template_id": PROMPT_TEMPLATE_ID,
            "prompt_payload_path": str(payload_path.resolve()),
            "prompt_hash": prompt_hash,
            "skills_block_hash": skills_block_hash,
            "domain": domain,
            "granularity": bundle.granularity,
            "batch_size_k": len(task_ids),
            "batch_task_ids": task_ids,
            "runtime_protocol": "prompt_context_only",
            "forbidden_runtime_paths": ["/root/skills"],
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        return LoadedSkillContext(
            kind="tau2_prompt_context",
            benchmark="tau2",
            loader_type="tau2_prompt_context",
            skill_bundle_id=bundle.skill_bundle_id,
            skill_hash=bundle.skill_hash or stable_hash({"skills": bundle.skills}),
            manifest_path=str(manifest_path.resolve()),
            bundle_path=str(bundle_path.resolve()),
            prompt_template_id=PROMPT_TEMPLATE_ID,
            prompt_payload_path=str(payload_path.resolve()),
            prompt_hash=prompt_hash,
            skills_block_hash=skills_block_hash,
            domain=domain,
            granularity=bundle.granularity,
            batch_size_k=len(task_ids),
            batch_task_ids=task_ids,
            extra_run_args=[],
            extra_env={},
        )


def render_skills_block(bundle: SkillBundle) -> str:
    lines = ["<skills>"]
    for skill in bundle.skills:
        skill_id = _plain(str(skill.get("id") or skill.get("title") or "skill"))
        title = _plain(str(skill.get("title") or skill_id))
        content = _plain(str(skill.get("content") or ""))
        lines.extend(
            [
                f"<skill id=\"{skill_id}\">",
                f"<title>{title}</title>",
                "<content>",
                content,
                "</content>",
                "</skill>",
            ]
        )
    lines.append("</skills>")
    return "\n".join(lines)


def render_prompt(*, domain_policy: str, skills_block: str) -> str:
    return "\n".join(
        [
            "<public_domain_policy>",
            _plain(domain_policy),
            "</public_domain_policy>",
            skills_block,
            "<constraints>",
            "Use only information visible in the conversation, public policy, tools, and the skills block.",
            "</constraints>",
        ]
    )


def validate_tau2_context(context: LoadedSkillContext | dict[str, Any]) -> LoadedSkillContext:
    if isinstance(context, LoadedSkillContext):
        loaded = context
    else:
        loaded = LoadedSkillContext.from_dict(context)
    if loaded.kind != "tau2_prompt_context":
        raise ValueError("skill_injected tau2 agent requires tau2_prompt_context")
    loaded.validate_for_runner("tau2")
    if loaded.prompt_payload_path and "/root/skills" in loaded.prompt_payload_path:
        raise ValueError("tau2 prompt context must not point at /root/skills")
    return loaded


def load_prompt_from_context(context: LoadedSkillContext | dict[str, Any]) -> str:
    loaded = validate_tau2_context(context)
    if not loaded.prompt_payload_path:
        raise ValueError("tau2_prompt_context requires prompt_payload_path")
    payload = json.loads(Path(loaded.prompt_payload_path).read_text(encoding="utf-8"))
    prompt = str(payload.get("prompt", ""))
    _assert_prompt_public(prompt)
    return prompt


def _assert_prompt_public(prompt: str) -> None:
    for label, pattern in FORBIDDEN_PROMPT_PATTERNS.items():
        if pattern.search(prompt):
            raise ValueError(f"tau2 prompt contains forbidden field: {label}")


def _plain(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()
