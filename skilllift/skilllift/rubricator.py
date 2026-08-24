from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .errors import LLMOutputError
from .llm_client import LLMClient
from .normalization import recompute_receipt_bounds
from .oracle_contrast import public_oracle_score_payload
from .persistence import ExperimentStore
from .portfolio import PortfolioContextLimitError
from .baselines.prompts import (
    build_rubricator_init_prompt,
    build_rubricator_repair_prompt,
    build_rubricator_revision_prompt,
)
from .schemas import (
    EvidenceBudgetConfig,
    EvoSkill,
    OracleScore,
    Receipt,
    ReceiptRevisionAttempt,
    ReceiptRevisionInput,
    RubricCriterion,
    SkillKey,
    TaskSpec,
    VerifierScore,
)


@dataclass
class ValidationReport:
    ok: bool
    error_codes: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error_codes": self.error_codes,
            "messages": self.messages,
        }


def generate_initial_receipt(
    task: TaskSpec,
    reference_material: str,
    llm_client: LLMClient | None,
    *,
    max_input_chars: int | None = None,
) -> Receipt:
    if llm_client is None:
        return build_fallback_receipt(task)
    system, user = build_rubricator_init_prompt(task, reference_material)
    if max_input_chars is not None and len(system) + len(user) > max_input_chars:
        raise PortfolioContextLimitError(
            "initial rubricator input exceeds the configured model context"
        )
    try:
        payload = llm_client.call_json(system, user, temperature=0.0)
    except LLMOutputError as exc:
        return _fallback_receipt_for_initial_failure(task, "initial_call", exc)
    receipt = normalize_receipt_candidate(payload, old_receipt=None)
    report = validate_receipt(receipt)
    if not report.ok:
        repaired = repair_initial_receipt(task, payload, report, llm_client)
        if repaired is None:
            return _fallback_receipt_for_initial_failure(
                task,
                "repair_validation",
                LLMOutputError(
                    f"rubricator produced invalid receipt: {report.error_codes}"
                ),
            )
        return repaired
    return receipt


def repair_initial_receipt(
    task: TaskSpec,
    invalid_payload: dict[str, Any],
    report: ValidationReport,
    llm_client: LLMClient,
) -> Receipt | None:
    system, user = build_rubricator_repair_prompt(
        task, invalid_payload, report.to_dict()
    )
    payload = llm_client.call_json(system, user, temperature=0.0)
    receipt = normalize_receipt_candidate(payload, old_receipt=None)
    receipt.metadata["llm_repaired"] = True
    repaired_report = validate_receipt(receipt)
    return receipt if repaired_report.ok else None


def _fallback_receipt_for_initial_failure(
    task: TaskSpec,
    stage: str,
    error: Exception,
) -> Receipt:
    receipt = build_fallback_receipt(task)
    receipt.metadata.update(
        {
            "llm_initial_failed": True,
            "fallback_reason": str(error),
            "fallback_stage": stage,
        }
    )
    return receipt


def validate_receipt(
    receipt: Receipt, *, old_receipt: Receipt | None = None
) -> ValidationReport:
    errors: list[str] = []
    messages: list[str] = []
    _validate_count(receipt, errors, messages)
    _validate_rubrics(receipt, errors, messages)
    computed = recompute_receipt_bounds(receipt)
    if receipt.maximum_score != computed.maximum_score:
        errors.append("maximum_score_mismatch")
    if receipt.minimum_score != computed.minimum_score:
        errors.append("minimum_score_mismatch")
    if receipt.maximum_score == receipt.minimum_score:
        errors.append("empty_score_range")
    if receipt.version < 1:
        errors.append("receipt_version_invalid")
    if _contains_hidden_answer(receipt):
        errors.append("hidden_answer_leak")
    if old_receipt is not None:
        _validate_revision_delta(old_receipt, receipt, errors, messages)
    elif receipt.removed_rubrics:
        errors.append("initial_receipt_cannot_remove_rubrics")
    return ValidationReport(ok=not errors, error_codes=errors, messages=messages)


def normalize_receipt_candidate(
    payload: dict[str, Any], old_receipt: Receipt | None
) -> Receipt:
    version = _receipt_version(payload.get("version"), old_receipt)
    raw_rubrics = payload.get("rubrics", [])
    if not isinstance(raw_rubrics, list):
        raw_rubrics = []
    rubrics = [
        _rubric_from_payload(item, index)
        for index, item in enumerate(raw_rubrics, start=1)
    ]
    receipt = Receipt(
        version=version,
        rubrics=rubrics,
        maximum_score=0,
        minimum_score=0,
        baseline_score=payload.get("baseline_score"),
        metadata=(
            payload.get("metadata", {})
            if isinstance(payload.get("metadata", {}), dict)
            else {}
        ),
        removed_rubrics=_removed_rubrics_from_payload(payload.get("removed_rubrics")),
    )
    return recompute_receipt_bounds(receipt)


def _revision_temperature(rank_alignment: float | None, attempt_index: int) -> float:
    if attempt_index > 0:
        return 0.15
    alignment = rank_alignment if rank_alignment is not None else 0.4
    if alignment < 0.3:
        return 0.6
    if alignment < 0.7:
        return 0.4
    return 0.15


def revise_receipt(
    revision_input: ReceiptRevisionInput,
    llm_client: LLMClient,
    store: ExperimentStore,
    budget: EvidenceBudgetConfig | None = None,
    *,
    max_input_chars: int | None = None,
) -> Receipt:
    evidence = build_skill_evidence_package(
        revision_input.task,
        revision_input.skills,
        revision_input.verifier_scores,
        revision_input.oracle_scores,
        budget,
    )
    previous_payload: dict[str, Any] | None = None
    previous_report: ValidationReport | None = None
    for attempt_index in range(2):
        system, user = build_rubricator_revision_prompt(
            revision_input,
            evidence,
            previous_candidate=previous_payload,
            validation_report=previous_report.to_dict() if previous_report else None,
            attempt_index=attempt_index,
        )
        if max_input_chars is not None and len(system) + len(user) > max_input_chars:
            raise PortfolioContextLimitError(
                "rubricator revision input exceeds the configured model context"
            )
        temperature = _revision_temperature(
            revision_input.rank_alignment, attempt_index
        )
        payload = llm_client.call_json(system, user, temperature=temperature)
        candidate = normalize_receipt_candidate(payload, revision_input.receipt)
        report = validate_receipt(candidate, old_receipt=revision_input.receipt)
        decision = (
            "accepted"
            if report.ok
            else "retry" if attempt_index == 0 else "rejected_keep_old"
        )
        attempt_prefix = (
            f"outer_{store.current_outer_round:03d}_"
            if store.current_outer_round
            else ""
        )
        attempt = record_revision_attempt(
            store,
            revision_input.receipt,
            candidate,
            report,
            attempt_index,
            {
                "prompt.txt": f"{system}\n\n{user}",
                "parsed_candidate.json": json.dumps(payload, indent=2),
                "receipt_diff.json": json.dumps(
                    _receipt_diff(revision_input.receipt, candidate), indent=2
                ),
            },
            {
                "decision": decision,
                "repair_of_attempt_id": (
                    f"{attempt_prefix}receipt_v{revision_input.receipt.version}_attempt_0"
                    if attempt_index == 1
                    else ""
                ),
                "temperature": temperature,
            },
        )
        if attempt.accepted:
            return candidate
        previous_payload = payload
        previous_report = report
    store.append_event(
        {
            "event": "receipt_revision_failed",
            "old_receipt_version": revision_input.receipt.version,
            "error_codes": previous_report.error_codes if previous_report else [],
            "decision": "rejected_keep_old",
        }
    )
    return revision_input.receipt


def build_fallback_receipt(task: TaskSpec) -> Receipt:
    rubrics = [
        RubricCriterion(
            "r1", "Planning", "Skill gives an ordered plan for solving the task", 2
        ),
        RubricCriterion(
            "r2",
            "Tool Coverage",
            "Skill identifies required data-gathering tool calls",
            3,
        ),
        RubricCriterion(
            "r3", "Validation", "Skill requires verifying outputs before finishing", 2
        ),
        RubricCriterion(
            "r4",
            "Safety",
            "Skill forbids actions that the task marks as unsafe or read-only",
            2,
        ),
        RubricCriterion(
            "r5",
            "Completeness",
            "Skill emphasizes complete coverage of task requirements",
            2,
        ),
        RubricCriterion(
            "r6",
            "Noise",
            "Skill encourages reporting irrelevant or stale items as current work",
            -2,
        ),
    ]
    receipt = Receipt(
        version=1,
        rubrics=rubrics,
        maximum_score=0,
        minimum_score=0,
        baseline_score=0,
        metadata={"source": "fallback", "task": task.task_name},
    )
    return recompute_receipt_bounds(receipt)


def build_skill_evidence_package(
    task: TaskSpec,
    skills: dict[SkillKey, EvoSkill],
    verifier_scores: dict[SkillKey, VerifierScore],
    oracle_scores: dict[SkillKey, OracleScore],
    budget: EvidenceBudgetConfig | None = None,
) -> dict[str, Any]:
    ordered = sorted(skills.items())
    remaining = None if budget is None else max(
        0,
        budget.max_total_chars
        - sum(len(skill.files.get("SKILL.md", "")) for _, skill in ordered),
    )
    items = []
    for key, skill in ordered:
        skill_md = skill.files.get("SKILL.md", "")
        entrypoint = skill.entrypoint or _first_python_file(skill.files)
        aux_paths = [path for path in sorted(skill.files) if path != "SKILL.md"]
        aux_paths.sort(
            key=lambda path: (
                0 if path == entrypoint else 1 if path in skill_md else 2,
                path,
            )
        )
        aux_files = []
        for path in aux_paths:
            content = skill.files[path]
            if budget is None:
                limit = len(content)
            else:
                if remaining <= 0:
                    break
                limit = min(len(content), budget.max_aux_file_chars, remaining)
                remaining -= limit
            aux_files.append({"path": path, "content": content[:limit]})
        entrypoint_code = next(
            (item["content"] for item in aux_files if item["path"] == entrypoint),
            "",
        )
        items.append(
            {
                "skill_key": {"slot": key.slot, "version": key.version},
                "key": key.token(),
                "skill_name": skill.skill_name,
                "package_manifest": sorted(skill.files),
                "file_manifest": [
                    {
                        "path": path,
                        "chars": len(content),
                        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    }
                    for path, content in sorted(skill.files.items())
                ],
                "skill_md": skill_md,
                "entrypoint": entrypoint,
                "entrypoint_code": entrypoint_code,
                "aux_files": aux_files,
                "metadata": skill.metadata,
                "verifier_score": verifier_scores[key].to_dict() if key in verifier_scores else None,
                "oracle_score": public_oracle_score_payload(oracle_scores.get(key)),
            "evidence_level": "complete_portfolio" if budget is None else "global_budget",
            }
        )
    return {
        "task": task.to_dict(),
        "evidence_scope": {
            "task": "public_task_spec",
            "seed": "complete_portfolio" if budget is None else "manifest_plus_SKILL.md_plus_budgeted_auxiliary_files",
            "trajectory": "excluded",
            "private_evaluator_material": "excluded",
        },
        "skills": items,
    }


def record_revision_attempt(
    store: ExperimentStore,
    old_receipt: Receipt,
    candidate: Receipt | None,
    report: ValidationReport,
    attempt_index: int,
    files: dict[str, str],
    metadata: dict[str, Any] | None = None,
) -> ReceiptRevisionAttempt:
    round_prefix = (
        f"outer_{store.current_outer_round:03d}_"
        if store.current_outer_round
        else ""
    )
    attempt = ReceiptRevisionAttempt(
        attempt_id=f"{round_prefix}receipt_v{old_receipt.version}_attempt_{attempt_index}",
        old_receipt_version=old_receipt.version,
        accepted=report.ok,
        error_codes=report.error_codes,
        candidate_receipt=candidate,
        metadata={"messages": report.messages, **(metadata or {})},
    )
    store.save_revision_attempt(
        attempt,
        {**files, "validation_report.json": json.dumps(report.to_dict(), indent=2)},
    )
    return attempt


def _skill_evidence_item(
    key: SkillKey,
    skill: EvoSkill,
    verifier_score: VerifierScore | None,
    oracle_score: OracleScore | None,
    budget: EvidenceBudgetConfig,
) -> dict[str, Any]:
    skill_md = skill.files.get("SKILL.md", "")
    entrypoint = skill.entrypoint or _first_python_file(skill.files)
    entrypoint_code = skill.files.get(entrypoint, "") if entrypoint else ""
    level = _evidence_level(skill_md, entrypoint_code, budget)
    skill_md, entrypoint_code = _apply_evidence_budget(
        skill_md, entrypoint_code, level, budget
    )
    return {
        "skill_key": {"slot": key.slot, "version": key.version},
        "key": key.token(),
        "skill_name": skill.skill_name,
        "package_manifest": sorted(skill.files),
        "skill_md": skill_md,
        "entrypoint": entrypoint,
        "entrypoint_code": entrypoint_code,
        "metadata": skill.metadata,
        "verifier_score": verifier_score.to_dict() if verifier_score else None,
        "oracle_score": public_oracle_score_payload(oracle_score),
        "evidence_level": level,
    }


def _first_python_file(files: dict[str, str]) -> str | None:
    for path in sorted(files):
        if path.endswith(".py"):
            return path
    return None


def _evidence_level(
    skill_md: str, entrypoint_code: str, budget: EvidenceBudgetConfig
) -> str:
    if budget.level != "auto":
        return budget.level
    total = len(skill_md) + len(entrypoint_code)
    if total <= budget.max_skill_chars:
        return "entrypoint"
    if total <= budget.max_total_chars:
        return "excerpt"
    return "summary"


def _apply_evidence_budget(
    skill_md: str,
    entrypoint_code: str,
    level: str,
    budget: EvidenceBudgetConfig,
) -> tuple[str, str]:
    if level in {"full", "entrypoint"}:
        return (
            skill_md[: budget.max_skill_chars],
            entrypoint_code[: budget.max_skill_chars],
        )
    if level == "excerpt":
        return _excerpt(skill_md, budget.max_excerpt_chars), _excerpt(
            entrypoint_code, budget.max_excerpt_chars
        )
    return _excerpt(skill_md, min(1000, budget.max_excerpt_chars)), ""


def _excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]\n"


def _validate_count(receipt: Receipt, errors: list[str], messages: list[str]) -> None:
    if not 5 <= len(receipt.rubrics) <= 15:
        errors.append("rubric_count_out_of_range")
        messages.append("receipt must contain 5 to 15 rubrics")


def _validate_rubrics(receipt: Receipt, errors: list[str], messages: list[str]) -> None:
    seen: set[str] = set()
    for rubric in receipt.rubrics:
        if rubric.rubric_id in seen:
            errors.append("duplicate_rubric_id")
        seen.add(rubric.rubric_id)
        if not rubric.criterion.strip():
            errors.append("empty_criterion")
        if rubric.points == 0 or rubric.points > 5 or rubric.points < -5:
            errors.append("points_out_of_range")
        if not _looks_binary(rubric.criterion):
            messages.append(f"{rubric.rubric_id}: criterion may not be binary")


def _validate_revision_delta(
    old_receipt: Receipt,
    candidate: Receipt,
    errors: list[str],
    messages: list[str],
) -> None:
    if candidate.version != old_receipt.version + 1:
        errors.append("receipt_version_must_increment_by_one")
    old_ids = {rubric.rubric_id for rubric in old_receipt.rubrics}
    new_ids = {rubric.rubric_id for rubric in candidate.rubrics}
    removed_ids = old_ids - new_ids
    declarations = candidate.removed_rubrics
    declared_ids = {item.get("rubric_id") for item in declarations if isinstance(item, dict)}
    if removed_ids != declared_ids:
        errors.append("removed_rubric_declaration_missing")
        messages.append(
            f"declared removed rubrics {sorted(declared_ids)} do not match actual removals {sorted(removed_ids)}"
        )
    for item in declarations:
        if not isinstance(item, dict):
            errors.append("removed_rubric_entry_invalid")
            continue
        rubric_id = item.get("rubric_id")
        reason = item.get("reason")
        evidence_refs = item.get("evidence_refs")
        if rubric_id not in removed_ids:
            errors.append("removed_rubric_entry_invalid")
        if not isinstance(reason, str) or not reason.strip():
            errors.append("removed_rubric_reason_missing")
        if (
            not isinstance(evidence_refs, list)
            or not evidence_refs
            or any(not isinstance(ref, str) or not ref.strip() for ref in evidence_refs)
        ):
            errors.append("removed_rubric_evidence_missing")


def _removed_rubrics_from_payload(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        return [{"_invalid_entry": value}]
    return [dict(item) if isinstance(item, dict) else {"_invalid_entry": item} for item in value]


def _receipt_version(value: Any, old_receipt: Receipt | None) -> int:
    if value is None:
        return old_receipt.version + 1 if old_receipt else 1
    if isinstance(value, bool):
        return old_receipt.version if old_receipt else 0
    try:
        numeric = float(value)
        if not numeric.is_integer():
            return old_receipt.version if old_receipt else 0
        return int(numeric)
    except (TypeError, ValueError):
        return old_receipt.version if old_receipt else 0


def _receipt_diff(old_receipt: Receipt, candidate: Receipt) -> dict[str, Any]:
    old_by_id = {item.rubric_id: item for item in old_receipt.rubrics}
    new_by_id = {item.rubric_id: item for item in candidate.rubrics}
    added = sorted(set(new_by_id) - set(old_by_id))
    removed = sorted(set(old_by_id) - set(new_by_id))
    changed = [
        {
            "rubric_id": rubric_id,
            "before": old_by_id[rubric_id].to_dict(),
            "after": new_by_id[rubric_id].to_dict(),
        }
        for rubric_id in sorted(set(old_by_id) & set(new_by_id))
        if old_by_id[rubric_id].to_dict() != new_by_id[rubric_id].to_dict()
    ]
    return {
        "version_before": old_receipt.version,
        "version_after": candidate.version,
        "added_rubric_ids": added,
        "removed_rubric_ids": removed,
        "changed_rubrics": changed,
        "removed_rubrics": candidate.removed_rubrics,
    }


def _rubric_from_payload(item: Any, index: int) -> RubricCriterion:
    if not isinstance(item, dict):
        item = {}
    return RubricCriterion(
        rubric_id=str(item.get("rubric_id") or f"r{index}"),
        category=str(item.get("category") or "General"),
        criterion=str(item.get("criterion") or ""),
        points=_integer_points(item.get("points")),
    )


def _integer_points(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return int(number) if number.is_integer() else 0


def _looks_binary(text: str) -> bool:
    return bool(text.strip()) and not re.search(
        r"\b(score|rate|grade) from \d", text, re.I
    )


def _contains_hidden_answer(receipt: Receipt) -> bool:
    text = json.dumps(receipt.to_dict(), ensure_ascii=False).lower()
    return "hidden_answer" in text or "ground_truth" in text


def similar_rule_text(left: str, right: str) -> float:
    left_tokens = set(_normalize_text(left).split())
    right_tokens = set(_normalize_text(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()
