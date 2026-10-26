from types import SimpleNamespace
import difflib
import json

import pytest

from experiments.revision2026.common import load_protocol, write_json
from experiments.revision2026.runner import evaluation_identity, evaluation_path, seal_evaluation_plan
from experiments.revision2026.statistics import summarize


def spec(root, task="a/b"):
    return {"kind": "evaluate", "benchmark": "skilllearnbench", "model": "glm", "method": "skilllift", "run": 0,
            "family": "a", "task_id": task, "repeat": 0, "phase": "heldout", "root": str(root), "config_hash": "config"}


def test_task_identifiers_cannot_collide_on_disk(tmp_path):
    assert evaluation_path(spec(tmp_path, "a/b")) != evaluation_path(spec(tmp_path, "a__b"))
    assert evaluation_path(spec(tmp_path, "a%2Fb")) != evaluation_path(spec(tmp_path, "a/b"))


def test_duplicate_evaluations_and_shrunken_rosters_are_rejected(tmp_path):
    one, two = spec(tmp_path, "a/2"), spec(tmp_path, "a/3")
    with pytest.raises(ValueError, match="Duplicate"):
        seal_evaluation_plan(tmp_path, [one, one])
    seal_evaluation_plan(tmp_path, [one, two])
    with pytest.raises(ValueError, match="roster changed"):
        seal_evaluation_plan(tmp_path, [one])


def test_missing_selected_results_do_not_shrink_expected_denominator(tmp_path):
    one, two = spec(tmp_path, "a/2"), spec(tmp_path, "a/3")
    seal_evaluation_plan(tmp_path, [one, two])
    # One record is present and every worker status file could be lost. The sealed
    # roster still requires two observations and inference must remain absent.
    write_json(evaluation_path(one) / "selected.json", {"status": "OK", "reward": 1, "config_hash": "config", "cell_identity": evaluation_identity(one)})
    rows = summarize(tmp_path, load_protocol()["statistics"])
    assert rows[0]["expected"] == 2 and rows[0]["observed_valid"] == 1
    assert rows[0]["ci95"] is None


def test_failed_verifier_does_not_become_a_valid_reward(tmp_path, monkeypatch):
    from experiments.revision2026 import docker_agent as module
    task = tmp_path / "task"
    task.mkdir()
    (task / "instruction.md").write_text("Do the task")
    output = tmp_path / "trial"
    timeline = []

    def execute(args, **kwargs):
        timeline.append(args)
        if args[:2] == ["docker", "run"] and "/tests/test.sh" in args:
            (output / "verifier/reward.txt").write_text("1")
            return SimpleNamespace(returncode=1, stdout="", stderr="crashed")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module, "endpoint", lambda _: {"MODEL": "fake"})
    monkeypatch.setattr(module, "base_image", lambda *a: "base")
    monkeypatch.setattr(module, "command", execute)
    monkeypatch.setattr(module, "run_solver", lambda *a: ("completed", []))
    instance = SimpleNamespace(task_id="task/1", family="task", number=1, path=task)
    result = module.evaluate(instance, None, "glm", {}, output, tmp_path / "usage.jsonl", tmp_path)
    assert result["status"] == "ERROR" and result["reward"] is None
    stop = next(i for i, args in enumerate(timeline) if args[:2] == ["docker", "stop"])
    score = next(i for i, args in enumerate(timeline) if "/tests/test.sh" in args)
    assert stop < score


def test_direct_oracle_edits_use_real_feedback_without_rubric_calls(tmp_path, monkeypatch):
    from skilllift.coordinator import CandidateEvaluation
    from skilllift.portfolio import PortfolioRef, PublicTask
    from experiments.revision2026 import optimization as module
    from skilllift_eval.runners import bounded_edits_skill_generator
    seed = tmp_path / "seed"
    (seed / "guide").mkdir(parents=True)
    (seed / "guide/SKILL.md").write_text("---\nname: guide\ndescription: Task guidance\n---\nOriginal.\n")
    feedback_seen, rewards = [], iter([0, .5, 1])

    class Oracle:
        family = "task"
        last_feedback = {}

        def seed_portfolio(self, task):
            return PortfolioRef.from_directory(task_id=task, root=seed, config_hash="cfg")

        def public_task(self, task):
            return PublicTask(task, "public task")

        def evaluate(self, task, portfolio, trial):
            value = next(rewards)
            self.last_feedback = {"execution_diagnostic": f"observed-{value}"}
            return CandidateEvaluation.valid(value, result_hash=str(value))

    class Generator:
        def __init__(self, *args, **kwargs):
            pass

        def generate(self, task, direction, parent):
            feedback_seen.append(direction.hypothesis)
            old = (parent.root / "guide/SKILL.md").read_text()
            new = old + f"Revision {len(feedback_seen)}.\n"
            return "diff --git a/guide/SKILL.md b/guide/SKILL.md\n" + "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile="a/guide/SKILL.md", tofile="b/guide/SKILL.md"))

    monkeypatch.setattr(bounded_edits_skill_generator, "BoundedEditsSkillGenerator", Generator)
    # Accessing a rubricator/verifier here would fail the test.
    monkeypatch.setattr(module, "clients", lambda *a: {"skill_generator": object()})
    protocol = load_protocol()
    protocol["skilllift"].update(max_rounds=1, max_candidates_per_round=1)
    winner, details = module.run_direct_oracle(Oracle(), protocol, tmp_path / "run", "glm", tmp_path / "usage.jsonl")
    assert "observed-0" in feedback_seen[0] and "observed-0.5" in feedback_seen[1]
    assert len(feedback_seen) == 2 and details["stop_reason"] == "terminal_candidate"
    assert "Revision 2" in (winner / "guide/SKILL.md").read_text()


def test_skillopt_configuration_never_serializes_role_secrets(tmp_path):
    from experiments.revision2026.optimization import skillopt_config
    secret = "synthetic-test-only-credential"
    cfg = skillopt_config(load_protocol(), tmp_path, tmp_path / "SKILL.md", {"MODEL": "fake", "BASE_URL": "https://example.test/v1", "API_KEY": secret}, 0)
    assert secret not in json.dumps(cfg)
    assert not any("api_key" in key for key in cfg)
    assert cfg["eval_test"] is False


def test_native_api_failure_is_not_a_valid_zero_but_solver_budget_is(tmp_path):
    from skilllift.coordinator import CandidateEvaluation
    from experiments.revision2026.oracles import checked_native_result
    raw, marker = tmp_path / "result.json", tmp_path / "marker.json"
    write_json(marker, {"result_path": str(raw)})
    evaluation = CandidateEvaluation.valid(0, result_hash="raw", artifact_ref=str(marker))
    write_json(raw, {"error": {"exception_type": "AuthenticationError"}})
    assert not checked_native_result(evaluation).is_valid
    write_json(raw, {"error": {"exception_type": "AgentTimeoutError"}})
    assert checked_native_result(evaluation).is_valid


@pytest.mark.parametrize("field", ["judge_error", "llm_judge_error", "llm_error"])
def test_wildclaw_judge_api_failure_is_rejected_even_with_numeric_reward(tmp_path, field):
    from skilllift.coordinator import CandidateEvaluation
    from experiments.revision2026.oracles import checked_native_result
    raw, marker = tmp_path / "score.json", tmp_path / "marker.json"
    write_json(marker, {"score_path": str(raw)})
    evaluation = CandidateEvaluation.valid(0, result_hash="raw", artifact_ref=str(marker))
    write_json(raw, {"scores": {"overall_score": 0, field: "API 400, unsupported image content"}})
    assert not checked_native_result(evaluation).is_valid
    # This is a penalized agent outcome in the task, not a judge failure.
    write_json(raw, {"scores": {"overall_score": .9, "trap_errors": 1}})
    assert checked_native_result(evaluation).is_valid
