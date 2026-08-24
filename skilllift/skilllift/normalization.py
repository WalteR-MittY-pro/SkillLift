from __future__ import annotations

from .schemas import Receipt, RubricCriterion


def compute_score_bounds(rubrics: list[RubricCriterion]) -> tuple[int, int]:
    maximum = sum(item.points for item in rubrics if item.points > 0)
    minimum = sum(item.points for item in rubrics if item.points < 0)
    return minimum, maximum


def compute_raw_score(receipt: Receipt, criterion_hits: dict[str, bool]) -> int:
    raw_score = 0
    for rubric in receipt.rubrics:
        if criterion_hits.get(rubric.rubric_id, False):
            raw_score += rubric.points
    return raw_score


def normalize_score(raw_score: int, minimum_score: int, maximum_score: int) -> float:
    if maximum_score == minimum_score:
        return 0.0
    normalized = (raw_score - minimum_score) / (maximum_score - minimum_score)
    return max(0.0, min(1.0, float(normalized)))


def recompute_receipt_bounds(receipt: Receipt) -> Receipt:
    minimum, maximum = compute_score_bounds(receipt.rubrics)
    return Receipt(
        version=receipt.version,
        rubrics=receipt.rubrics,
        maximum_score=maximum,
        minimum_score=minimum,
        baseline_score=receipt.baseline_score,
        metadata=dict(receipt.metadata),
        removed_rubrics=list(receipt.removed_rubrics),
    )
