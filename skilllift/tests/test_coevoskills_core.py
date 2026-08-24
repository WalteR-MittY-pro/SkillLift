from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from coevoskills.config import CoEvoSkillsConfig
from coevoskills.coordinator import run_experiment
from coevoskills.errors import SkillPackageError, SurrogateSuiteError
from coevoskills.persistence import ExperimentStore
from coevoskills.schemas import (
    ArtifactFile,
    ArtifactSnapshot,
    AssertionResult,
    Diagnostic,
    OracleResult,
    SkillVersion,
    SurrogateResult,
    TaskInput,
    TestAssertion as AssertionSpec,
    TestSuiteVersion as SuiteVersion,
)
from coevoskills.skill_package import parse_skill_payload
from coevoskills.surrogate_runtime import (
    PythonSurrogateRuntime,
    _ensure_docker_image_available,
)


def skill(version: int = 0) -> SkillVersion:
    return SkillVersion(
        version=version,
        name="demo-skill",
        files={
            "SKILL.md": "# Demo\n\nUse the helper.\n",
            "scripts/helper.py": "def value():\n    return 1\n",
        },
        entrypoint="scripts/helper.py",
    )


def suite(version: int = 0) -> SuiteVersion:
    return SuiteVersion(
        version=version,
        code=(
            "def run(artifact_root):\n"
            "    exists = (artifact_root / 'result.txt').exists()\n"
            "    return [{'assertion_id': 'a1', 'passed': exists, 'message': 'result required'}]\n"
        ),
        assertions=[AssertionSpec("a1", "result exists")],
    )


class FakeGenerator:
    def __init__(self) -> None:
        self.refinements = 0
        self.oracle_failures = 0
        self.ratio = 0.0

    def initialize(self, task):
        return skill()

    def refine(self, task, current, diagnostic):
        self.refinements += 1
        return skill(current.version + 1)

    def note_oracle_failure(self):
        self.oracle_failures += 1

    def context_ratio(self):
        return self.ratio


class FakeVerifier:
    def __init__(self) -> None:
        self.escalations = 0

    def initialize_suite(self, task, artifacts):
        return suite()

    def escalate_suite(self, task, artifacts, previous):
        self.escalations += 1
        return suite(previous.version + 1)

    def diagnose(self, task, artifacts, current_suite, result):
        return Diagnostic(["a1"], "result missing", ["write result.txt"])


class FakeRollout:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls = 0

    def rollout(self, task, current):
        self.calls += 1
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "result.txt").write_text("ok\n", encoding="utf-8")
        return ArtifactSnapshot(
            run_id=f"rollout-{self.calls}",
            root_path=str(self.root),
            files=[ArtifactFile("result.txt", 3, "hash", "text/plain", "ok")],
        )


class FailAfterCallsRollout(FakeRollout):
    def __init__(self, root: Path, fail_on: int) -> None:
        super().__init__(root)
        self.fail_on = fail_on

    def rollout(self, task, current):
        if self.calls + 1 == self.fail_on:
            self.calls += 1
            raise RuntimeError("injected rollout failure")
        return super().rollout(task, current)


class SequenceRuntime:
    def __init__(self, passed: list[bool]) -> None:
        self.passed = iter(passed)

    def run(self, current_suite, artifacts):
        value = next(self.passed)
        return SurrogateResult(
            current_suite.version,
            [AssertionResult("a1", value, "")],
        )


class SequenceOracle:
    def __init__(self, scores: list[float]) -> None:
        self.scores = iter(scores)
        self.calls = 0

    def evaluate(self, task, current):
        self.calls += 1
        score = next(self.scores)
        return OracleResult(f"oracle-{self.calls}", score, score >= 1.0, "/private/score")


class TurnBudget:
    def __init__(self, exhaust_after: int) -> None:
        self.exhaust_after = exhaust_after
        self.checkpoints: list[tuple[int, str]] = []

    def checkpoint(self, *, turns_completed: int, stage: str) -> bool:
        self.checkpoints.append((turns_completed, stage))
        return turns_completed >= self.exhaust_after


def test_surrogate_failure_refines_skill_before_single_oracle_call(tmp_path: Path) -> None:
    generator = FakeGenerator()
    verifier = FakeVerifier()
    oracle = SequenceOracle([1.0])
    result = run_experiment(
        TaskInput("demo", "produce result.txt"),
        CoEvoSkillsConfig(evaluation_repeats=1),
        generator=generator,
        verifier=verifier,
        rollout=FakeRollout(tmp_path / "artifacts"),
        oracle=oracle,
        surrogate_runtime=SequenceRuntime([False, True]),
        store=ExperimentStore(tmp_path / "run"),
    )

    assert result.stop_reason == "oracle_passed"
    assert result.best_skill.version == 1
    assert generator.refinements == 1
    assert verifier.escalations == 0
    assert oracle.calls == 1


def test_oracle_false_positive_escalates_suite_without_changing_skill(tmp_path: Path) -> None:
    generator = FakeGenerator()
    verifier = FakeVerifier()
    oracle = SequenceOracle([0.0, 1.0])
    result = run_experiment(
        TaskInput("demo", "produce result.txt"),
        CoEvoSkillsConfig(evaluation_repeats=1),
        generator=generator,
        verifier=verifier,
        rollout=FakeRollout(tmp_path / "artifacts"),
        oracle=oracle,
        surrogate_runtime=SequenceRuntime([True, True]),
        store=ExperimentStore(tmp_path / "run"),
    )

    assert result.stop_reason == "oracle_passed"
    assert result.best_skill.version == 0
    assert generator.refinements == 0
    assert generator.oracle_failures == 1
    assert verifier.escalations == 1
    assert oracle.calls == 2


def test_resume_continues_after_last_completed_turn(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path / "run")
    first_rollout = FailAfterCallsRollout(tmp_path / "artifacts-first", fail_on=2)
    with pytest.raises(RuntimeError, match="injected rollout failure"):
        run_experiment(
            TaskInput("demo", "produce result.txt"),
            CoEvoSkillsConfig(evaluation_repeats=1),
            generator=FakeGenerator(),
            verifier=FakeVerifier(),
            rollout=first_rollout,
            oracle=SequenceOracle([0.0]),
            surrogate_runtime=SequenceRuntime([True]),
            store=store,
        )

    checkpoint = store.load_checkpoint(
        TaskInput("demo", "produce result.txt"),
        CoEvoSkillsConfig(evaluation_repeats=1),
    )
    assert checkpoint is not None
    assert checkpoint["turns_completed"] == 1
    assert checkpoint["oracle_interventions"] == 1

    class ResumeGenerator(FakeGenerator):
        def initialize(self, task):
            raise AssertionError("resume must not regenerate the initial skill")

    resumed_rollout = FakeRollout(tmp_path / "artifacts-resumed")
    resumed_oracle = SequenceOracle([1.0])
    result = run_experiment(
        TaskInput("demo", "produce result.txt"),
        CoEvoSkillsConfig(evaluation_repeats=1),
        generator=ResumeGenerator(),
        verifier=FakeVerifier(),
        rollout=resumed_rollout,
        oracle=resumed_oracle,
        surrogate_runtime=SequenceRuntime([True]),
        store=store,
    )

    assert result.stop_reason == "oracle_passed"
    assert result.summary["turns_completed"] == 2
    assert resumed_rollout.calls == 1
    assert resumed_oracle.calls == 1


def test_context_cap_stops_before_rollout(tmp_path: Path) -> None:
    generator = FakeGenerator()
    generator.ratio = 0.8
    rollout = FakeRollout(tmp_path / "artifacts")
    result = run_experiment(
        TaskInput("demo", "do it"),
        CoEvoSkillsConfig(context_cap_ratio=0.7, evaluation_repeats=1),
        generator=generator,
        verifier=FakeVerifier(),
        rollout=rollout,
        oracle=SequenceOracle([1.0]),
        surrogate_runtime=SequenceRuntime([True]),
        store=ExperimentStore(tmp_path / "run"),
    )
    assert result.stop_reason == "context_cap_reached"
    assert rollout.calls == 0


def test_min_turns_delays_success_stop(tmp_path: Path) -> None:
    rollout = FakeRollout(tmp_path / "artifacts")
    oracle = SequenceOracle([1.0, 1.0])
    result = run_experiment(
        TaskInput("demo", "do it"),
        CoEvoSkillsConfig(min_turns=2, max_turns=3, evaluation_repeats=1),
        generator=FakeGenerator(),
        verifier=FakeVerifier(),
        rollout=rollout,
        oracle=oracle,
        surrogate_runtime=SequenceRuntime([True, True]),
        store=ExperimentStore(tmp_path / "run"),
    )

    assert result.stop_reason == "oracle_passed"
    assert result.summary["turns_completed"] == 2
    assert rollout.calls == 2
    assert oracle.calls == 2


def test_max_turns_stops_before_next_rollout(tmp_path: Path) -> None:
    rollout = FakeRollout(tmp_path / "artifacts")
    oracle = SequenceOracle([0.0])
    result = run_experiment(
        TaskInput("demo", "do it"),
        CoEvoSkillsConfig(max_turns=1, evaluation_repeats=1),
        generator=FakeGenerator(),
        verifier=FakeVerifier(),
        rollout=rollout,
        oracle=oracle,
        surrogate_runtime=SequenceRuntime([True]),
        store=ExperimentStore(tmp_path / "run"),
    )

    assert result.stop_reason == "max_turns_reached"
    assert result.summary["turns_completed"] == 1
    assert rollout.calls == 1
    assert oracle.calls == 1


def test_token_budget_stops_after_completed_turn(tmp_path: Path) -> None:
    rollout = FakeRollout(tmp_path / "artifacts")
    oracle = SequenceOracle([0.0])
    budget = TurnBudget(exhaust_after=1)
    result = run_experiment(
        TaskInput("demo", "do it"),
        CoEvoSkillsConfig(max_turns=5, token_budget=100, evaluation_repeats=1),
        generator=FakeGenerator(),
        verifier=FakeVerifier(),
        rollout=rollout,
        oracle=oracle,
        surrogate_runtime=SequenceRuntime([True]),
        store=ExperimentStore(tmp_path / "run"),
        budget=budget,
    )

    assert result.stop_reason == "token_budget_exhausted"
    assert result.summary["turns_completed"] == 1
    assert rollout.calls == 1
    assert oracle.calls == 1
    assert (1, "oracle_evaluated") in budget.checkpoints


def test_min_turns_override_exhausted_token_budget(tmp_path: Path) -> None:
    rollout = FakeRollout(tmp_path / "artifacts")
    oracle = SequenceOracle([0.0, 0.0])
    budget = TurnBudget(exhaust_after=1)
    result = run_experiment(
        TaskInput("demo", "do it"),
        CoEvoSkillsConfig(
            min_turns=2,
            max_turns=3,
            token_budget=100,
            evaluation_repeats=1,
        ),
        generator=FakeGenerator(),
        verifier=FakeVerifier(),
        rollout=rollout,
        oracle=oracle,
        surrogate_runtime=SequenceRuntime([True, True]),
        store=ExperimentStore(tmp_path / "run"),
        budget=budget,
    )

    assert result.stop_reason == "token_budget_exhausted"
    assert result.summary["turns_completed"] == 2
    assert rollout.calls == 2
    assert oracle.calls == 2
    assert (1, "oracle_evaluated") in budget.checkpoints
    assert (2, "oracle_evaluated") in budget.checkpoints


def test_token_budget_stop_reason_precedes_max_turns(tmp_path: Path) -> None:
    rollout = FakeRollout(tmp_path / "artifacts")
    budget = TurnBudget(exhaust_after=1)
    result = run_experiment(
        TaskInput("demo", "do it"),
        CoEvoSkillsConfig(
            min_turns=1,
            max_turns=1,
            token_budget=100,
            evaluation_repeats=1,
        ),
        generator=FakeGenerator(),
        verifier=FakeVerifier(),
        rollout=rollout,
        oracle=SequenceOracle([0.0]),
        surrogate_runtime=SequenceRuntime([True]),
        store=ExperimentStore(tmp_path / "run"),
        budget=budget,
    )

    assert result.stop_reason == "token_budget_exhausted"
    assert result.summary["turns_completed"] == 1
    assert rollout.calls == 1


def test_positive_token_budget_requires_accounting_backend(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="TokenBudgetBackend"):
        run_experiment(
            TaskInput("demo", "do it"),
            CoEvoSkillsConfig(token_budget=100, evaluation_repeats=1),
            generator=FakeGenerator(),
            verifier=FakeVerifier(),
            rollout=FakeRollout(tmp_path / "artifacts"),
            oracle=SequenceOracle([1.0]),
            surrogate_runtime=SequenceRuntime([True]),
            store=ExperimentStore(tmp_path / "run"),
        )


def test_skill_package_rejects_traversal_and_supports_patch() -> None:
    with pytest.raises(SkillPackageError, match="unsafe relative path"):
        parse_skill_payload(
            {
                "package_mode": "full",
                "files": {
                    "SKILL.md": "# Demo",
                    "../escape.py": "pass",
                },
            },
            version=0,
        )

    updated = parse_skill_payload(
        {
            "package_mode": "patch",
            "files": {"scripts/helper.py": "def value():\n    return 2\n"},
        },
        version=1,
        base=skill(),
    )
    assert "return 2" in updated.files["scripts/helper.py"]
    assert updated.files["SKILL.md"].startswith("# Demo")


def test_python_surrogate_runtime_executes_declared_assertions(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "result.txt").write_text("ok\n", encoding="utf-8")
    snapshot = ArtifactSnapshot("r1", str(artifact_root), [])
    result = PythonSurrogateRuntime(allow_local=True).run(suite(), snapshot)
    assert result.passed is True
    assert result.score == 1.0

    unsafe = SuiteVersion(
        0,
        "import subprocess\ndef run(artifact_root):\n    return []\n",
        [AssertionSpec("a1", "bad")],
    )
    with pytest.raises(SurrogateSuiteError, match="forbidden import"):
        PythonSurrogateRuntime(allow_local=True).run(unsafe, snapshot)


def test_docker_image_preflight_accepts_local_cross_platform_image(monkeypatch) -> None:
    responses = iter(
        [
            subprocess.CompletedProcess([], 1, stdout="", stderr="No such image"),
            subprocess.CompletedProcess([], 0, stdout="60eec8752cb5\n", stderr=""),
        ]
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: next(responses))

    _ensure_docker_image_available("wildclawbench-ubuntu:v1.3")
