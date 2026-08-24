from argparse import Namespace

from skilllift_eval.runners.skilllift_thresholds import (
    probability_param,
    sanitize_probability_fields,
)


def test_probability_param_falls_back_and_records_invalid_value() -> None:
    warnings = []

    value = probability_param(
        {"rank_alignment_threshold": 1.5},
        "rank_alignment_threshold",
        0.9,
        warning_sink=warnings.append,
    )

    assert value == 0.9
    assert warnings == [
        {
            "event": "invalid_threshold_fallback",
            "field": "rank_alignment_threshold",
            "raw_value": 1.5,
            "fallback_value": 0.9,
            "valid_range": [0.0, 1.0],
            "reason": "SkillLift normalized thresholds must be in [0, 1]",
        }
    ]


def test_sanitize_probability_fields_updates_target_and_writes_warning(tmp_path) -> None:
    args = Namespace(oracle_threshold=1.5)

    warnings = sanitize_probability_fields(
        args, tmp_path, {"oracle_threshold": 0.8}
    )

    assert args.oracle_threshold == 0.8
    assert len(warnings) == 1
    assert (tmp_path / "threshold_warnings.jsonl").is_file()
