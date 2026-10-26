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


def _direct_oracle_harness(tmp_path, monkeypatch, outcomes, *, max_rounds=3, width=3):
    """Run run_direct_oracle against a scripted oracle; outcomes are (reward, passed, total) or None for an infra failure."""
    from skilllift.coordinator import CandidateEvaluation, RewardSpec
    from skilllift.portfolio import PortfolioRef, PublicTask
    from experiments.revision2026 import optimization as module
    from skilllift_eval.runners import bounded_edits_skill_generator
    seed = tmp_path / "seed"
    (seed / "guide").mkdir(parents=True)
    (seed / "guide/SKILL.md").write_text("---\nname: guide\ndescription: Task guidance\n---\nOriginal.\n")
    script, timeline = iter(outcomes), []

    class Oracle:
        family = "task"
        last_feedback = {}

        def seed_portfolio(self, task):
            return PortfolioRef.from_directory(task_id=task, root=seed, config_hash="cfg")

        def public_task(self, task):
            return PublicTask(task, "public task")

        def reward_spec(self):
            return RewardSpec(terminal_threshold=1.0, minimum=0.0, maximum=1.0)

        def evaluate(self, task, portfolio, trial):
            outcome = next(script)
            timeline.append(("oracle", trial.phase, trial.candidate_id, portfolio.tree_hash))
            if outcome is None:
                return CandidateEvaluation.infrastructure_failure("synthetic 429")
            reward, passed, total = outcome
            rows = [{"criterion": f"c{i}", "status": "passed" if i < passed else "failed"} for i in range(total)]
            self.last_feedback = {"reward": reward, "execution_diagnostic": f"trace-{len(timeline)}", "oracle_criterion_outcomes": rows}
            return CandidateEvaluation.valid(reward, result_hash=f"r{len(timeline)}")

    class Generator:
        def __init__(self, *args, **kwargs):
            pass

        def generate(self, task, direction, parent):
            timeline.append(("generate", direction.direction_id, direction.hypothesis, parent.tree_hash))
            old = (parent.root / "guide/SKILL.md").read_text()
            new = old + f"Revision {direction.direction_id}.\n"
            return "diff --git a/guide/SKILL.md b/guide/SKILL.md\n" + "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile="a/guide/SKILL.md", tofile="b/guide/SKILL.md"))

    monkeypatch.setattr(bounded_edits_skill_generator, "BoundedEditsSkillGenerator", Generator)
    # Only the skill generator may exist; requesting a rubricator/verifier would KeyError.
    monkeypatch.setattr(module, "clients", lambda model, ledger, roles, **kwargs: {role: object() for role in roles if role == "skill_generator"} if roles == ("skill_generator",) else pytest.fail("rubric roles requested"))
    protocol = load_protocol()
    protocol["skilllift"].update(max_rounds=max_rounds, max_candidates_per_round=width)
    winner, details = module.run_direct_oracle(Oracle(), protocol, tmp_path / "run", "glm", tmp_path / "usage.jsonl")
    return winner, details, timeline, json.loads((tmp_path / "run/curve.json").read_text())


def test_direct_oracle_matches_skilllift_oracle_structure_without_rubric(tmp_path, monkeypatch):
    # Anchor fails; round 0 siblings score 0, 1, 0 -> accept the second and stop as terminal.
    winner, details, timeline, curve = _direct_oracle_harness(tmp_path, monkeypatch, [(0, 1, 4), (0, 1, 4), (1, 4, 4), (0, 2, 4)])
    kinds = [event[0] for event in timeline]
    # All K siblings are generated from the same champion before any of them is executed.
    assert kinds == ["oracle", "generate", "generate", "generate", "oracle", "oracle", "oracle"]
    generated = [event for event in timeline if event[0] == "generate"]
    assert len({event[3] for event in generated}) == 1
    assert all("trace-1" in event[2] for event in generated)  # champion's real oracle trace is the feedback
    assert "direct-r0-c0" in generated[1][2] and "direct-r0-c1" in generated[2][2]  # siblings see earlier patches
    assert details["stop_reason"] == "terminal_candidate" and details["oracle_rollouts"] == 4
    assert "Revision direct-r0-c1" in (winner / "guide/SKILL.md").read_text()
    assert curve[-1]["oracle_rollouts_cumulative"] == 4 and curve[-1]["accept_reason"] == "oracle_improved"


def test_direct_oracle_budget_is_bounded_and_uses_criterion_tiebreak(tmp_path, monkeypatch):
    # Equal reward but more criteria passed is accepted (oracle analogue of SkillLift's verifier tie-break);
    # two later rounds without improvement stop the cell at 1 + 3 x 3 oracle rollouts.
    outcomes = [(0, 1, 4)] + [(0, 2, 4), (0, 1, 4), (0, 0, 4)] + [(0, 1, 4)] * 6
    winner, details, timeline, curve = _direct_oracle_harness(tmp_path, monkeypatch, outcomes)
    assert details["stop_reason"] == "two_no_improvement_rounds" and details["rounds"] == 3
    assert details["oracle_rollouts"] == 10 == sum(event[0] == "oracle" for event in timeline)
    assert curve[1]["accept_reason"] == "criterion_tiebreak" and curve[1]["champion_criterion_pass_fraction"] == 0.5
    assert "Revision direct-r0-c0" in (winner / "guide/SKILL.md").read_text()
    # Round 1 feedback includes round 0's executed siblings, not only the champion.
    round1 = [event for event in timeline if event[0] == "generate" and event[1].startswith("direct-r1")]
    assert all("previous_round_candidates" in event[2] and "direct-r0-c2" in event[2] for event in round1)


def test_direct_oracle_retries_infrastructure_failures_on_the_same_candidate(tmp_path, monkeypatch):
    outcomes = [None, (0, 0, 2), None, (1, 2, 2), (0, 0, 2), (0, 0, 2)]
    winner, details, timeline, curve = _direct_oracle_harness(tmp_path, monkeypatch, outcomes)
    oracle_calls = [event for event in timeline if event[0] == "oracle"]
    assert [event[1] for event in oracle_calls[:2]] == ["anchor", "anchor"]
    assert oracle_calls[2][2] == oracle_calls[3][2] == "direct-r0-c0"  # retried, failure never scored
    assert details["stop_reason"] == "terminal_candidate" and details["oracle_rollouts"] == 6


def test_direct_oracle_anchor_terminal_spends_one_rollout(tmp_path, monkeypatch):
    winner, details, timeline, curve = _direct_oracle_harness(tmp_path, monkeypatch, [(1, 3, 3)])
    assert details == {"stop_reason": "anchor_terminal", "rounds": 0, "oracle_rollouts": 1, "generator_calls": 0}
    assert [event[0] for event in timeline] == ["oracle"]


def test_process_best_separates_search_from_skilllift_internal_finals():
    from experiments.revision2026.oracles import process_best
    assert process_best([("anchor", 0.2), ("candidate", 0.6), ("final", 1.0)]) == {
        "best_excluding_internal_final": 0.6, "best_including_internal_final": 1.0, "valid_executions": 3}
    assert process_best([])["best_including_internal_final"] is None


def test_process_best_report_keeps_full_denominator_and_published_row(tmp_path, monkeypatch):
    from experiments.revision2026 import oracles
    from experiments.revision2026.runner import cell_path
    from experiments.revision2026.statistics import process_best_report
    monkeypatch.setattr(oracles, "native_task_ids", lambda benchmark: ["t1", "t2"])
    protocol = load_protocol()
    protocol["models"] = ["glm"]
    write_json(tmp_path / "run_manifest.json", {"identity": {"protocol": protocol}, "config_hash": "cfg"})
    marker = cell_path(tmp_path, "skillsbench", "glm", "direct_oracle", 0, "t1") / "frozen.json"
    write_json(marker, {"training": {"process_best": {"best_excluding_internal_final": 0.5, "best_including_internal_final": 0.5}}})
    rows = process_best_report(tmp_path)
    new = next(row for row in rows if row["source"] == "this run")
    assert new["expected"] == 2 and new["observed"] == 1 and new["status"] == "incomplete"
    assert new["mean_best_including_internal_final_percent"] is None
    published = next(row for row in rows if row["status"] == "published")
    assert published["method"] == "skilllift" and published["mean_best_including_internal_final_percent"] == 75.1
    marker2 = cell_path(tmp_path, "skillsbench", "glm", "direct_oracle", 0, "t2") / "frozen.json"
    write_json(marker2, {"training": {"process_best": {"best_excluding_internal_final": 1.0, "best_including_internal_final": 1.0}}})
    new = next(row for row in process_best_report(tmp_path) if row["source"] == "this run")
    assert new["status"] == "complete" and new["mean_best_including_internal_final_percent"] == 75.0


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


def test_direct_oracle_feedback_is_trimmed_from_the_oldest_trace_first():
    from experiments.revision2026.optimization import _fit_payload
    payload = {"current_skill_feedback": {"solver_conversation": "OLD" * 1000 + "FINAL-OUTCOME", "reward": 0}}
    text, trimmed = _fit_payload(payload, 500)
    assert trimmed and len(text) <= 500 and "FINAL-OUTCOME" in text
    assert _fit_payload({"current_skill_feedback": {"solver_conversation": "short"}}, 500) == (json.dumps({"current_skill_feedback": {"solver_conversation": "short"}}), False)
