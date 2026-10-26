import json
from types import SimpleNamespace

import pytest

from experiments.revision2026 import resilience
from experiments.revision2026.common import load_protocol, tree_hash, write_json


class HTTPLike(Exception):
    def __init__(self, status, text=""):
        super().__init__(f"Error code: {status} {text}")
        self.status_code = status


@pytest.mark.parametrize("error", [HTTPLike(429), HTTPLike(404), HTTPLike(408), HTTPLike(503), TimeoutError("read timed out"),
                                   "LLM call failed: HTTP Error 429: Too Many Requests", "Connection reset by peer",
                                   "APIConnectionError: Connection error."])
def test_transient_failures_are_retryable(error):
    assert resilience.is_transient(error)


@pytest.mark.parametrize("error", [HTTPLike(400), HTTPLike(401), HTTPLike(429, "insufficient_quota"), "Error code: 400 - {'code': '1210', 'message': 'image not supported'}",
                                   "maximum context length exceeded", "Invalid API key", "Verifier did not write reward.txt"])
def test_permanent_or_task_failures_are_not_retried(error):
    assert not resilience.is_transient(error)


def test_retry_call_survives_more_than_three_transient_errors(monkeypatch):
    errors = [HTTPLike(429), HTTPLike(429), HTTPLike(503), HTTPLike(429)]
    sleeps, calls = [], []

    def flaky():
        calls.append(1)
        if errors:
            raise errors.pop(0)
        return "ok"

    assert resilience.retry_call(flaky, attempts=8, base=1, cap=10, describe="t", sleep=sleeps.append) == "ok"
    assert len(calls) == 5 and len(sleeps) == 4
    with pytest.raises(HTTPLike):
        resilience.retry_call(lambda: (_ for _ in ()).throw(HTTPLike(400)), attempts=8, base=1, cap=10, describe="t", sleep=sleeps.append)
    assert len(sleeps) == 4  # permanent error: no extra sleep


def test_retry_after_is_not_shortened_and_date_header_is_supported():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    assert resilience.backoff_seconds(0, 1, 10, "45") == 45
    assert resilience.backoff_seconds(0, 1, 10, "-1") == 0
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=45))
    assert 43 <= resilience.backoff_seconds(0, 1, 10, future) <= 45


def test_default_retry_layers_do_not_multiply_full_task_runs():
    protocol = load_protocol()
    assert protocol["retry"]["api_max_attempts"] == 4  # initial + three retries
    assert protocol["retry"]["oracle_max_attempts"] == 4
    assert protocol["skilllift"]["max_consecutive_infra_failures"] == 1
    assert protocol["retry"]["job_max_attempts"] == 1


def test_journal_is_occurrence_indexed_survives_torn_lines_and_releases_failures(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal = resilience.Journal(path)
    assert journal.lookup("k") == (False, None)
    journal.release("k")  # live request failed: nothing consumed
    assert journal.lookup("k") == (False, None)
    journal.record("k", {"n": 1})
    assert journal.lookup("k") == (False, None)
    journal.record("k", {"n": 2})
    with path.open("a") as stream:
        stream.write('{"key": "k", "value": {"n"')  # crash mid-write
    replay = resilience.Journal(path)
    assert replay.lookup("k") == (True, {"n": 1}) and replay.lookup("k") == (True, {"n": 2})
    assert replay.lookup("k") == (False, None)
    replay.record("k", {"n": 3})
    restarted = resilience.Journal(path)
    assert [restarted.lookup("k")[1] for _ in range(3)] == [{"n": 1}, {"n": 2}, {"n": 3}]


def test_resilient_llm_replays_identical_prompts_without_calling_the_api(tmp_path):
    class Client:
        role, model, calls = "skill_generator", "fake", 0

        def call_text(self, system, user, temperature=0.0, **kwargs):
            Client.calls += 1
            return f"answer-{Client.calls}"

    policy = resilience.policy_from(load_protocol())
    first = resilience.ResilientLLM(Client(), policy, resilience.Journal(tmp_path / "j.jsonl"))
    assert [first.call_text("s", "u"), first.call_text("s", "u"), first.call_text("s", "other")] == ["answer-1", "answer-2", "answer-3"]
    replay = resilience.ResilientLLM(Client(), policy, resilience.Journal(tmp_path / "j.jsonl"))
    assert [replay.call_text("s", "u"), replay.call_text("s", "u"), replay.call_text("s", "other")] == ["answer-1", "answer-2", "answer-3"]
    assert Client.calls == 3
    assert replay.call_text("s", "u") == "answer-4" and Client.calls == 4  # beyond the journal: live


class FakeNative:
    """Native-adapter stand-in that writes real marker/result/trace files like the SkillsBench adapter."""

    def __init__(self, root, script):
        self.root, self.script, self.live = root, list(script), 0

    def public_task(self, task_id):
        from skilllift.portfolio import PublicTask
        return PublicTask(task_id, "public task")

    def evaluate(self, task_id, portfolio, trial):
        from skilllift.coordinator import CandidateEvaluation
        self.live += 1
        outcome = self.script.pop(0)
        if outcome is None:
            return CandidateEvaluation.infrastructure_failure("LLM call failed: HTTP Error 429: Too Many Requests")
        folder = self.root / f"trial-{self.live}"
        folder.mkdir(parents=True)
        (folder / "chat.jsonl").write_text(json.dumps({"type": "message", "message": {"role": "assistant", "content": f"trace {self.live}"}}) + "\n")
        write_json(folder / "result.json", {"rewards": {"reward": outcome}, "error": None})
        write_json(folder / "marker.json", {"result_path": str(folder / "result.json")})
        return CandidateEvaluation.valid(outcome, result_hash=f"h{self.live}", artifact_ref=str(folder / "marker.json"))


def _oracle(tmp_path, native, journal, seed):
    from experiments.revision2026.oracles import TrainingOracle
    protocol = load_protocol()
    return TrainingOracle(benchmark="skillsbench", family="task", model="glm", seed=seed, protocol=protocol,
                          root=tmp_path / "attempt", ledger=tmp_path / "usage.jsonl", native=native, journal=journal)


def _seed(tmp_path):
    seed = tmp_path / "seed"
    (seed / "guide").mkdir(parents=True)
    (seed / "guide/SKILL.md").write_text("---\nname: guide\ndescription: d\n---\nOriginal.\n")
    return seed


def test_training_oracle_retries_transient_failures_and_replays_after_restart(tmp_path, monkeypatch):
    from skilllift.coordinator import TrialSpec
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    seed = _seed(tmp_path)
    native = FakeNative(tmp_path / "native", [None, None, 0.4])
    oracle = _oracle(tmp_path, native, resilience.Journal(tmp_path / "cell/journal.jsonl"), seed)
    portfolio = oracle.seed_portfolio("task")
    result = oracle.evaluate("task", portfolio, TrialSpec("anchor-x", "anchor", 0))
    assert result.is_valid and result.reward == 0.4 and native.live == 3
    restarted = _oracle(tmp_path, FakeNative(tmp_path / "native2", []), resilience.Journal(tmp_path / "cell/journal.jsonl"), seed)
    again = restarted.evaluate("task", portfolio, TrialSpec("anchor-x", "anchor", 0))
    assert again.reward == 0.4 and restarted.replayed == 1 and restarted.native.live == 0
    assert "trace 3" in restarted.last_feedback["solver_conversation"]


def test_training_feedback_excludes_official_checks_in_live_execution_and_legacy_replay(tmp_path):
    from skilllift.coordinator import TrialSpec

    class NativeWithPrivateChecks(FakeNative):
        def evaluate(self, *args):
            result = super().evaluate(*args)
            write_json(self.root / f"trial-{self.live}" / "verifier/ctrf.json", {
                "results": {"tests": [{"name": "SECRET_EXPECTED_12345", "status": "failed"}]}})
            return result

    seed = _seed(tmp_path)
    journal_path = tmp_path / "journal.jsonl"
    oracle = _oracle(tmp_path, NativeWithPrivateChecks(tmp_path / "native", [0.4]), resilience.Journal(journal_path), seed)
    portfolio = oracle.seed_portfolio("task")
    trial = TrialSpec("anchor-x", "anchor", 0)
    assert oracle.evaluate("task", portfolio, trial).reward == 0.4
    assert "trace 1" in oracle.last_feedback["solver_conversation"]
    assert "SECRET_EXPECTED" not in journal_path.read_text()
    assert "oracle_criterion_outcomes" not in oracle.last_feedback
    # Simulate a journal written before the feedback policy changed.
    entry = json.loads(journal_path.read_text())
    entry["value"]["feedback"].update(oracle_criterion_outcomes=[{"criterion": "SECRET_EXPECTED_12345", "status": "failed"}],
                                      expected_answer="PRIVATE_ANSWER")
    journal_path.write_text(json.dumps(entry) + "\n")
    restarted = _oracle(tmp_path, FakeNative(tmp_path / "native2", []), resilience.Journal(journal_path), seed)
    assert restarted.evaluate("task", portfolio, trial).reward == 0.4
    assert restarted.native.live == 0
    assert restarted.last_feedback == oracle.last_feedback
    assert "SECRET_EXPECTED" in journal_path.read_text()  # Original audit evidence is preserved.


def test_direct_oracle_resumes_after_crash_without_repeating_oracle_executions(tmp_path, monkeypatch):
    import difflib
    from experiments.revision2026 import optimization as module
    from skilllift_eval.runners import bounded_edits_skill_generator
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    crash = {"after": 5, "calls": 0}

    class Generator:
        def __init__(self, *args, **kwargs):
            pass

        def generate(self, task, direction, parent):
            crash["calls"] += 1
            if crash["after"] and crash["calls"] > crash["after"]:
                raise KeyboardInterrupt  # process killed mid-cell
            old = (parent.root / "guide/SKILL.md").read_text()
            new = old + f"Revision {direction.direction_id}.\n"
            return "diff --git a/guide/SKILL.md b/guide/SKILL.md\n" + "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile="a/guide/SKILL.md", tofile="b/guide/SKILL.md"))

    monkeypatch.setattr(bounded_edits_skill_generator, "BoundedEditsSkillGenerator", Generator)
    monkeypatch.setattr(module, "clients", lambda model, ledger, roles, **kwargs: {"skill_generator": object()})
    protocol = load_protocol()
    seed = _seed(tmp_path)
    script = [0.0, 0.2, 0.1, 0.0, 0.2, 0.1, 0.0, 0.2, 0.1, 0.0]
    first = FakeNative(tmp_path / "n1", list(script))
    with pytest.raises(KeyboardInterrupt):
        module.run_direct_oracle(_oracle(tmp_path / "a1", first, resilience.Journal(tmp_path / "cell/journal.jsonl"), seed),
                                 protocol, tmp_path / "a1/run", "glm", tmp_path / "a1/usage.jsonl", journal=resilience.Journal(tmp_path / "cell/journal.jsonl"))
    executed_before_crash = first.live
    assert executed_before_crash == 4  # anchor + round 0's three candidates
    crash["after"], crash["calls"] = 0, 0
    second = FakeNative(tmp_path / "n2", script[executed_before_crash:])
    journal = resilience.Journal(tmp_path / "cell/journal.jsonl")
    oracle = _oracle(tmp_path / "a2", second, journal, seed)
    winner, details = module.run_direct_oracle(oracle, protocol, tmp_path / "a2/run", "glm", tmp_path / "a2/usage.jsonl", journal=journal)
    assert oracle.replayed == executed_before_crash and second.live == details["oracle_rollouts"] - executed_before_crash
    assert details["stop_reason"] == "two_no_improvement_rounds" and details["oracle_rollouts"] == 10


def test_run_jobs_skips_finished_work_and_relaunches_failed_jobs(tmp_path, monkeypatch):
    from experiments.revision2026 import runner
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    protocol = load_protocol()
    protocol["retry"]["job_max_attempts"] = 3  # Explicit opt-in; default avoids multiplying whole-task attempts.
    common = dict(benchmark="skillsbench", model="glm", method="direct_oracle", run=0, root=str(tmp_path), protocol=protocol, config_hash="cfg")
    done = {**common, "kind": "train", "family": "done"}
    todo = {**common, "kind": "train", "family": "todo"}
    cell = runner.cell_path(tmp_path, "skillsbench", "glm", "direct_oracle", 0, "done")
    (cell / "skills").mkdir(parents=True)
    (cell / "skills/SKILL.md").write_text("frozen")
    write_json(cell / "frozen.json", {"status": "complete", "config_hash": "cfg", "skill_hash": tree_hash(cell / "skills")})
    launched = []

    def fake_run(args, **kwargs):
        spec = json.loads(open(args[-1]).read())
        launched.append(spec["family"])
        return SimpleNamespace(returncode=1 if launched.count("todo") == 1 else 0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    runner.run_jobs([done, todo], tmp_path, 2)
    assert launched == ["todo", "todo"]  # finished cell never relaunched; failed one relaunched once


def test_heldout_evaluation_retries_transient_api_errors(tmp_path, monkeypatch):
    from experiments.revision2026 import runner, docker_agent
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setattr(runner, "endpoint_identity", lambda model: {"model": "fake"})
    protocol = load_protocol()
    spec = {"kind": "evaluate", "benchmark": "skilllearnbench", "model": "glm", "method": "llm_written", "run": 0, "family": "fam",
            "task_id": "fam/fam-2", "instance": 2, "task_path": str(tmp_path / "task"), "repeat": 0, "phase": "heldout",
            "root": str(tmp_path), "config_hash": "cfg", "protocol": protocol}
    cell = runner.cell_path(tmp_path, "skilllearnbench", "glm", "llm_written", 0, "fam")
    (cell / "skills").mkdir(parents=True)
    (cell / "skills/SKILL.md").write_text("frozen")
    write_json(cell / "frozen.json", {"status": "complete", "config_hash": "cfg", "skill_hash": tree_hash(cell / "skills"), "frozen_at": "t"})
    outcomes = [{"status": "ERROR", "reward": None, "error": "RateLimitError status=429: Too Many Requests"},
                {"status": "ERROR", "reward": None, "error": "APIConnectionError: Connection error."},
                {"status": "OK", "reward": 1.0}]
    monkeypatch.setattr(docker_agent, "evaluate", lambda *args: outcomes.pop(0))
    runner.evaluate_cell(spec)
    selected = json.loads((runner.evaluation_path(spec) / "selected.json").read_text())
    assert selected["status"] == "OK" and selected["physical_attempts"] == 3
