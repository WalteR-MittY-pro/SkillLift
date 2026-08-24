import pytest

from skilllift.errors import RankError
from skilllift.normalization import compute_raw_score, normalize_score, recompute_receipt_bounds
from skilllift.ranking import (
    compute_rank_alignment,
    assign_tie_aware_ranks,
    build_criterion_contrast,
    find_rank_mismatches,
    kendall_rank_alignment,
    rank_oracle_scores,
    rank_verifier_scores,
    spearman_rank_alignment,
    tie_aware_rank_alignment,
)
from skilllift.schemas import OracleScore, Receipt, RubricCriterion, SkillKey, VerifierScore


def _receipt() -> Receipt:
    return recompute_receipt_bounds(
        Receipt(
            version=1,
            rubrics=[
                RubricCriterion("r1", "Merit", "Good", 4),
                RubricCriterion("r2", "Flaw", "Bad", -2),
                RubricCriterion("r3", "Merit", "Clear", 1),
            ],
            maximum_score=0,
            minimum_score=0,
        )
    )


def test_signed_points_and_normalization() -> None:
    receipt = _receipt()
    raw = compute_raw_score(receipt, {"r1": True, "r2": True, "r3": False})
    assert raw == 2
    assert normalize_score(raw, receipt.minimum_score, receipt.maximum_score) == pytest.approx(4 / 7)
    assert normalize_score(3, 3, 3) == 0.0


def test_rank_alignment_methods() -> None:
    keys = [SkillKey(0, 1), SkillKey(1, 1), SkillKey(2, 1)]
    assert kendall_rank_alignment(keys, keys) == 1.0
    assert spearman_rank_alignment(keys, keys) == 1.0
    assert kendall_rank_alignment(keys, list(reversed(keys))) < 0
    assert spearman_rank_alignment(keys, list(reversed(keys))) < 0
    assert compute_rank_alignment(keys, keys) == 1.0


def test_tie_aware_alignment_ignores_pairs_tied_on_either_side() -> None:
    keys = [SkillKey(index, 1) for index in range(3)]
    verifier = {keys[0]: 0.8, keys[1]: 0.8, keys[2]: 0.2}
    oracle = {keys[0]: 0.7, keys[1]: 0.7, keys[2]: 0.1}

    assert tie_aware_rank_alignment(verifier, oracle) == 1.0


def test_tie_aware_ranks_share_rank_number() -> None:
    keys = [SkillKey(index, 1) for index in range(3)]

    assert assign_tie_aware_ranks({keys[0]: 1.0, keys[1]: 1.0, keys[2]: 0.5}) == {
        keys[0]: 1,
        keys[1]: 1,
        keys[2]: 2,
    }


def test_tie_aware_alignment_returns_none_when_every_pair_is_tied() -> None:
    keys = [SkillKey(index, 1) for index in range(3)]
    scores = {key: 0.5 for key in keys}

    assert tie_aware_rank_alignment(scores, scores) is None


def test_verifier_tie_contributes_zero_against_strict_oracle_order() -> None:
    keys = [SkillKey(index, 1) for index in range(3)]
    verifier = {keys[0]: 0.8, keys[1]: 0.8, keys[2]: 0.2}
    oracle = {keys[0]: 0.9, keys[1]: 0.7, keys[2]: 0.1}

    assert tie_aware_rank_alignment(verifier, oracle) == pytest.approx(2 / 3)


def test_criterion_contrast_separates_positive_missing_and_negative_present() -> None:
    receipt = _receipt()
    a, b = SkillKey(0, 1), SkillKey(1, 1)
    scores = {
        a: VerifierScore(a, 1, {"r1": False, "r2": True, "r3": True}, 0, 0.0),
        b: VerifierScore(b, 1, {"r1": True, "r2": False, "r3": True}, 0, 0.0),
    }

    contrast = build_criterion_contrast(scores, receipt)

    assert contrast["r1"]["positive_requirement_missing"] == ["s000_v001"]
    assert contrast["r2"]["negative_violation_present"] == ["s000_v001"]


def test_rank_inputs_must_have_same_skill_keys() -> None:
    with pytest.raises(RankError):
        kendall_rank_alignment([SkillKey(0, 1)], [SkillKey(1, 1)])


def test_score_rank_and_mismatches() -> None:
    a, b = SkillKey(0, 1), SkillKey(1, 1)
    verifier_scores = {
        a: VerifierScore(a, 1, {}, 0, 0.2),
        b: VerifierScore(b, 1, {}, 0, 0.8),
    }
    oracle_scores = {
        a: OracleScore(a, 0.9, 1),
        b: OracleScore(b, 0.3, 0),
    }
    local_rank = rank_verifier_scores(verifier_scores)
    oracle_rank = rank_oracle_scores(oracle_scores)
    assert local_rank == [b, a]
    assert oracle_rank == [a, b]
    assert find_rank_mismatches(local_rank, oracle_rank)[0]["delta"] != 0
