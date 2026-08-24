from __future__ import annotations

import re
from typing import Any

from ..schemas import EvoSkill


def evo_skill_to_skill_bundle_dict(
    skill: EvoSkill,
    *,
    domain: str,
    created_at: str,
    source_artifact_path: str | None = None,
) -> dict[str, Any]:
    skill_id = _skill_id(skill)
    return {
        "skill_bundle_id": f"skilllift-tau2-{domain}-{skill.key.token()}",
        "baseline": "skilllift",
        "benchmark_target": "tau2",
        "granularity": "domain",
        "domain": domain,
        "source_round": skill.key.token(),
        "source_artifact_path": source_artifact_path,
        "skills": [
            {
                "id": skill_id,
                "title": skill.skill_name,
                "content": skill.files.get("SKILL.md", "").strip() + "\n",
                "metadata": {
                    "skilllift_skill_key": skill.key.token(),
                    "entrypoint": skill.entrypoint,
                    "file_names": sorted(skill.files),
                },
            }
        ],
        "created_at": created_at,
    }


def _skill_id(skill: EvoSkill) -> str:
    raw = f"{skill.key.token()}-{skill.skill_name}"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-._")
    return safe or skill.key.token()

