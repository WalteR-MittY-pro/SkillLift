import pytest

from experiments.revision2026.statistics import holm, paired_inference

SETTINGS = {"random_seed": 1, "bootstrap_repeats": 200, "permutation_repeats": 500, "confidence": .95}


def observations(values):
    return [{"family": f"f{i}", "task_id": f"f{i}-2", "evolution_run": r, "repeat": 0,
             "reward": value, "status": "OK", "phase": "heldout"} for i, value in enumerate(values) for r in range(3)]


def test_identical_methods_have_zero_difference_and_p_one():
    rows = observations([0, 1, .5, 1])
    result = paired_inference(rows, rows, SETTINGS)
    assert result["difference_pp"] == 0
    assert result["difference_ci95"] == [0, 0]
    assert result["p_raw"] == 1
    assert result["families"] == 4  # not twelve independent tasks


def test_missing_or_duplicate_trials_are_rejected():
    rows = observations([0, 1, .5])
    with pytest.raises(ValueError, match="coverage"):
        paired_inference(rows, rows[:-1], SETTINGS)
    with pytest.raises(ValueError, match="Duplicate"):
        paired_inference(rows + [rows[0]], rows, SETTINGS)


def test_holm_preserves_original_order_and_planned_family():
    assert holm([.04, .01, 1.0]) == pytest.approx([.08, .03, 1.0])


def test_pilot_cannot_enter_inferential_table():
    rows = observations([0, 1])
    rows[0]["phase"] = "pilot"
    with pytest.raises(ValueError, match="independent"):
        paired_inference(rows, rows, SETTINGS)
