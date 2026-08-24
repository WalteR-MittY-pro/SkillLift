from skilllift.persistence import ExperimentStore, read_json, read_jsonl
from skilllift.schemas import (
    SkillLiftConfig,
    EvoSkill,
    OracleScore,
    Receipt,
    ReceiptRevisionAttempt,
    RoundState,
    RubricCriterion,
    SkillKey,
    TaskSpec,
    VerifierScore,
)


def _receipt() -> Receipt:
    return Receipt(1, [RubricCriterion("r1", "Planning", "Has plan", 1)], 1, 0)


def _skill() -> EvoSkill:
    return EvoSkill(SkillKey(0, 1), "demo", {"SKILL.md": "x\n", "a.py": "print(1)\n"}, "a.py")


def test_experiment_store_writes_all_core_artifacts(tmp_path) -> None:
    store = ExperimentStore(tmp_path / "exp")
    task = TaskSpec("task", "body", "task.md")
    skill = _skill()
    receipt = _receipt()
    verifier = VerifierScore(skill.key, 1, {"r1": True}, 1, 1.0)
    oracle = OracleScore(skill.key, 0.9, 1)
    state = RoundState(0, 0, "mode_a", 0, {skill.key: skill}, receipt)

    store.save_config(SkillLiftConfig(exp_name="x"))
    store.save_task_spec(task)
    store.save_receipt(receipt, "receipt_r0")
    store.save_skill(skill, "round_0")
    store.save_verifier_scores({skill.key: verifier}, "round_0")
    store.save_oracle_scores({skill.key: oracle}, "round_0")
    store.save_round_state(state)
    store.save_revision_attempt(ReceiptRevisionAttempt("attempt_0", 1, False, ["bad"]), {"prompt.txt": "fix"})
    store.append_event({"type": "x"})
    store.append_event({"type": "y"})
    store.save_summary({"best": "demo"}, "# Summary")

    assert read_json(tmp_path / "exp" / "config.json")["exp_name"] == "x"
    assert (tmp_path / "exp" / "task_spec.json").exists()
    assert (tmp_path / "exp" / "receipts" / "receipt_r0.json").exists()
    assert list((tmp_path / "exp" / "skills" / "round_0").glob("*.json"))
    assert (tmp_path / "exp" / "verifier_scores" / "round_0.json").exists()
    assert (tmp_path / "exp" / "oracle_scores" / "round_0.json").exists()
    assert (tmp_path / "exp" / "round_states" / "step_000_mode_a.json").exists()
    assert (tmp_path / "exp" / "receipt_revision_attempts" / "attempt_0" / "prompt.txt").exists()
    assert [row["type"] for row in read_jsonl(tmp_path / "exp" / "events.jsonl")] == ["x", "y"]
    assert (tmp_path / "exp" / "summary.md").read_text().endswith("\n")


def test_reset_new_run_removes_previous_artifacts(tmp_path) -> None:
    store = ExperimentStore(tmp_path / "exp")
    store.save_summary({"x": 1}, "summary")
    store.reset_new_run()
    assert store.exp_dir.exists()
    assert not any(store.exp_dir.iterdir())
