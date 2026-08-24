from __future__ import annotations

import difflib
import re
from collections import Counter
from pathlib import Path

import pytest

from skilllift.errors import PersistenceError
from skilllift.portfolio import PortfolioRef
from skilllift.coordinator import (
    CandidateEvaluation,
    CoordinatorConfig,
    RewardSpec,
    TaskPortfolioCoordinator,
    TrialSpec,
)
from skilllift.portfolio import (
    PortfolioContextLimitError,
    PublicTask,
    RubricHypothesis,
    SearchDirection,
    SearchPlan,
)
from skilllift.portfolio import PortfolioStore


def _skill(score: float) -> str:
    return (
        "---\nname: alpha\ndescription: Use for alpha tasks.\n"
        "triggers:\n  - alpha\n---\n# Alpha\n\n"
        f"score: {score:.1f}\n"
    )


def _seed(tmp_path: Path, score: float = 0.0) -> PortfolioRef:
    root = tmp_path / "seed"
    (root / "alpha").mkdir(parents=True)
    (root / "alpha" / "SKILL.md").write_text(_skill(score), encoding="utf-8")
    return PortfolioRef.from_directory(
        task_id="task-1",
        root=root,
        config_hash="cfg",
        curated_skill_names=("alpha",),
    )


def _score(portfolio: PortfolioRef) -> float:
    text = (portfolio.root / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    return float(re.search(r"^score: ([0-9.]+)$", text, re.MULTILINE).group(1))


class FakePlanner:
    def __init__(self, directions_per_round: int = 3, *, raise_on_call: bool = False) -> None:
        self.directions_per_round = directions_per_round
        self.raise_on_call = raise_on_call
        self.calls = []

    def plan(self, task, parent, previous_plan, history, direction_history, prohibited_novelty_keys):
        if self.raise_on_call:
            raise AssertionError("persisted SearchPlan should have been reused")
        round_index = len(self.calls)
        self.calls.append((parent.tree_hash, tuple(history)))
        rubric = RubricHypothesis("r-score", "Improve the public workflow", ("task.md",))
        directions = tuple(
            SearchDirection(
                direction_id=f"d-{round_index}-{index}",
                rubric_id="r-score",
                hypothesis=f"Direction {index} will improve reward",
                capability=f"capability-{index}",
                target_scope=("alpha",),
                cross_skill_rationale=None,
                evidence_refs=("task.md", "alpha/SKILL.md"),
                novelty_key=f"round-{round_index}/direction-{index}",
            )
            for index in range(self.directions_per_round)
        )
        return SearchPlan(round_index + 1, (rubric,), directions)


class FakeGenerator:
    def __init__(self, increments=(0.1, 0.2, 0.3), *, raise_on_call: bool = False) -> None:
        self.increments = increments
        self.raise_on_call = raise_on_call
        self.calls = []

    def generate(self, task, direction, parent):
        if self.raise_on_call:
            raise AssertionError("persisted candidate patch should have been reused")
        self.calls.append(direction.direction_id)
        index = int(direction.direction_id.rsplit("-", 1)[1])
        old = (parent.root / "alpha" / "SKILL.md").read_text(encoding="utf-8")
        new_score = _score(parent) + self.increments[index]
        new = re.sub(r"^score: [0-9.]+$", f"score: {new_score:.1f}", old, flags=re.MULTILINE)
        new = new.rstrip() + f"\ncandidate: {direction.direction_id}\n"
        diff = difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="a/alpha/SKILL.md",
            tofile="b/alpha/SKILL.md",
        )
        return "diff --git a/alpha/SKILL.md b/alpha/SKILL.md\n" + "".join(diff)


class FakeAdapter:
    def __init__(
        self,
        seed: PortfolioRef,
        *,
        terminal: float = 1.0,
        final_rewards=(0.4, 0.6),
        fail_first_attempt: bool = False,
        crash_candidate: str | None = None,
    ) -> None:
        self.seed = seed
        self.terminal = terminal
        self.final_rewards = final_rewards
        self.fail_first_attempt = fail_first_attempt
        self.crash_candidate = crash_candidate
        self.calls: list[tuple[TrialSpec, str]] = []
        self.recover_calls: list[str] = []

    def public_task(self, task_id):
        return PublicTask(task_id, "Do the public task", "task.md")

    def seed_portfolio(self, task_id):
        return self.seed

    def reward_spec(self):
        return RewardSpec(terminal_threshold=self.terminal)

    def recover(self, task_id, portfolio, trial):
        self.recover_calls.append(trial.trial_id)
        return None

    def evaluate(self, task_id, portfolio, trial):
        self.calls.append((trial, portfolio.tree_hash))
        if (
            self.crash_candidate is not None
            and self.crash_candidate == trial.candidate_id
            and trial.attempt == 0
        ):
            raise RuntimeError("simulated process death")
        if self.fail_first_attempt and trial.attempt == 0:
            return CandidateEvaluation.infrastructure_failure("transient")
        reward = self.final_rewards[trial.final_index] if trial.phase == "final" else _score(portfolio)
        return CandidateEvaluation.valid(reward, result_hash=f"result-{trial.trial_id}")


def _coordinator(
    tmp_path: Path,
    adapter: FakeAdapter,
    planner: FakePlanner,
    generator: FakeGenerator,
    **config,
) -> TaskPortfolioCoordinator:
    return TaskPortfolioCoordinator(
        adapter=adapter,
        planner=planner,
        generator=generator,
        store=PortfolioStore(tmp_path / "run"),
        config=CoordinatorConfig(**config),
    )


def test_terminal_anchor_short_circuits_all_evolution_and_final_trials(tmp_path: Path) -> None:
    seed = _seed(tmp_path, 1.0)
    adapter = FakeAdapter(seed)
    planner = FakePlanner()
    generator = FakeGenerator()

    result = _coordinator(tmp_path, adapter, planner, generator).evolve("task-1")

    assert result.cohort == "anchor_terminal"
    assert result.stop_reason == "anchor_terminal"
    assert result.logical_cells == 1
    assert result.physical_attempts == 1
    assert result.final_rewards == ()
    assert planner.calls == []
    assert generator.calls == []


@pytest.mark.parametrize(
    "field",
    ["mode_a_min_score_threshold", "rank_alignment_threshold"],
)
def test_coordinator_thresholds_must_be_normalized(field: str) -> None:
    with pytest.raises(ValueError, match="threshold"):
        CoordinatorConfig(**{field: 1.5})


def test_reward_terminal_threshold_must_fit_reward_bounds() -> None:
    with pytest.raises(ValueError, match="terminal_threshold"):
        RewardSpec(terminal_threshold=1.5, minimum=0.0, maximum=1.0)


def test_strict_promotion_keeps_parent_on_tie_and_hash_breaks_candidate_tie(tmp_path: Path) -> None:
    seed = _seed(tmp_path)
    adapter = FakeAdapter(seed, terminal=9.0)
    planner = FakePlanner()
    generator = FakeGenerator(increments=(0.0, 0.5, 0.5))

    result = _coordinator(
        tmp_path,
        adapter,
        planner,
        generator,
        max_rounds=1,
    ).evolve("task-1")

    candidate_hashes = {
        trial.candidate_id: tree_hash
        for trial, tree_hash in adapter.calls
        if trial.phase == "candidate" and trial.candidate_id in {"r000-c01", "r000-c02"}
    }
    assert result.winner_portfolio.tree_hash == min(candidate_hashes.values())
    accepted = [outcome for outcome in result.history if outcome.is_accepted]
    assert len(accepted) == 1
    assert accepted[0].candidate_id in candidate_hashes
    assert not next(outcome for outcome in result.history if outcome.candidate_id == "r000-c00").is_accepted


def test_parent_reward_is_cached_and_full_budget_is_twelve_logical_cells(tmp_path: Path) -> None:
    seed = _seed(tmp_path)
    adapter = FakeAdapter(seed, terminal=9.0)
    result = _coordinator(tmp_path, adapter, FakePlanner(), FakeGenerator()).evolve("task-1")

    phases = Counter(trial.phase for trial, _ in adapter.calls)
    assert phases == {"anchor": 1, "candidate": 9, "final": 2}
    assert result.logical_cells == 12
    assert result.physical_attempts == 12
    assert result.adaptation_reward == pytest.approx(0.9)
    assert result.final_score == 0.6
    assert [item.reward_delta for item in result.history if item.is_accepted] == pytest.approx([0.3, 0.3, 0.3])


def test_two_no_improvement_rounds_stop_before_third_round(tmp_path: Path) -> None:
    seed = _seed(tmp_path)
    adapter = FakeAdapter(seed, terminal=9.0)

    result = _coordinator(
        tmp_path,
        adapter,
        FakePlanner(),
        FakeGenerator(increments=(0.0, 0.0, 0.0)),
    ).evolve("task-1")

    phases = Counter(trial.phase for trial, _ in adapter.calls)
    assert phases == {"anchor": 1, "candidate": 6, "final": 2}
    assert result.stop_reason == "two_no_improvement_rounds"
    assert result.rounds_completed == 2


def test_each_logical_cell_allows_only_one_infrastructure_retry(tmp_path: Path) -> None:
    seed = _seed(tmp_path)
    adapter = FakeAdapter(seed, terminal=9.0, fail_first_attempt=True)

    result = _coordinator(tmp_path, adapter, FakePlanner(), FakeGenerator()).evolve("task-1")

    assert result.logical_cells == 12
    assert result.physical_attempts == 24
    by_cell = Counter((trial.phase, trial.round_index, trial.candidate_id, trial.final_index) for trial, _ in adapter.calls)
    assert set(by_cell.values()) == {2}


def test_resume_reuses_plan_patches_and_completed_candidate(tmp_path: Path) -> None:
    seed = _seed(tmp_path)
    first_adapter = FakeAdapter(seed, terminal=9.0, crash_candidate="r000-c01")
    first_planner = FakePlanner()
    first_generator = FakeGenerator()
    coordinator = _coordinator(
        tmp_path,
        first_adapter,
        first_planner,
        first_generator,
        max_rounds=1,
        candidate_concurrency=1,
    )

    with pytest.raises(RuntimeError, match="simulated process death"):
        coordinator.evolve("task-1")

    assert Counter(trial.candidate_id for trial, _ in first_adapter.calls)["r000-c00"] == 1
    resumed_adapter = FakeAdapter(seed, terminal=9.0)
    resumed = _coordinator(
        tmp_path,
        resumed_adapter,
        FakePlanner(raise_on_call=True),
        FakeGenerator(raise_on_call=True),
        max_rounds=1,
        candidate_concurrency=1,
    ).evolve("task-1")

    resumed_candidates = [trial.candidate_id for trial, _ in resumed_adapter.calls if trial.phase == "candidate"]
    assert "r000-c00" not in resumed_candidates
    assert resumed_candidates.count("r000-c01") == 1
    assert resumed.logical_cells == 6
    assert resumed.physical_attempts == 7
    assert len(resumed.final_rewards) == 2


def test_resume_rejects_config_and_algorithm_changes(tmp_path: Path) -> None:
    seed = _seed(tmp_path)
    first_adapter = FakeAdapter(seed, terminal=9.0, crash_candidate="r000-c01")
    with pytest.raises(RuntimeError, match="simulated process death"):
        _coordinator(
            tmp_path,
            first_adapter,
            FakePlanner(),
            FakeGenerator(),
            max_rounds=1,
            candidate_concurrency=1,
        ).evolve("task-1")

    changed_seed = PortfolioRef.from_directory(
        task_id="task-1",
        root=seed.root,
        config_hash="changed-config",
        curated_skill_names=("alpha",),
    )
    # The seed portfolio marker is immutable: resuming with a different
    # config/algorithm fingerprint is rejected instead of silently ignored.
    with pytest.raises(PersistenceError, match="immutable marker already exists"):
        _coordinator(
            tmp_path,
            FakeAdapter(changed_seed, terminal=9.0),
            FakePlanner(raise_on_call=True),
            FakeGenerator(raise_on_call=True),
            max_rounds=1,
            candidate_concurrency=2,
        ).evolve("task-1")


def test_terminal_winner_resume_remains_adapted_and_reuses_final_cells(tmp_path: Path) -> None:
    seed = _seed(tmp_path)
    first_adapter = FakeAdapter(seed, terminal=0.3)
    first = _coordinator(tmp_path, first_adapter, FakePlanner(), FakeGenerator()).evolve("task-1")

    assert first.stop_reason == "terminal_candidate"
    assert first.cohort == "adapted_budget"
    resumed_adapter = FakeAdapter(seed, terminal=0.3)
    resumed = _coordinator(
        tmp_path,
        resumed_adapter,
        FakePlanner(raise_on_call=True),
        FakeGenerator(raise_on_call=True),
    ).evolve("task-1")

    assert resumed.cohort == "adapted_budget"
    assert resumed.stop_reason == "terminal_candidate"
    assert resumed_adapter.calls == []


def test_context_limited_planner_keeps_anchor_runs_final_two_and_stops_cleanly(tmp_path: Path) -> None:
    seed = _seed(tmp_path, 0.2)
    adapter = FakeAdapter(seed, terminal=1.0, final_rewards=(0.2, 0.3))

    class ContextLimitedPlanner:
        def plan(self, *args, **kwargs):
            raise PortfolioContextLimitError("complete evidence pack exceeds context")

    result = _coordinator(
        tmp_path,
        adapter,
        ContextLimitedPlanner(),  # type: ignore[arg-type]
        FakeGenerator(raise_on_call=True),
    ).evolve("task-1")

    assert result.cohort == "adaptation_skipped_context"
    assert result.stop_reason == "context_preflight_exceeded"
    assert result.rounds_completed == 0
    assert result.logical_cells == 3
    assert result.final_rewards == (0.2, 0.3)
    assert result.final_score == 0.3


def test_context_limited_generators_discard_candidates_without_stopping_task(tmp_path: Path) -> None:
    seed = _seed(tmp_path, 0.2)
    adapter = FakeAdapter(seed, terminal=1.0, final_rewards=(0.2, 0.3))

    class ContextLimitedGenerator:
        def generate(self, *args, **kwargs):
            raise PortfolioContextLimitError("target files exceed context")

    result = _coordinator(
        tmp_path,
        adapter,
        FakePlanner(),
        ContextLimitedGenerator(),  # type: ignore[arg-type]
        max_rounds=1,
    ).evolve("task-1")

    assert result.cohort == "adaptation_skipped_context"
    assert result.stop_reason == "context_preflight_exceeded"
    assert result.logical_cells == 3
    assert len(result.history) == 3
    assert not any(outcome.is_valid for outcome in result.history)
    store = PortfolioStore(tmp_path / "run")
    records = [store.load_candidate("task-1", 0, f"r000-c{index:02d}") for index in range(3)]
    assert all(record is not None and record.failure_kind == "context_preflight_exceeded" for record in records)


def test_existing_shared_asset_scope_is_not_misclassified_as_a_new_skill(tmp_path: Path) -> None:
    root = tmp_path / "seed"
    (root / "alpha").mkdir(parents=True)
    (root / "alpha" / "SKILL.md").write_text(_skill(0.2), encoding="utf-8")
    (root / "reference.md").write_text("old\n", encoding="utf-8")
    seed = PortfolioRef.from_directory(task_id="task-1", root=root, config_hash="cfg")

    class SharedAssetPlanner:
        def plan(self, *args, **kwargs):
            rubric = RubricHypothesis("r-reference", "Improve the public reference", ("task.md",))
            direction = SearchDirection(
                "d-reference",
                "r-reference",
                "A clearer reference will improve reward",
                "reference-guidance",
                ("reference.md",),
                None,
                ("task.md", "reference.md"),
                "reference/v1",
            )
            return SearchPlan(1, (rubric,), (direction,))

    class SharedAssetGenerator:
        def generate(self, *args, **kwargs):
            return """diff --git a/reference.md b/reference.md
--- a/reference.md
+++ b/reference.md
@@ -1 +1 @@
-old
+new
"""

    result = _coordinator(
        tmp_path,
        FakeAdapter(seed, terminal=1.0),
        SharedAssetPlanner(),  # type: ignore[arg-type]
        SharedAssetGenerator(),  # type: ignore[arg-type]
        max_rounds=1,
    ).evolve("task-1")

    assert len(result.history) == 1
    assert result.history[0].is_valid
