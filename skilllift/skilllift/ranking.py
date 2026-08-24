from __future__ import annotations

import itertools
import math
from typing import Any, Mapping

from .errors import RankError
from .schemas import OracleScore, Receipt, SkillKey, VerifierScore


def rank_verifier_scores(scores: dict[SkillKey, VerifierScore]) -> list[SkillKey]:
    return [
        key
        for key, _ in sorted(
            scores.items(),
            key=lambda item: (-item[1].normalized_score, item[0].slot, item[0].version),
        )
    ]


def rank_oracle_scores(scores: dict[SkillKey, OracleScore]) -> list[SkillKey]:
    return [
        key
        for key, _ in sorted(
            scores.items(),
            key=lambda item: (-item[1].oracle_score, -item[1].oracle_pass, item[0].slot, item[0].version),
        )
    ]


def spearman_rank_alignment(local_rank: list[SkillKey], oracle_rank: list[SkillKey]) -> float:
    _validate_rank_inputs(local_rank, oracle_rank)
    n = len(local_rank)
    if n < 2:
        return 1.0
    local_positions = _positions(local_rank)
    oracle_positions = _positions(oracle_rank)
    diff_sum = sum((local_positions[key] - oracle_positions[key]) ** 2 for key in local_rank)
    return 1.0 - (6.0 * diff_sum) / (n * (n * n - 1))


def kendall_rank_alignment(local_rank: list[SkillKey], oracle_rank: list[SkillKey]) -> float:
    _validate_rank_inputs(local_rank, oracle_rank)
    n = len(local_rank)
    if n < 2:
        return 1.0
    oracle_positions = _positions(oracle_rank)
    concordant = 0
    discordant = 0
    for left in range(n):
        for right in range(left + 1, n):
            a = local_rank[left]
            b = local_rank[right]
            if oracle_positions[a] < oracle_positions[b]:
                concordant += 1
            else:
                discordant += 1
    total = concordant + discordant
    return 1.0 if total == 0 else (concordant - discordant) / total


def compute_rank_alignment(
    local_rank: list[SkillKey],
    oracle_rank: list[SkillKey],
    method: str = "kendall",
) -> float:
    normalized = method.strip().lower()
    if normalized == "kendall":
        return kendall_rank_alignment(local_rank, oracle_rank)
    if normalized == "spearman":
        return spearman_rank_alignment(local_rank, oracle_rank)
    raise RankError(f"unsupported rank alignment method: {method}")


def tie_aware_rank_alignment(
    local_scores: Mapping[SkillKey, float],
    oracle_scores: Mapping[SkillKey, float],
    *,
    tolerance: float = 1e-9,
) -> float | None:
    """Compare score orderings without inventing preferences inside oracle ties.

    A ``None`` result means there was no strict pairwise signal (for example,
    every candidate received the same oracle reward). Oracle-tied pairs are
    ignored; a verifier tie on an oracle-ordered pair contributes zero. Slot or
    version ordering is deliberately never used as evidence of preference.
    """
    common = set(local_scores) & set(oracle_scores)
    if len(common) < 2:
        return None
    for key in common:
        if not math.isfinite(float(local_scores[key])) or not math.isfinite(float(oracle_scores[key])):
            raise RankError("rank scores must be finite")

    signed_agreement = 0
    comparable = 0
    for left, right in itertools.combinations(sorted(common), 2):
        local_cmp = _compare_scores(local_scores[left], local_scores[right], tolerance)
        oracle_cmp = _compare_scores(oracle_scores[left], oracle_scores[right], tolerance)
        if oracle_cmp == 0:
            continue
        comparable += 1
        if local_cmp == 0:
            continue
        if local_cmp == oracle_cmp:
            signed_agreement += 1
        else:
            signed_agreement -= 1
    if comparable == 0:
        return None
    return signed_agreement / comparable


def rank_score_groups(
    scores: Mapping[SkillKey, float], *, tolerance: float = 1e-9
) -> list[list[SkillKey]]:
    """Return deterministic descending score groups without breaking ties."""
    ordered = sorted(scores, key=lambda key: (-float(scores[key]), key))
    groups: list[list[SkillKey]] = []
    for key in ordered:
        if not groups or not math.isclose(
            float(scores[key]), float(scores[groups[-1][0]]), abs_tol=tolerance, rel_tol=0.0
        ):
            groups.append([key])
        else:
            groups[-1].append(key)
    return groups


def assign_tie_aware_ranks(
    scores: Mapping[SkillKey, float], *, tolerance: float = 1e-9
) -> dict[SkillKey, int]:
    """Assign equal rank numbers to equal score groups."""
    ranks: dict[SkillKey, int] = {}
    for group_index, group in enumerate(rank_score_groups(scores, tolerance=tolerance), start=1):
        for key in group:
            ranks[key] = group_index
    return ranks


def build_rank_contrast(
    local_scores: Mapping[SkillKey, float],
    oracle_scores: Mapping[SkillKey, float],
    *,
    tolerance: float = 1e-9,
) -> dict[str, Any]:
    """Serialize the public ranking evidence used by the rubricator."""
    local_groups = rank_score_groups(local_scores, tolerance=tolerance)
    oracle_groups = rank_score_groups(oracle_scores, tolerance=tolerance)

    def tokens(groups: list[list[SkillKey]]) -> list[list[str]]:
        return [[key.token() for key in group] for group in groups]

    def tiers(groups: list[list[SkillKey]], scores: Mapping[SkillKey, float]) -> list[dict[str, Any]]:
        return [
            {
                "score": float(scores[group[0]]),
                "skills": [key.token() for key in group],
            }
            for group in groups
        ]

    local_positions = {
        key: index for index, group in enumerate(local_groups) for key in group
    }
    oracle_positions = {
        key: index for index, group in enumerate(oracle_groups) for key in group
    }
    group_mismatches = [
        {
            "skill": key.to_dict(),
            "verifier_group": local_positions[key] + 1,
            "oracle_group": oracle_positions[key] + 1,
        }
        for key in sorted(set(local_positions) & set(oracle_positions))
        if local_positions[key] != oracle_positions[key]
    ]
    pairwise_differences = []
    for left, right in itertools.combinations(sorted(set(local_scores) & set(oracle_scores)), 2):
        oracle_cmp = _compare_scores(oracle_scores[left], oracle_scores[right], tolerance)
        if oracle_cmp == 0:
            continue
        preferred, other = (left, right) if oracle_cmp > 0 else (right, left)
        verifier_cmp = _compare_scores(
            local_scores[preferred], local_scores[other], tolerance
        )
        if verifier_cmp <= 0:
            pairwise_differences.append(
                {
                    "oracle_preferred": preferred.token(),
                    "oracle_lower": other.token(),
                    "verifier_relation": "tied" if verifier_cmp == 0 else "reversed",
                }
            )

    oracle_has_signal = len(oracle_groups) > 1
    oracle_payload = {
        "oracle_rank_groups": tokens(oracle_groups),
        "tiers": {
            "top": tokens(oracle_groups[:1]) if oracle_has_signal else [],
            "mid": tokens(oracle_groups[1:-1]) if oracle_has_signal and len(oracle_groups) > 2 else [],
            "bottom": tokens(oracle_groups[-1:]) if oracle_has_signal else [],
        },
        "oracle_has_signal": oracle_has_signal,
    }
    return {
        "verifier_rank_groups": tokens(local_groups),
        "oracle_rank_groups": tokens(oracle_groups),
        "verifier_tiers": tiers(local_groups, local_scores),
        "oracle_tiers": tiers(oracle_groups, oracle_scores),
        "oracle_has_signal": oracle_has_signal,
        "oracle_contrast": oracle_payload,
        "tiers": oracle_payload["tiers"],
        "rank_mismatches": group_mismatches,
        "pairwise_differences": pairwise_differences,
        "alignment": tie_aware_rank_alignment(
            local_scores, oracle_scores, tolerance=tolerance
        ),
    }


def build_criterion_contrast(
    scores: Mapping[SkillKey, VerifierScore], receipt: Receipt
) -> dict[str, Any]:
    """Group visible verifier criterion outcomes by candidate.

    This is diagnostic evidence for the rubricator, not a hidden evaluator
    signal. Positive and negative criteria are kept in separate buckets so a
    missing merit cannot be confused with a present violation.
    """
    result: dict[str, Any] = {}
    for rubric in receipt.rubrics:
        present = [
            key.token()
            for key, score in sorted(scores.items())
            if score.criterion_hits.get(rubric.rubric_id, False)
        ]
        absent = [
            key.token()
            for key, score in sorted(scores.items())
            if not score.criterion_hits.get(rubric.rubric_id, False)
        ]
        result[rubric.rubric_id] = {
            "points": rubric.points,
            "positive_requirement_present": present if rubric.points > 0 else [],
            "positive_requirement_missing": absent if rubric.points > 0 else [],
            "negative_violation_present": present if rubric.points < 0 else [],
            "negative_violation_absent": absent if rubric.points < 0 else [],
        }
    return result


def _compare_scores(left: float, right: float, tolerance: float) -> int:
    if math.isclose(float(left), float(right), abs_tol=tolerance, rel_tol=0.0):
        return 0
    return 1 if float(left) > float(right) else -1


def find_rank_mismatches(local_rank: list[SkillKey], oracle_rank: list[SkillKey]) -> list[dict[str, Any]]:
    _validate_rank_inputs(local_rank, oracle_rank)
    local_positions = _positions(local_rank)
    oracle_positions = _positions(oracle_rank)
    mismatches: list[dict[str, Any]] = []
    for key in sorted(local_rank):
        local_position = local_positions[key]
        oracle_position = oracle_positions[key]
        if local_position != oracle_position:
            mismatches.append(
                {
                    "skill": key.to_dict(),
                    "local_rank": local_position + 1,
                    "oracle_rank": oracle_position + 1,
                    "delta": oracle_position - local_position,
                }
            )
    return mismatches


def _positions(rank: list[SkillKey]) -> dict[SkillKey, int]:
    return {key: index for index, key in enumerate(rank)}


def _validate_rank_inputs(local_rank: list[SkillKey], oracle_rank: list[SkillKey]) -> None:
    if len(local_rank) != len(oracle_rank):
        raise RankError("rank lengths differ")
    if set(local_rank) != set(oracle_rank):
        raise RankError("rank skill keys differ")
