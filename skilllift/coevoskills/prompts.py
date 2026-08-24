from __future__ import annotations

import json
from typing import Any

from .schemas import ArtifactSnapshot, Diagnostic, SkillVersion, SurrogateResult, TaskInput, TestSuiteVersion

SKILL_CREATOR_META = """Create a portable agent skill package for the task. The package must contain
SKILL.md plus reusable supporting files. Encode workflow, constraints, validation, and recovery.
Task outputs must be produced by following or importing the skill rather than duplicating its logic.
Return only the requested JSON object."""


def generator_initial_prompt(task: TaskInput) -> tuple[str, str]:
    return (
        "You are the CoEvoSkills Skill Generator. Maintain and improve one portable multi-file skill.",
        "\n\n".join(
            [
                "[Meta Skill]\n" + SKILL_CREATOR_META,
                "[Task]\n" + task.instruction,
                "[Reference Material]\n" + task.reference_material,
                "[Required JSON]\n" + _json(_skill_schema("full")),
            ]
        ),
    )


def generator_refine_prompt(
    task: TaskInput,
    skill: SkillVersion,
    diagnostic: Diagnostic,
    history: list[dict[str, Any]],
) -> tuple[str, str]:
    return (
        "You are the CoEvoSkills Skill Generator. Repair the skill from independent test diagnostics. Return JSON only.",
        "\n\n".join(
            [
                "[Task]\n" + task.instruction,
                "[Current Skill]\n" + _json(skill.to_dict()),
                "[Accumulated Feedback]\n" + _json(history),
                "[Latest Diagnostic]\n" + _json(diagnostic.to_dict()),
                "[Required JSON]\n" + _json(_skill_schema("patch")),
                "Do not optimize for hidden tests. Make reusable changes grounded in the diagnostic.",
            ]
        ),
    )


def verifier_suite_prompt(
    task: TaskInput,
    artifacts: ArtifactSnapshot,
    previous: TestSuiteVersion | None,
    oracle_mismatch: bool,
) -> tuple[str, str]:
    system = (
        "You are an information-isolated Surrogate Verifier. Generate deterministic Python assertions from the public task and output artifacts. "
        "You cannot see the skill, generator reasoning, hidden tests, or Oracle details. Return JSON only."
    )
    payload = {
        "task_instruction": task.instruction,
        "artifacts": artifacts.public_view(),
        "previous_suite": previous.to_dict() if previous else None,
        "oracle_mismatch": oracle_mismatch,
        "required_json": {
            "assertions": [{"assertion_id": "a1", "description": "observable requirement"}],
            "code": "def run(artifact_root):\n    return [{'assertion_id': 'a1', 'passed': True, 'message': ''}]\n",
        },
        "rules": [
            "Define exactly one function run(artifact_root).",
            "Return one result object for every declared assertion_id.",
            "Use only deterministic checks over artifact_root.",
            "Allowed imports: pathlib, json, csv, re, math, statistics, datetime.",
            "Do not access network, environment variables, absolute paths, subprocesses, or hidden data.",
            "An Oracle mismatch means the previous suite was incomplete; strengthen it without guessing hidden answers.",
        ],
    }
    return system, _json(payload)


def verifier_diagnostic_prompt(
    task: TaskInput,
    artifacts: ArtifactSnapshot,
    suite: TestSuiteVersion,
    result: SurrogateResult,
) -> tuple[str, str]:
    payload = {
        "task_instruction": task.instruction,
        "artifacts": artifacts.public_view(),
        "suite": suite.to_dict(),
        "assertion_results": result.to_dict(),
        "required_json": {
            "failed_assertion_ids": ["a1"],
            "root_cause": "publicly supported diagnosis",
            "suggestions": ["actionable reusable skill change"],
        },
    }
    return (
        "You are the isolated Surrogate Verifier. Diagnose only failed assertions from public evidence. Return JSON only.",
        _json(payload),
    )


def _skill_schema(mode: str) -> dict[str, Any]:
    return {
        "package_mode": mode,
        "skill_name": "evo-task-skill",
        "files": [
            {"path": "SKILL.md", "content": "portable workflow"},
            {"path": "scripts/utils.py", "content": "reusable Python helpers"},
        ],
        "delete_files": [],
        "entrypoint": "scripts/utils.py",
        "metadata": {"change_summary": "short summary"},
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
