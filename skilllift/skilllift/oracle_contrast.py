from __future__ import annotations

from typing import Any

from .schemas import OracleScore, SkillKey


def public_oracle_score_payload(score: OracleScore | None) -> dict[str, Any] | None:
    if score is None:
        return None
    return {
        "oracle_score": score.oracle_score,
        "oracle_pass": score.oracle_pass,
        "rank": score.rank,
    }


def has_signal(scores: dict[SkillKey, OracleScore]) -> bool:
    """True iff oracle scores are not all tied across skills.

    Uses oracle_score alone — oracle_pass is derived from oracle_threshold
    (absolute-level dimension) and is intentionally excluded from this
    discrimination check.
    """
    if len(scores) < 2:
        return False
    distinct_scores = {_score_key(score.oracle_score) for score in scores.values()}
    return len(distinct_scores) >= 2


def public_oracle_scores_payload(scores: dict[SkillKey, OracleScore]) -> dict[str, Any]:
    return {
        key.token(): public_oracle_score_payload(score)
        for key, score in sorted(scores.items())
    }


def build_oracle_contrast(scores: dict[SkillKey, OracleScore]) -> dict[str, Any]:
    """Build the high/low/tie contrast across oracle scores.

    No per-skill execution observation is propagated: receipt revision is driven
    by oracle score ranking + verifier rank mismatch alone, in line with the
    tau2 oracle-free design.

    oracle_rank_groups is grouped, not linear: each inner list is a tie group
    (same oracle_score), groups are ordered best-first. Within a group, the
    token order is display-only and carries no preference.
    """
    oracle_has_signal = has_signal(scores)
    if not scores:
        return {
            "oracle_contrast": {
                "high_scoring_skills": [],
                "low_scoring_skills": [],
                "tie_groups": [],
                "oracle_has_signal": oracle_has_signal,
                "oracle_rank_groups": [],
                "tiers": _empty_tiers(),
            },
        }
    ordered = sorted(scores.items())
    rank_groups = _rank_groups(ordered)
    tie_groups = [group for group in rank_groups if len(group) > 1]
    tiers = _tier_rank_groups(rank_groups, oracle_has_signal)
    if not oracle_has_signal:
        high: list[str] = []
        low: list[str] = []
    else:
        high = rank_groups[0]
        low = rank_groups[-1]
    return {
        "oracle_contrast": {
            "high_scoring_skills": high,
            "low_scoring_skills": low,
            "tie_groups": tie_groups,
            "oracle_has_signal": oracle_has_signal,
            "oracle_rank_groups": rank_groups,
            "tiers": tiers,
        },
    }


def _rank_groups(items: list[tuple[SkillKey, OracleScore]]) -> list[list[str]]:
    """Group tokens by oracle_score, ordered best-first.

    Each inner list is sorted by token for deterministic output — the
    within-group order is display-only and carries no preference.
    """
    by_score: dict[float, list[str]] = {}
    for key, score in items:
        by_score.setdefault(_score_key(score.oracle_score), []).append(key.token())
    return [
        sorted(by_score[score_value])
        for score_value in sorted(by_score.keys(), reverse=True)
    ]


def _score_key(score: float) -> float:
    return round(score, 6)


def _empty_tiers() -> dict[str, list[list[str]]]:
    return {"top": [], "mid": [], "bottom": []}


def _tier_rank_groups(
    rank_groups: list[list[str]],
    oracle_has_signal: bool,
) -> dict[str, list[list[str]]]:
    group_count = len(rank_groups)
    if not oracle_has_signal or group_count <= 1:
        return _empty_tiers()
    if group_count == 2:
        return {
            "top": rank_groups[:1],
            "mid": [],
            "bottom": rank_groups[1:],
        }
    edge_count = max(1, group_count // 3)
    return {
        "top": rank_groups[:edge_count],
        "mid": rank_groups[edge_count:-edge_count],
        "bottom": rank_groups[-edge_count:],
    }
