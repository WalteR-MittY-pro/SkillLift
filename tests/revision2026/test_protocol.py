import json

import pytest

from experiments.revision2026.common import load_protocol, require_frozen, tree_hash, verify_seeds, write_json


def test_committed_seed_artifacts_are_exactly_the_frozen_twenty():
    manifest = verify_seeds()
    assert manifest["family_count"] == 20
    assert len({row["family"] for row in manifest["artifacts"]}) == 20


def test_completed_cell_cannot_resume_with_modified_skill(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    skill = skills / "SKILL.md"
    skill.write_text("original")
    marker = tmp_path / "frozen.json"
    write_json(marker, {"status": "complete", "skill_hash": tree_hash(skills)})
    require_frozen(marker)
    skill.write_text("different")
    with pytest.raises(ValueError, match="modified"):
        require_frozen(marker)


def test_protocol_refuses_training_on_a_heldout_instance(tmp_path):
    protocol = load_protocol()
    protocol["skilllearnbench"]["train_instance"] = 2
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(protocol))
    with pytest.raises(ValueError, match="instance 1"):
        load_protocol(path)
