from __future__ import annotations

import json
from pathlib import Path

from skilllift_eval.runners.skilllift_skillsbench import load_valid_marker, skill_payload_hash, write_json_atomic


def _payload() -> dict:
    skill = {
        "key": {"slot": 0, "version": 1},
        "skill_name": "skilllift-demo",
        "files": {"SKILL.md": "---\nname: skilllift-demo\ndescription: Demo.\n---\n# Demo\n"},
        "entrypoint": None,
        "metadata": {},
    }
    return {
        "config_hash": "config-a",
        "skills": {"s000_v001": skill},
        "skill_hashes": {"s000_v001": skill_payload_hash(skill)},
    }


def test_round_marker_requires_matching_config_and_skill_hashes(tmp_path: Path) -> None:
    path = tmp_path / "round_result.json"
    write_json_atomic(path, _payload())

    assert load_valid_marker(path, "config-a") is not None
    assert load_valid_marker(path, "config-b") is None

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["skills"]["s000_v001"]["files"]["SKILL.md"] += "changed"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_valid_marker(path, "config-a") is None


def test_corrupt_marker_is_not_a_resume_boundary(tmp_path: Path) -> None:
    path = tmp_path / "round_result.json"
    path.write_text("{broken", encoding="utf-8")

    assert load_valid_marker(path, "config-a") is None
