from __future__ import annotations

from typing import Any

from .errors import LLMOutputError, VerifierError
from .llm_client import LLMClient
from .normalization import compute_raw_score, normalize_score
from .baselines.prompts import VerifierMode, build_verifier_prompt
from .ranking import assign_tie_aware_ranks, rank_verifier_scores
from .schemas import EvoSkill, Receipt, SkillKey, TaskSpec, VerifierScore


def score_skill(
    task: TaskSpec,
    skill: EvoSkill,
    receipt: Receipt,
    llm_client: LLMClient | None,
    mode: VerifierMode = "mode_a",
    skill_format: str = "code_package",
) -> VerifierScore:
    if llm_client is None:
        return fallback_score_skill(skill, receipt)
    system, user = build_verifier_prompt(
        task, skill, receipt, mode=mode, skill_format=skill_format
    )
    try:
        payload = llm_client.call_json(system, user, temperature=0.0)
        return parse_verifier_output(payload, receipt, skill.key)
    except (LLMOutputError, VerifierError) as exc:
        if skill_format == "agent_skill":
            raise
        return fallback_score_skill(skill, receipt, f"fallback_invalid_verifier_output: {exc}")


def score_skills(
    task: TaskSpec,
    skills: dict[SkillKey, EvoSkill],
    receipt: Receipt,
    llm_client: LLMClient | None,
    mode: VerifierMode = "mode_a",
    skill_format: str = "code_package",
) -> dict[SkillKey, VerifierScore]:
    scores = {
        key: score_skill(
            task,
            skill,
            receipt,
            llm_client,
            mode=mode,
            skill_format=skill_format,
        )
        for key, skill in sorted(skills.items())
    }
    ranks = assign_tie_aware_ranks(
        {key: score.normalized_score for key, score in scores.items()}
    )
    for key in rank_verifier_scores(scores):
        scores[key].rank = ranks[key]
    return scores


def parse_verifier_output(payload: dict[str, Any], receipt: Receipt, skill: SkillKey) -> VerifierScore:
    hits = _parse_criterion_hits(payload, receipt)
    raw_score = compute_raw_score(receipt, hits)
    normalized = normalize_score(raw_score, receipt.minimum_score, receipt.maximum_score)
    rationale = str(payload.get("rationale", ""))
    evidence = _parse_criterion_evidence(payload.get("criterion_evidence"), receipt)
    return VerifierScore(
        skill,
        receipt.version,
        hits,
        raw_score,
        normalized,
        rationale=rationale,
        criterion_evidence=evidence,
        positive_requirement_missing=[
            rubric.rubric_id
            for rubric in receipt.rubrics
            if rubric.points > 0 and not hits[rubric.rubric_id]
        ],
        negative_violation_present=[
            rubric.rubric_id
            for rubric in receipt.rubrics
            if rubric.points < 0 and hits[rubric.rubric_id]
        ],
    )


def fallback_score_skill(skill: EvoSkill, receipt: Receipt, rationale: str = "deterministic fallback verifier") -> VerifierScore:
    text = _skill_text(skill)
    hits = {rubric.rubric_id: _fallback_hit(text, rubric.criterion, rubric.points) for rubric in receipt.rubrics}
    raw_score = compute_raw_score(receipt, hits)
    normalized = normalize_score(raw_score, receipt.minimum_score, receipt.maximum_score)
    return VerifierScore(
        skill=skill.key,
        receipt_version=receipt.version,
        criterion_hits=hits,
        raw_score=raw_score,
        normalized_score=normalized,
        rationale=rationale,
        criterion_evidence={
            rubric.rubric_id: (
                "observable positive behavior found"
                if rubric.points > 0 and hits[rubric.rubric_id]
                else "observable positive behavior missing"
                if rubric.points > 0
                else "explicit forbidden behavior present"
                if hits[rubric.rubric_id]
                else "no explicit forbidden behavior found"
            )
            for rubric in receipt.rubrics
        },
        positive_requirement_missing=[
            rubric.rubric_id
            for rubric in receipt.rubrics
            if rubric.points > 0 and not hits[rubric.rubric_id]
        ],
        negative_violation_present=[
            rubric.rubric_id
            for rubric in receipt.rubrics
            if rubric.points < 0 and hits[rubric.rubric_id]
        ],
    )


def _parse_criterion_hits(payload: dict[str, Any], receipt: Receipt) -> dict[str, bool]:
    value = payload.get("criterion_hits")
    if isinstance(value, dict):
        return _hits_from_map(value, receipt)
    raise VerifierError("verifier output must contain criterion_hits")


def _hits_from_map(values: dict[str, Any], receipt: Receipt) -> dict[str, bool]:
    expected = {rubric.rubric_id for rubric in receipt.rubrics}
    actual = set(values)
    if actual != expected:
        raise VerifierError(f"criterion_hits keys must match receipt rubrics: missing={expected - actual}, extra={actual - expected}")
    if not all(isinstance(item, bool) for item in values.values()):
        raise VerifierError("criterion_hits values must be bool")
    return {rubric.rubric_id: bool(values[rubric.rubric_id]) for rubric in receipt.rubrics}


def _parse_criterion_evidence(
    value: Any, receipt: Receipt
) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise VerifierError("criterion_evidence must be an object when provided")
    expected = {rubric.rubric_id for rubric in receipt.rubrics}
    if set(value) - expected:
        raise VerifierError("criterion_evidence contains unknown rubric ids")
    if not all(isinstance(item, str) for item in value.values()):
        raise VerifierError("criterion_evidence values must be strings")
    return {key: str(value[key])[:500] for key in value}


def _skill_text(skill: EvoSkill) -> str:
    return "\n".join(skill.files.values()).lower()


def _fallback_hit(skill_text: str, criterion: str, points: int) -> bool:
    tokens = [token for token in _words(criterion) if len(token) >= 4]
    if not tokens:
        return points > 0
    matches = sum(1 for token in tokens if token in skill_text)
    ratio = matches / max(1, len(tokens))
    if points > 0:
        return ratio >= 0.2 or any(word in skill_text for word in ["verify", "validate", "check", "deadline"])
    return ratio >= 0.35 and not any(word in skill_text for word in ["avoid", "never", "do not", "must not"])


def _words(value: str) -> list[str]:
    return [part.strip(".,;:()[]{}'\"`").lower() for part in value.split()]
