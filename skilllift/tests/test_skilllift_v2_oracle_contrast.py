from skilllift.oracle_contrast import build_oracle_contrast, has_signal, public_oracle_score_payload
from skilllift.schemas import OracleFeedback, OracleScore, SkillKey


def _score(slot: int, score: float) -> tuple[SkillKey, OracleScore]:
    key = SkillKey(slot, 1)
    feedback = OracleFeedback(metadata={"failure_type": "success"})
    return key, OracleScore(key, score, 1 if score >= 0.9 else 0, feedback=feedback, output_dir="/private/run")


def test_has_signal_detects_tie() -> None:
    assert has_signal({}) is False
    assert has_signal(dict([_score(0, 0.5)])) is False
    assert has_signal(dict([_score(0, 0.5), _score(1, 0.5)])) is False
    assert has_signal(dict([_score(0, 0.0), _score(1, 0.0)])) is False
    assert has_signal(dict([_score(0, 0.5), _score(1, 0.8)])) is True


def test_public_oracle_score_payload_drops_feedback_details() -> None:
    key, score = _score(0, 0.5)
    score.feedback.stderr_excerpt = "SECRET_LOG"
    score.feedback.traceback_summary = "SECRET_TRACEBACK"
    score.feedback.metadata["chat_summary"] = "SECRET_CHAT"
    score.feedback.metadata["skill_observation"] = {"skill_key": "s000_v001"}

    payload = public_oracle_score_payload(score)

    assert payload == {
        "oracle_score": 0.5,
        "oracle_pass": 0,
        "rank": -1,
    }
    text = str(payload)
    assert "skill_observation" not in text
    assert "SECRET_LOG" not in text
    assert "SECRET_TRACEBACK" not in text
    assert "SECRET_CHAT" not in text
    assert "output_dir" not in text
    assert key.token() == "s000_v001"


def test_oracle_contrast_builds_high_low_tie_groups_without_private_paths() -> None:
    scores = dict(
        [
            _score(0, 0.95),
            _score(1, 0.2),
            _score(2, 0.2),
        ]
    )

    payload = build_oracle_contrast(scores)
    contrast = payload["oracle_contrast"]

    assert contrast["high_scoring_skills"] == ["s000_v001"]
    assert contrast["low_scoring_skills"] == ["s001_v001", "s002_v001"]
    assert contrast["tie_groups"] == [["s001_v001", "s002_v001"]]
    assert contrast["oracle_has_signal"] is True
    assert contrast["oracle_rank_groups"] == [["s000_v001"], ["s001_v001", "s002_v001"]]
    assert contrast["tiers"] == {
        "top": [["s000_v001"]],
        "mid": [],
        "bottom": [["s001_v001", "s002_v001"]],
    }
    assert "/private/run" not in str(payload)


def test_oracle_contrast_builds_tiers_for_four_distinct_scores() -> None:
    scores = dict(
        [
            _score(0, 0.95),
            _score(1, 0.62),
            _score(2, 0.58),
            _score(3, 0.1),
        ]
    )

    contrast = build_oracle_contrast(scores)["oracle_contrast"]

    assert contrast["tiers"] == {
        "top": [["s000_v001"]],
        "mid": [
            ["s001_v001"],
            ["s002_v001"],
        ],
        "bottom": [["s003_v001"]],
    }


def test_oracle_contrast_keeps_equal_score_groups_together() -> None:
    scores = dict(
        [
            _score(0, 0.9),
            _score(1, 0.9),
            _score(2, 0.5),
            _score(3, 0.5),
            _score(4, 0.5),
            _score(5, 0.1),
        ]
    )

    contrast = build_oracle_contrast(scores)["oracle_contrast"]

    assert contrast["tiers"] == {
        "top": [["s000_v001", "s001_v001"]],
        "mid": [["s002_v001", "s003_v001", "s004_v001"]],
        "bottom": [["s005_v001"]],
    }


def test_oracle_contrast_all_tie_does_not_create_false_differences() -> None:
    scores = dict([_score(0, 0.5), _score(1, 0.5)])

    payload = build_oracle_contrast(scores)

    assert payload["oracle_contrast"]["high_scoring_skills"] == []
    assert payload["oracle_contrast"]["low_scoring_skills"] == []
    assert payload["oracle_contrast"]["tie_groups"] == [["s000_v001", "s001_v001"]]
    assert payload["oracle_contrast"]["oracle_has_signal"] is False
    assert payload["oracle_contrast"]["oracle_rank_groups"] == [["s000_v001", "s001_v001"]]
    assert payload["oracle_contrast"]["tiers"] == {"top": [], "mid": [], "bottom": []}
