from __future__ import annotations

import json
from typing import Any, Literal

from ..oracle_contrast import public_oracle_scores_payload
from ..ranking import find_rank_mismatches
from ..schemas import EvoSkill, Receipt, ReceiptRevisionInput, TaskRanking, TaskSpec, VerifierScore
from .skill_package import evo_skill_prompt_payload

VerifierMode = Literal["mode_a", "mode_b"]

VARIANT_AXES = [
    "precondition_policy_boundary",
    "evidence_reconciliation",
    "state_change_safety",
    "post_action_verification",
]

VARIANT_STRESSES = [
    "ambiguous_or_changed_user_intent",
    "insufficient_or_conflicting_evidence",
    "external_state_or_authorization_risk",
    "failed_or_partial_action_recovery",
]


def build_rubricator_init_prompt(
    task: TaskSpec, reference_material: str
) -> tuple[str, str]:
    system = (
        "You are an expert in assessment and rubric design. "
        "Generate binary, signed-point rubrics for evaluating task-specific skills. "
        "Return valid JSON only."
    )
    user = "\n".join(
        [
            f"[Task ID]\n{task.task_name}",
            f"[Task Description]\n{task.task_description}",
            "[Seed / Reference Evidence (the task is supplied above; do not repeat it)]\n"
            + _reference_without_duplicate_task(task, reference_material),
            "[Required JSON Schema]",
            _json(_receipt_schema(version=1)),
            "[Rules]",
            "- Generate 5 to 15 binary criteria.",
            "- Points must be signed integers only: positive points 1..5, negative points -5..-1.",
            "- Do not use fractional or decimal points.",
            "- Do not create multiple threshold variants for the same criterion.",
            "- maximum_score and minimum_score must match the signed point bounds.",
            "- Generate strict, discriminative criteria: incomplete, overly broad, retrieval-only, or non-operational skills must not receive full credit.",
            "- Cover every required public task intent in the task description and reference material, including state-changing intents, mid-conversation intent changes, required preconditions, and verification obligations.",
            "- Prefer concrete observable requirements over broad criteria such as 'provide the requested result'.",
            "- Full credit requires explicit operational instructions, not generic intent.",
            "- Missing required behavior must be a missed positive criterion, not a negative flaw.",
            "- Positive criteria must require concrete evidence: what action, what precondition/check, when to refuse or execute, and how to verify after action.",
            "- Negative criteria are only for explicit unsafe or forbidden instructions present in the skill.",
            "- Do not give credit for merely naming an action or saying it should be handled correctly.",
        ]
    )
    return system, user


def build_rubricator_repair_prompt(
    task: TaskSpec,
    invalid_payload: dict[str, Any],
    validation_report: dict[str, Any],
) -> tuple[str, str]:
    system = "You repair invalid assessment receipt JSON. Return valid JSON only, with no prose."
    user = "\n".join(
        [
            "[Task]",
            _json(task.to_dict()),
            "[Invalid Receipt JSON]",
            _json(invalid_payload),
            "[Validation Report]",
            _json(validation_report),
            "[Required JSON Schema]",
            _json(_receipt_schema(version=1)),
            "[Rules]",
            "- Return 5 to 15 binary criteria.",
            "- Points must be signed integers only: positive 1..5, negative -5..-1.",
            "- Do not use fractional or decimal points.",
            "- Merge overlapping threshold variants into one binary criterion.",
            "- Do not include private oracle labels or hidden answers.",
        ]
    )
    return system, user


def build_rubricator_revision_prompt(
    revision_input: ReceiptRevisionInput,
    evidence: dict[str, Any],
    previous_candidate: dict[str, Any] | None = None,
    validation_report: dict[str, Any] | None = None,
    attempt_index: int = 0,
) -> tuple[str, str]:
    system = (
        "You are an expert rubric reviser. Improve the receipt so local skill ranking "
        "better matches public oracle evidence without using private labels. Return valid JSON only."
    )
    mismatches = (
        revision_input.oracle_contrast.get("rank_mismatches", [])
        if revision_input.oracle_contrast
        else find_rank_mismatches(
            revision_input.verifier_rank, revision_input.oracle_rank
        )
    )
    payload = {
        "task": revision_input.task.to_dict(),
        "current_receipt": revision_input.receipt.to_dict(),
        "skill_evidence": evidence,
        "verifier_scores": {
            key.token(): value.to_dict()
            for key, value in revision_input.verifier_scores.items()
        },
        "oracle_scores": public_oracle_scores_payload(revision_input.oracle_scores),
        "verifier_rank": [key.token() for key in revision_input.verifier_rank],
        "oracle_rank": [key.token() for key in revision_input.oracle_rank],
        "rank_alignment": revision_input.rank_alignment,
        "rank_mismatches": mismatches,
    }
    per_task_evidence: list[str] = []
    if revision_input.per_task_rankings is not None:
        payload["per_task_rankings"] = _public_per_task_rankings_payload(
            revision_input.per_task_rankings
        )
        per_task_evidence = [
            "[Per-Task Ranking Evidence]",
            "`oracle_rank` is aggregate; `per_task_rankings` is granular. Use only rows where has_signal=true.",
        ]
    if revision_input.oracle_contrast is not None:
        payload["oracle_contrast"] = revision_input.oracle_contrast
    if revision_input.oracle_evidence is not None:
        payload["oracle_evidence"] = revision_input.oracle_evidence
        payload.pop("oracle_scores", None)
        payload.pop("per_task_rankings", None)
        per_task_evidence = []
    if previous_candidate is not None or validation_report is not None:
        payload["previous_invalid_candidate"] = previous_candidate
        payload["validation_report"] = validation_report
        payload["attempt_index"] = attempt_index
    user_parts = [
        "[Revision Input]",
        _json(payload),
        "[Evidence Field Glossary]",
        "- positive_requirement_missing: a positive-point requirement marked false by the verifier; propose an observable instruction only when public evidence supports it.",
        "- negative_violation_present: a negative-point forbidden behavior marked true; remove or rewrite that behavior in the skill, not the task requirement.",
        "- oracle_contrast: public score bands and tie groups only; it is not a hidden answer or an evaluator script.",
        "- oracle_contrast.pairwise_differences: pairs where the public oracle prefers one skill but the verifier ties or reverses them.",
        "- oracle_contrast.criterion_contrast: visible per-criterion candidate groups; use positive_requirement_missing for absent merits and negative_violation_present for present forbidden behavior.",
        "- evidence_scope.trajectory=excluded: do not infer a rule from an unseen execution trace.",
        *per_task_evidence,
        "[Oracle Contrast Diagnosis]",
        "- For each pairwise difference, compare only visible task requirements, skill files, criterion_hits, and criterion_evidence.",
        "- If the oracle-preferred skill contains a reusable observable behavior that the receipt does not reward, add or strengthen a positive criterion for that behavior.",
        "- If the lower-scoring skill contains an explicit unsafe or forbidden behavior that the receipt does not penalize, add or strengthen a negative criterion for that behavior.",
        "- A scalar score gap alone is never enough to create a criterion; when visible evidence cannot explain it, preserve the receipt and record no_visible_explanation.",
        "- If oracle_scores separate skills but verifier_scores are tied or full-credit, the current receipt is too weak.",
        "- In receipt.metadata.oracle_contrast_diagnosis, write only an evidence-bounded hypothesis from visible skill text and public scores.",
        "- Do not infer expected answers, missing task-specific actions, hidden labels, or private oracle rules from logs, chat, artifacts, filenames, or output paths.",
        "- If visible skill text does not explain the oracle gap, write no_visible_explanation instead of guessing.",
        "- If oracle scores tie (fully or partially), do not treat tied skills as having a slot-based preference.",
        "- Use oracle_contrast.oracle_rank_groups and oracle_contrast.tiers to compare score bands; skills in the same positive-score rank group can support shared evidence, but do not infer within-group ordering.",
        "- Do not compare skills inside the same zero-score rank group; treat them as no-signal examples for group-internal diagnosis.",
        "- If oracle_contrast.oracle_has_signal is false or oracle_contrast.tiers.top and oracle_contrast.tiers.bottom are empty, treat oracle contrast as no usable signal and preserve the receipt except for validation-error repair.",
        "- Revise only by adding or strengthening binary criteria judgeable from the public task and skill package alone.",
        "[Required JSON Schema]",
        _json(
            _receipt_schema(
                version=revision_input.receipt.version + 1,
                allow_removals=True,
            )
        ),
        "[Rules]",
        "- Return a complete receipt, not a patch.",
        "- Preserve a criterion only if it is specific, binary-evaluable, and discriminative.",
        "- You may remove a weak, redundant, or non-discriminative criterion only when you declare it in removed_rubrics with rubric_id, a concrete reason, and visible evidence_refs.",
        "- removed_rubrics describes only removals from the immediately previous receipt version; do not copy historical removal declarations.",
        "- Do not silently remove any criterion. Keep the receipt version exactly one greater than current_receipt.version.",
        "- Missing required behavior must be a missed positive criterion, not a negative criterion.",
        "- Negative criteria are only for explicit unsafe or forbidden instructions present in the skill.",
        "- Do not invent policy requirements, hidden oracle rules, private labels, expected actions, missing actions, or unobserved execution details.",
        "- Do not overfit to exact tool names, task ids, fault names, or one trajectory; express criteria as reusable observable behavior.",
        "- If validation_report is present, repair those validation errors instead of resampling from scratch.",
        "- Keep all points signed integers in the range -5..-1 or 1..5.",
    ]
    user = "\n".join(user_parts)
    return system, user


def build_verifier_prompt(
    task: TaskSpec,
    skill: EvoSkill,
    receipt: Receipt,
    mode: VerifierMode = "mode_a",
    skill_format: str = "code_package",
) -> tuple[str, str]:
    system = (
        "You are an expert evaluator. "
        "Your role is Verifier. "
        "You will receive a task description, a candidate skill, and a receipt containing binary rubrics. "
        "You must not invent hidden assumptions beyond the provided task, skill and rubrics. "
        "Evaluate the candidate skill against each rubric criterion in the given order. "
        "Return valid JSON only."
    )
    user = "\n".join(
        [
            "[Task]",
            _json(task.to_dict()),
            "[Candidate Skill]",
            _json(_skill_prompt_payload(skill, skill_format=skill_format)),
            "[Receipt]",
            _json(receipt.to_dict()),
            "[Required JSON Schema]",
            _json(
                {
                    "criterion_hits": {"r1": True},
                    "criterion_evidence": {"r1": "observable evidence in SKILL.md"},
                    "rationale": "short explanation",
                }
            ),
            "[Criterion Glossary]",
            "- Positive criterion (points > 0): true means the required behavior is present; false means required behavior is missing.",
            "- Negative criterion (points < 0): true means an explicit forbidden behavior is present; false means no such violation is present.",
            "- JSON fields positive_requirement_missing and negative_violation_present are derived summaries; do not emit them as criterion_hits.",
            "[Rules]",
            *_verifier_shared_rules(skill_format=skill_format),
            *_verifier_mode_rules(mode, skill_format=skill_format),
        ]
    )
    return system, user


def _verifier_shared_rules(skill_format: str = "code_package") -> list[str]:
    rules = [
        "- For positive-point criteria, true means the merit is present.",
        "- For positive-point criteria, false means the merit is absent.",
        "- For negative-point criteria, true means the flaw is present.",
        "- For negative-point criteria, false means the flaw is absent.",
        "- Judge every rubric independently.",
    ]
    if skill_format in {"markdown_guide", "agent_skill"}:
        evidence_rule = (
            "- Evaluate only observable evidence in the skill package: SKILL.md instructions, metadata, and included files."
            if skill_format == "markdown_guide"
            else "- Evaluate only observable evidence in SKILL.md, the file manifest, entrypoint when present, and included auxiliary text files."
        )
        rules.extend(
            [
                evidence_rule,
                "",
                "# Check for agent-facing workflow anti-patterns:",
                "- Missing preconditions before actions that affect tools, external state, or files.",
                "- Missing observations that tell the agent how to decide between next steps.",
                "- Missing post-action verification for required outputs, state changes, or artifacts.",
                "- Guidance that only states intent without an operational workflow the agent can follow.",
                "",
            ]
        )
    else:
        rules.extend(
            [
                "- Evaluate only observable evidence in the skill package: SKILL.md instructions, entrypoint source code, metadata, and included files.",
                "",
                "# Check for anti-patterns in Python source code:",
                "- Large hardcoded multi-line strings (>10 lines) with task-specific data",
                "- Excessive keyword matching (many if-statements checking keywords without LLM processing)",
                "- Outputs generated without fetching data or using appropriate tools",
                "- For tasks requiring web data: penalize if agent-browser or HTTP tools are not used.",
                "- For tasks requiring semantic understanding: penalize if only regex/keywords are used without LLM.",
                "",
            ]
        )
    rules.extend(
        [
        "- Do not give credit for intent unless the executable or agent-facing workflow makes the behavior likely.",
        "- Apply the same standard consistently across candidate skills so scores are comparable for ranking.",
        "- If a positive-point criterion cannot be determined from the package, mark it false.",
        "- If a negative-point flaw is not explicitly present, mark it false.",
        "- Do not assume oracle-specific hidden outputs, private labels, or benchmark answers.",
        "- rationale should mention the strongest evidence for hit/miss decisions, especially criteria affecting rank.",
        "- criterion_hits keys must exactly match receipt.rubrics[*].rubric_id.",
        "- Do not return raw_score or normalized_score; they are computed locally from criterion_hits.",
        "- rationale must be under 120 words.",
        ]
    )
    return rules


def _verifier_mode_rules(mode: VerifierMode, skill_format: str = "code_package") -> list[str]:
    if mode == "mode_b":
        return [
            "[Mode B Rules]",
            "- This score is used only for local rank comparison against a separately computed oracle rank.",
            "- Score each candidate independently using the same receipt and the same evidence standard; never compare candidate text directly.",
            "- Do not use oracle results, oracle scores, oracle feedback, or inferred oracle outcomes.",
            "- Apply an especially consistent standard across all skills in the batch.",
            "- When task_description contains Automated Checks, grading code, score formulas, required output paths, or metric weights, treat them as authoritative context for interpreting receipt criteria.",
        ]
    return [
        "[Mode A Rules]",
        "- This score is used to improve the candidate skill under the fixed receipt.",
        "- In rationale, emphasize the most actionable missed positive criteria and explicit negative flaws.",
        (
            "- Prefer evidence that shows how the agent-facing workflow can be changed to satisfy the receipt."
            if skill_format == "markdown_guide"
            else "- Prefer evidence that shows how the agent-facing workflow or executable code can be changed to satisfy the receipt."
        ),
    ]

def _public_per_task_rankings_payload(rankings: list[TaskRanking]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for index, ranking in enumerate(rankings, start=1):
        skill_scores = {
            key.token(): {
                "oracle_score": score.oracle_score,
                "oracle_pass": score.oracle_pass,
                "rank": score.rank,
            }
            for key, score in sorted(ranking.skill_scores.items())
        }
        payload.append(
            {
                "task_index": index,
                "domain": ranking.domain,
                "oracle_rank": [key.token() for key in ranking.oracle_rank],
                "skill_scores": skill_scores,
                "has_signal": ranking.has_signal,
                "no_signal_reason": ranking.no_signal_reason,
            }
        )
    return payload


def _agent_facing_workflow_discipline_rules() -> list[str]:
    return [
        "[Agent-Facing Workflow Discipline]",
        "- Make the agent-facing operational workflow concrete enough to execute on unseen instances, not just examples.",
        "- State required preconditions before the agent uses tools, changes external state, or writes files.",
        "- Name the observations the agent should collect and how those observations determine the next step.",
        "- Include post-action verification for required outputs, state changes, and artifacts.",
        "- Do not invent capabilities, APIs, tools, data fields, policies, labels, expected outputs, or hidden facts that are not present in visible task or reference material.",
        "- Action ownership: every workflow step must say whether the agent performs it directly or it depends on something outside the agent's direct control. The agent must use only capabilities visible in the task, tools, files, APIs, or reference material.",
        "- Delegated steps: when a required step is outside the agent's direct control, the skill must tell the agent to request exactly one concrete natural-language action or exactly one concrete piece of information, then continue only after the result is reported or observed.",
        "- No surrogate tool execution: never tell an outside actor to call internal tool, API, function, schema, or field names unless the visible reference explicitly says that interface is exposed to them. Convert internal names into plain observable actions or questions.",
        "- No loop stalls: after a delegated step, the skill must define what result should be checked next. Do not repeat the same status check unless the previous action completed or new information changes the next decision.",
    ]


def _agent_facing_workflow_repair_rules() -> list[str]:
    return [
        "[Agent-Facing Workflow Repair]",
        "- Repair the agent-facing workflow, not just the wording of the receipt or rationale.",
        "- Link each changed instruction to concrete preconditions, observations, or post-action verification that can improve the targeted rubrics.",
        "- Keep the repair benchmark-agnostic: do not add benchmark names, hidden labels, private oracle details, or task-family-specific business rules.",
        "- When repairing a stalled workflow, fix action ownership first: state which steps the agent performs directly, which steps are outside the agent's direct control, the single action or information needed, and the result that should advance the workflow.",
    ]


def _skill_prompt_payload(skill: EvoSkill, skill_format: str = "code_package") -> dict[str, Any]:
    if skill_format == "agent_skill":
        return evo_skill_prompt_payload(skill)
    if skill_format == "markdown_guide":
        return {
            "key": skill.key.token(),
            "skill_name": skill.skill_name,
            "metadata": skill.metadata,
            "files": skill.files,
        }
    return skill.to_dict()


def _variant_strategy(slot: int) -> dict[str, str]:
    index = max(0, slot - 1)
    axis = VARIANT_AXES[index % len(VARIANT_AXES)]
    stress = VARIANT_STRESSES[(index // len(VARIANT_AXES)) % len(VARIANT_STRESSES)]
    return {"axis": axis, "stress": stress}


def _variant_strategy_lines(slot: int) -> list[str]:
    return [
        "[Variant Strategy]",
        _json({"slot": slot, **_variant_strategy(slot)}),
        "[Strategy Rules]",
        "- Follow the Variant Strategy as this slot's primary diversity direction.",
        "- The axis defines which workflow dimension to vary; the stress defines which failure mode to handle.",
        "- Keep the same task objective; do not invent hidden facts, private oracle rules, or benchmark-specific labels.",
        "- Make this slot materially different in agent-facing workflow, not only wording.",
    ]


def _agent_runtime_name(task: TaskSpec) -> str:
    slug = "-".join(part for part in task.task_name.lower().replace("_", "-").split("-") if part)
    return f"skilllift-{slug or 'skill'}"


def _json_skill_package_rules(task: TaskSpec, skill_format: str) -> list[str]:
    common = [
        "- Every file content must be a JSON string.",
        "- Do not wrap JSON or file contents in markdown fences.",
        "- File paths must be relative POSIX paths and must not contain '..'.",
        "- SKILL.md frontmatter must include name and a concise description that helps trigger this skill.",
    ]
    if skill_format == "agent_skill":
        return [
            "- The output format is agent_skill.",
            f"- SKILL.md frontmatter name must be exactly '{_agent_runtime_name(task)}'.",
            "- A package may contain SKILL.md plus scripts/ and references/ text files; Python is optional.",
            "- Use at most 12 text files, at most 12,000 characters in SKILL.md, at most 8,000 per auxiliary file, and 40,000 characters total.",
            "- entrypoint is optional; when present it must name an existing file.",
            "- Every included .py file must parse with ast.parse.",
            *common,
        ]
    return [
        "- Return a complete package containing SKILL.md and at least one Python file.",
        "- Python file content must be raw Python source inside the JSON string.",
        "- Do not include ``` anywhere inside Python file content.",
        "- Every .py file must parse with ast.parse.",
        "- entrypoint must name an existing .py file in files.",
        *common,
    ]


def build_sg_seed_prompt(
    task: TaskSpec,
    reference_material: str,
    skill_format: str = "code_package",
    content_block: str | None = None,
) -> tuple[str, str]:
    if skill_format == "markdown_guide":
        system = (
            "You are an expert markdown guide skill writer. "
            "Return exactly one markdown/xml package and nothing else."
        )
        user = "\n".join(
            [
                "[Task]",
                _json(task.to_dict()),
                "[Reference Material]",
                _reference_without_duplicate_task(task, reference_material),
                "[Instruction]",
                "Generate one complete markdown guide skill for this task.",
                "[Output Contract]",
                _skill_package_schema(skill_format=skill_format, content_block=content_block),
                "[Rules]",
                "- Return package mode='full' and strategy='seed'.",
                "- SKILL.md is the only file.",
                "- Do not include executable source files or execution hooks.",
                "- Do not use JSON as the output contract.",
                "- Use exactly the provided content_block token for the SKILL.md body.",
                *_agent_facing_workflow_discipline_rules(),
                "- Keep the guide grounded in the task and public reference material.",
                "- Do not include hardcoded expected answers, private oracle labels, API keys, or secrets.",
            ]
        )
        return system, user
    if skill_format == "agent_skill":
        system = (
            "You are an expert agent skill package engineer. "
            "Return exactly one JSON object and nothing else."
        )
        user = "\n".join(
            [
                "[Task]",
                _json(task.to_dict()),
                "[Reference Material]",
                _reference_without_duplicate_task(task, reference_material),
                "[Instruction]",
                "Generate one complete multi-file agent skill for this task.",
                "[Output Contract]",
                _json(_skill_package_schema(skill_format=skill_format)),
                "[Rules]",
                "- Return package_mode='full'.",
                *_agent_facing_workflow_discipline_rules(),
                *_json_skill_package_rules(task, skill_format),
                "- Do not include hardcoded expected answers, private oracle labels, API keys, or secrets.",
            ]
        )
        return system, user
    system = (
        "You are an expert agent skill package engineer. "
        "Return exactly one JSON object and nothing else. "
        "Do not wrap the JSON in markdown fences."
    )
    user = "\n".join(
        [
            "[Task]",
            _json(task.to_dict()),
            "[Reference Material]",
            _reference_without_duplicate_task(task, reference_material),
            "[Instruction]",
            "Generate one complete agent skill package for this task.",
            "[Output Contract]",
            _json(_skill_package_schema(skill_format=skill_format, content_block=content_block)),
            "[Rules]",
            "# CRITICAL: Your skill will be tested on DIFFERENT data from the examples.",
            "- DO NOT hardcode the final answer or expected outputs from task examples.",
            "- DO NOT use large multi-line string literals (>10 lines) containing task-specific results.",
            *_agent_facing_workflow_discipline_rules(),
            "- Return package_mode='full'.",
            *_json_skill_package_rules(task, skill_format),
            "- Keep the skill read-only when the task is read-only.",
            "- Do not include hardcoded expected answers, private oracle labels, API keys, or secrets.",
        ]
    )
    return system, user


def build_sg_variant_prompt(
    task: TaskSpec,
    seed: EvoSkill,
    slot: int,
    skill_format: str = "code_package",
    content_block: str | None = None,
) -> tuple[str, str]:
    if skill_format == "markdown_guide":
        system = (
            "You are an expert markdown guide skill writer. "
            "Generate one variant of the provided baseline guide. "
            "Return exactly one markdown/xml package and nothing else."
        )
        user = "\n".join(
            [
                "[Task]",
                _json(task.to_dict()),
                f"[Variant Slot]\n{slot}",
                *_variant_strategy_lines(slot),
                "[Seed Skill (reference; do not restate)]",
                _json(_skill_prompt_payload(seed, skill_format=skill_format)),
                "[Instruction]",
                "Write a markdown guide with agent-facing workflow differences from the seed; do not merely rephrase it.",
                "[Output Contract]",
                _skill_package_schema(skill_format=skill_format, content_block=content_block),
                "[Rules]",
                "- Return package mode='full'.",
                "- SKILL.md is the only file.",
                "- Do not include executable source files or execution hooks.",
                "- Do not use JSON as the output contract.",
                "- Preserve the same task objective.",
                "- The variant must produce agent-facing workflow differences, not cosmetic wording.",
                *_agent_facing_workflow_discipline_rules(),
            ]
        )
        return system, user
    system = (
        "You are an expert agent skill package engineer. "
        "Generate one variant of the provided baseline skill package. "
        "Return exactly one JSON object and nothing else. "
        "Do not wrap the JSON in markdown fences."
    )
    user = "\n".join(
        [
            "[Task]",
            _json(task.to_dict()),
            f"[Variant Slot]\n{slot}",
            *_variant_strategy_lines(slot),
            "[Seed Skill (reference; do not restate)]",
            _json(_skill_prompt_payload(seed, skill_format=skill_format)),
            "[Instruction]",
            "Write a skill package with agent-facing workflow differences from the seed; do not merely rephrase it.",
            "[Output Contract]",
            _json(_skill_package_schema(skill_format=skill_format, content_block=content_block)),
            "[Rules]",
            "- Return package_mode='full'.",
            "- Preserve the same task objective.",
            "- The variant must produce agent-facing workflow differences, not cosmetic wording.",
            "",
            "# If seed contains anti-patterns, DO NOT copy them:",
            "- If the seed skill contains hardcoded answers or keyword-matching anti-patterns, DO NOT copy them.",
            "- Generate a genuinely different approach grounded in visible task and reference material.",
            "",
            *_agent_facing_workflow_discipline_rules(),
            *_json_skill_package_rules(task, skill_format),
        ]
    )
    return system, user


def build_sg_repair_prompt(
    task: TaskSpec,
    strategy: str,
    invalid_payload: dict[str, Any] | str,
    validation_error: str | dict[str, Any],
    skill_format: str = "code_package",
    content_block: str | None = None,
) -> tuple[str, str]:
    mode_a_repair = strategy == "mode_a_update"
    if skill_format == "markdown_guide":
        repair_scope = (
                "When [Strategy] is mode_a_update, this repairs a failed skill-update package. Repair envelope structure, content_block usage, targeted_rubrics, expected_effect, and changed_files without changing the task objective. "
                if mode_a_repair
                else "Repair only envelope structure, content_block usage, and required metadata. "
        )
        system = "".join(
            [
                "You repair an invalid markdown guide package. ",
                "Return exactly one markdown/xml package and nothing else. ",
                repair_scope,
            ]
        )
        error_payload = (
            validation_error
            if isinstance(validation_error, dict)
            else {"message": validation_error}
        )
        user = "\n".join(
            [
                "[Task]",
                _json(task.to_dict()),
                f"[Strategy]\n{strategy}",
                "[Original Output Contract]",
                _skill_patch_schema(skill_format=skill_format, content_block=content_block)
                if strategy == "mode_a_update"
                else _skill_package_schema(skill_format=skill_format, content_block=content_block),
                "[Invalid Markdown Guide Package]",
                str(invalid_payload),
                "[Validation Error]",
                _json(error_payload),
                "[Rules]",
                "- Preserve the task intent and useful guidance from the invalid package.",
                "- Use SKILL.md as the only file.",
                "- Do not include executable source files or execution hooks.",
                "- Use exactly the provided content_block token.",
                "- When [Strategy] is mode_a_update, preserve or repair targeted_rubrics and expected_effect.",
                "- If validation_error.allowed_target_rubrics is present, targeted_rubrics must use exact rubric_id values from that list.",
                *(
                    [
                        "- When [Strategy] is mode_a_update, changed_files must list SKILL.md.",
                        "- Do not introduce new task logic, hidden facts, expected answers, or private oracle rules.",
                    ]
                    if mode_a_repair
                    else []
                ),
            ]
        )
        return system, user
    if skill_format == "agent_skill":
        error_payload = validation_error if isinstance(validation_error, dict) else {"message": validation_error}
        system = (
            "Repair an invalid multi-file agent_skill JSON package. "
            "Return exactly one JSON object and nothing else."
        )
        user = "\n".join(
            [
                "[Task]",
                _json(task.to_dict()),
                f"[Strategy]\n{strategy}",
                "[Original Output Contract]",
                _json(
                    _skill_patch_schema(skill_format=skill_format)
                    if mode_a_repair
                    else _skill_package_schema(skill_format=skill_format)
                ),
                "[Invalid Skill Package JSON]",
                _json(invalid_payload),
                "[Validation Error]",
                _json(error_payload),
                "[Rules]",
                "- Repair schema, JSON string escaping, file paths, frontmatter, size limits, and Python syntax when Python files exist.",
                "- Preserve the task intent and useful workflow; do not invent hidden answers or private oracle rules.",
                "- For Mode A preserve or repair metadata.targeted_rubrics, expected_effect, and changed_files.",
                *_json_skill_package_rules(task, skill_format),
            ]
        )
        return system, user
    repair_scope = (
        "When [Strategy] is mode_a_update, this repairs a failed skill-update package. Repair schema, JSON string escaping, markdown fence leakage, Python syntax, metadata.targeted_rubrics, metadata.expected_effect, and metadata.changed_files without changing the task objective. "
        if mode_a_repair
        else "Repair only schema, JSON string escaping, markdown fence leakage, and Python syntax. "
    )
    system = "".join(
        [
            "You repair an invalid agent skill package JSON object. ",
            "Return exactly one JSON object and nothing else. ",
            "Do not wrap the JSON in markdown fences. ",
            repair_scope,
            "Do not rewrite the task logic, strategy, or intended behavior.",
        ]
    )
    error_payload = (
        validation_error
        if isinstance(validation_error, dict)
        else {"message": validation_error}
    )
    user = "\n".join(
        [
            "[Task]",
            _json(task.to_dict()),
            f"[Strategy]\n{strategy}",
            "[Original Output Contract]",
            _json(_skill_package_schema(skill_format=skill_format, content_block=content_block)),
            "[Invalid Skill Package JSON]",
            _json(invalid_payload),
            "[Validation Error]",
            _json(error_payload),
            "[Rules]",
            "- Repair only invalid representation details: schema, JSON string escaping, markdown fences, and Python syntax.",
            "- Return the same package_mode unless the package cannot be repaired safely without returning a full package.",
            "- Preserve the task intent and the useful guidance from the invalid package.",
            "- Do not convert this into guidance-only output.",
            "- Every file content must be a JSON string.",
            "- Every .py file content must be raw Python source only, no markdown fences.",
            "- Do not include ``` anywhere inside Python file content.",
            "- Every .py file must parse with ast.parse; fix malformed newline artifacts like `):n    `.",
            "- Keep the skill read-only when the task is read-only.",
            "- When [Strategy] is mode_a_update, preserve or repair metadata.targeted_rubrics and metadata.expected_effect.",
            "- If validation_error.allowed_target_rubrics is present, metadata.targeted_rubrics must use exact rubric_id values from that list.",
            *(
                [
                    "- When [Strategy] is mode_a_update, metadata.changed_files must list files changed by the repaired output.",
                    "- Do not introduce new task logic, hidden facts, expected answers, or private oracle rules.",
                ]
                if mode_a_repair
                else []
            ),
        ]
    )
    return system, user


def build_sg_update_prompt(
    task: TaskSpec,
    skill: EvoSkill,
    receipt: Receipt,
    score: VerifierScore,
    skill_format: str = "code_package",
    content_block: str | None = None,
) -> tuple[str, str]:
    if skill_format == "markdown_guide":
        system = (
            "You are an expert markdown guide skill writer. "
            "Your role is Skill Generator in Mode A. "
            "You improve one candidate guide under a fixed receipt. "
            "Return exactly one markdown/xml package and nothing else."
        )
        skill_package = {
            "key": skill.key.token(),
            "skill_name": skill.skill_name,
            "metadata": skill.metadata,
            "files": skill.files,
        }
        user = "\n".join(
            [
                "[Task]",
                _json(task.to_dict()),
                "[Fixed Receipt]",
                _json(receipt.to_dict()),
                "[Current Skill Package]",
                _json(skill_package),
                "[Verifier Result Under Fixed Receipt]",
                _json(score.to_dict()),
                "[Current Actionable Target Rubrics]",
                _json(_mode_a_actionable_target_rubrics(receipt, score)),
                "[Output Mode Decision]",
                "- Choose exactly one output mode: patch or full.",
                "- Do not mix patch and full schemas.",
                "- Use patch for localized file edits.",
                "- Use full only when the current package or guide is misleading, inconsistent, or too broken to patch safely.",
                "[Patch Output Contract]",
                _skill_patch_schema(skill_format=skill_format, content_block=content_block),
                "[Full Output Contract]",
                _skill_package_schema(skill_format=skill_format, content_block=content_block),
                "[Update Objective]",
                "Improve SKILL.md with agent-facing changes that address verifier misses under the fixed receipt. Do not merely rephrase rubric wording.",
                "[Acceptance Target]",
                "- This candidate will be rescored by the same verifier under the same fixed receipt.",
                "- The candidate is accepted only if this slot's normalized_score does not decrease.",
                "- Optimize for concrete criterion_hits improvements, not cosmetic wording.",
                *_agent_facing_workflow_repair_rules(),
                "[Target Selection]",
                "- Target positive-point rubrics whose current hit is false.",
                "- Target negative-point rubrics whose current hit is true.",
                "- targeted_rubrics must contain exact rubric_id strings from [Current Actionable Target Rubrics].",
                "- Do not use category names, criterion labels, oracle metric names, or derived labels such as figure_count_accuracy.",
                "[Hard Constraints]",
                "- Preserve the task objective, safety constraints, and existing working workflow unless replacing it with a stronger equivalent.",
                "- Do not update the receipt, hardcode expected outputs, use private oracle labels, or expose hidden answers.",
                "- SKILL.md is the only file.",
                "- Do not include executable source files or execution hooks.",
                "- Use exactly the provided content_block token.",
                "[Output Mode]",
                "- The returned package_mode/mode must match the single output mode chosen above.",
                "- Do not return a no-op patch.",
                "[Metadata]",
                "- strategy must be 'mode_a_update' for patch or 'mode_a_rewrite' for full.",
                "- targeted_rubrics must be a non-empty comma-separated list of addressed rubric IDs.",
                "- changed_files should list SKILL.md.",
                "- expected_effect must explain how SKILL.md addresses the targeted rubrics.",
            ]
        )
        return system, user
    if skill_format == "agent_skill":
        system = (
            "You are an expert agent skill engineer. Your role is Skill Generator in Mode A. "
            "Return exactly one JSON object and nothing else."
        )
        user = "\n".join(
            [
                "[Task]",
                _json(task.to_dict()),
                "[Fixed Receipt]",
                _json(receipt.to_dict()),
                "[Current Skill Package]",
                _json(_skill_prompt_payload(skill, skill_format=skill_format)),
                "[Verifier Result Under Fixed Receipt]",
                _json(score.to_dict()),
                "[Current Actionable Target Rubrics]",
                _json(_mode_a_actionable_target_rubrics(receipt, score)),
                "[Patch Output Contract]",
                _json(_skill_patch_schema(skill_format=skill_format)),
                "[Full Output Contract]",
                _json(_skill_package_schema(skill_format=skill_format)),
                "[Update Objective]",
                "Improve agent-facing workflow or supporting text files to address current verifier misses.",
                *_agent_facing_workflow_repair_rules(),
                "[Target Selection]",
                "- metadata.targeted_rubrics must use exact rubric_id values from the actionable targets.",
                "- metadata.expected_effect must explain the intended criterion improvement.",
                "- metadata.changed_files must list every changed or deleted file.",
                "[Package Rules]",
                *_json_skill_package_rules(task, skill_format),
                "- Do not return a no-op patch, hidden answers, private oracle labels, API keys, or secrets.",
            ]
        )
        return system, user
    system = (
        "You are an expert agent skill engineer. "
        "Your role is Skill Generator in Mode A. "
        "You improve one candidate skill package under a fixed receipt. "
        "Return exactly one JSON object and nothing else. "
        "Do not wrap the JSON in markdown fences."
    )
    skill_package = {
        "key": skill.key.token(),
        "skill_name": skill.skill_name,
        "entrypoint": skill.entrypoint,
        "metadata": skill.metadata,
        "files": skill.files,
    }
    user = "\n".join(
        [
            "[Task]",
            _json(task.to_dict()),
            "[Fixed Receipt]",
            _json(receipt.to_dict()),
            "[Current Skill Package]",
            _json(skill_package),
            "[Verifier Result Under Fixed Receipt]",
            _json(score.to_dict()),
            "[Current Actionable Target Rubrics]",
            _json(_mode_a_actionable_target_rubrics(receipt, score)),
            "[Output Mode Decision]",
            "- Choose exactly one output mode: patch or full.",
            "- Do not mix patch and full schemas.",
            "- Use patch for localized file edits.",
            "- Use full only when the current package or guide is misleading, inconsistent, or too broken to patch safely.",
            "[Patch Output Contract]",
            _json(_skill_patch_schema(skill_format=skill_format, content_block=content_block)),
            "[Full Output Contract]",
            _json(_skill_package_schema(skill_format=skill_format, content_block=content_block)),
            "[Update Objective]",
            "Improve the current skill package with executable or agent-facing changes that address verifier misses under the fixed receipt. Do not merely rephrase rubric wording.",
            "[Acceptance Target]",
            "- This candidate will be rescored by the same verifier under the same fixed receipt.",
            "- The candidate is accepted only if this slot's normalized_score does not decrease.",
            "- Optimize for concrete criterion_hits improvements, not cosmetic wording.",
            "[Rubric Hit Semantics]",
            "- Use receipt.rubrics[*].points with verifier_result.criterion_hits.",
            "- Positive-point false: add the missing merit; positive-point true: preserve it.",
            "- Negative-point true: remove the flaw/penalty; negative-point false: keep it absent.",
            "- Prioritize high absolute-point rubrics and issues lowering normalized_score.",
            "[Target Selection]",
            "- Target positive-point rubrics whose current hit is false.",
            "- Target negative-point rubrics whose current hit is true.",
            "- metadata.targeted_rubrics must contain exact rubric_id strings from [Current Actionable Target Rubrics].",
            "- Do not use category names, criterion labels, oracle metric names, or derived labels such as figure_count_accuracy.",
            "- If a miss depends on produced files, parsing, ordering, validation, or artifact generation, prefer executable .py changes.",
            "- SKILL.md-only patches are valid only when the miss is clearly about agent-facing procedure.",
            "- Metadata-only changes are invalid.",
            "[Hard Constraints]",
            "- Preserve the task objective, safety constraints, and existing working workflow unless replacing it with a stronger equivalent.",
            "- Do not update the receipt, hardcode expected outputs, use private oracle labels, or expose hidden answers.",
            "",
            "# Execution Logic Constraints:",
            "- When fixing a criterion miss, prefer changing executable logic over hardcoding outputs.",
            "- If the current skill uses keyword matching or hardcoded data, replace with proper tool usage or semantic understanding.",
            "- Ensure the updated skill works on varied inputs, not just the specific example.",
            "",
            *_agent_facing_workflow_repair_rules(),
            "- If the task produces artifacts, keep writing required outputs under /tmp_workspace/results/ unless the task explicitly says otherwise.",
            "- File paths must be relative POSIX paths and must not contain '..'.",
            "- File contents must be JSON strings; Python files must be raw source, contain no markdown fences, and parse with ast.parse.",
            "- entrypoint must name an existing .py file after patch merge or full replacement.",
            "[Output Mode]",
            "- The returned package_mode/mode must match the single output mode chosen above.",
            "- Do not return guidance_items or a no-op patch.",
            "[Metadata]",
            "- metadata.strategy must be 'mode_a_update' for patch or 'mode_a_rewrite' for full.",
            "- metadata.targeted_rubrics must be a non-empty list of addressed rubric IDs.",
            "- metadata.changed_files should list files changed by this output.",
            "- metadata.expected_effect must explain how changed files address the targeted rubrics.",
        ]
    )
    return system, user


def _receipt_schema(*, version: int, allow_removals: bool = False) -> dict[str, Any]:
    schema = {
        "metadata": {"question_domain": "domain/subdomain"},
        "rubrics": [
            {
                "rubric_id": "r1",
                "category": "string",
                "criterion": "binary evaluable criterion",
                "points": 1,
            }
        ],
        "maximum_score": 1,
        "minimum_score": -1,
        "baseline_score": 0,
        "version": version,
        "removed_rubrics": [],
    }
    if allow_removals:
        schema["removed_rubrics"] = [
            {
                "rubric_id": "r6",
                "reason": "why this old criterion is weak or redundant",
                "evidence_refs": ["oracle_contrast.oracle_rank_groups"],
            }
        ]
    return schema


def _skill_package_schema(
    skill_format: str = "code_package",
    content_block: str | None = None,
) -> dict[str, Any] | str:
    if skill_format == "markdown_guide":
        block = content_block or "skill_block_example"
        return "\n".join(
            [
                '<package mode="full" strategy="seed">',
                "<summary>short strategy summary</summary>",
                f'<file path="SKILL.md" content_block="{block}" />',
                "",
                f"<{block}>",
                "---",
                "name: skill",
                "description: concise trigger for when to use this guide",
                "---",
                "",
                "# Guide",
                "",
                "- Public, task-grounded guidance goes here.",
                f"</{block}>",
                "</package>",
            ]
        )
    if skill_format == "agent_skill":
        return {
            "skill_format": "agent_skill",
            "package_mode": "full",
            "files": [
                {
                    "path": "SKILL.md",
                    "content": "---\\nname: skilllift-domain\\ndescription: concise trigger\\n---\\n...",
                },
                {"path": "references/workflow.md", "content": "# Workflow\\n..."},
            ],
            "entrypoint": None,
            "metadata": {"summary": "short strategy summary", "strategy": "seed"},
            "delete_files": [],
        }
    return {
        "package_mode": "full",
        "files": [
            {
                "path": "SKILL.md",
                "content": "---\\nname: skill\\ndescription: concise trigger for when to use this skill\\n---\\n...",
            },
            {"path": "executor.py", "content": "print('optional helper')\\n"},
        ],
        "entrypoint": "executor.py",
        "metadata": {
            "summary": "short strategy summary",
            "strategy": "seed_or_variant_or_mode_a_rewrite",
            "changed_files": ["SKILL.md", "executor.py"],
        },
        "delete_files": [],
    }


def _skill_patch_schema(
    skill_format: str = "code_package",
    content_block: str | None = None,
) -> dict[str, Any] | str:
    if skill_format == "markdown_guide":
        block = content_block or "skill_block_example"
        return "\n".join(
            [
                '<package mode="patch" strategy="mode_a_update">',
                "<targeted_rubrics>r1</targeted_rubrics>",
                "<expected_effect>which verifier misses or penalties this update addresses</expected_effect>",
                "<changed_files>SKILL.md</changed_files>",
                f'<file path="SKILL.md" content_block="{block}" />',
                "",
                f"<{block}>",
                "# Updated Guide",
                "",
                "- Revised agent-facing guidance goes here.",
                f"</{block}>",
                "</package>",
            ]
        )
    if skill_format == "agent_skill":
        return {
            "skill_format": "agent_skill",
            "package_mode": "patch",
            "files": [{"path": "SKILL.md", "content": "complete updated SKILL.md"}],
            "delete_files": [],
            "entrypoint": None,
            "metadata": {
                "strategy": "mode_a_update",
                "targeted_rubrics": ["r1"],
                "changed_files": ["SKILL.md"],
                "expected_effect": "how the changed files address the targeted rubrics",
            },
        }
    return {
        "package_mode": "patch",
        "files": [
            {"path": "executor.py", "content": "print('updated helper')\n"},
        ],
        "delete_files": [],
        "entrypoint": "executor.py",
        "metadata": {
            "summary": "short update summary",
            "strategy": "mode_a_update",
            "targeted_rubrics": ["r1"],
            "changed_files": ["SKILL.md", "executor.py"],
            "expected_effect": "which verifier misses or penalties this update addresses",
        },
    }


def _mode_a_actionable_target_rubrics(
    receipt: Receipt,
    score: VerifierScore,
) -> list[dict[str, Any]]:
    targets = []
    for rubric in receipt.rubrics:
        hit = score.criterion_hits.get(rubric.rubric_id, False)
        if rubric.points > 0 and not hit:
            targets.append(_target_rubric_payload(rubric, hit, "missed_positive"))
        elif rubric.points < 0 and hit:
            targets.append(_target_rubric_payload(rubric, hit, "active_penalty"))
    return targets


def _target_rubric_payload(
    rubric: Any,
    current_hit: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "rubric_id": rubric.rubric_id,
        "category": rubric.category,
        "criterion": rubric.criterion,
        "points": rubric.points,
        "current_hit": current_hit,
        "reason": reason,
    }


def _reference_without_duplicate_task(task: TaskSpec, reference_material: str) -> str:
    marker = "# Task Description"
    if not reference_material.startswith(marker):
        return reference_material
    body = reference_material[len(marker) :].lstrip()
    task_text = task.task_description.strip()
    if not body.startswith(task_text):
        return reference_material
    rest = body[len(task_text) :].strip()
    return rest or "No additional reference material."


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
