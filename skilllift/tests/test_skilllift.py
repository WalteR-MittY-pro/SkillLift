from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

import pytest

from skilllift.portfolio import PortfolioRef
from skilllift.coordinator import (
    CandidateEvaluation,
    CoordinatorConfig,
    RewardSpec,
    TrialSpec,
)
from skilllift.portfolio import (
    PublicTask,
    RubricHypothesis,
    SearchDirection,
    SearchPlan,
)
from skilllift.portfolio import PortfolioStore
from skilllift import SKILLLIFT_COORDINATOR_VERSION, SkillLiftPortfolioCoordinator


def test_skilllift_protocol_version_tracks_mode_b_contract() -> None:
    assert SKILLLIFT_COORDINATOR_VERSION == "skilllift_portfolio_coordinator_v3"


def _skill(score: float, verifier: float = 0.3) -> str:
    return (
        "---\nname: alpha\ndescription: Use for alpha tasks.\n"
        "triggers:\n  - alpha\n---\n# Alpha\n\n"
        f"score: {score:.1f}\nverifier: {verifier:.1f}\n"
    )


def _seed(tmp_path: Path, score: float = 0.0, verifier: float = 0.3) -> PortfolioRef:
    root = tmp_path / "seed"
    (root / "alpha").mkdir(parents=True)
    (root / "alpha" / "SKILL.md").write_text(_skill(score, verifier), encoding="utf-8")
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
    def __init__(self, directions_per_round: int = 3) -> None:
        self.directions_per_round = directions_per_round
        self.feedback_seen: list | None = None

    def plan(
        self,
        task,
        parent,
        previous_plan,
        history,
        direction_history,
        prohibited_novelty_keys,
        verifier_feedback=None,
    ):
        self.feedback_seen = verifier_feedback
        round_index = 0
        if previous_plan is not None:
            round_index = previous_plan.receipt_version
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
    """First drafts bump `score:`; refinement passes rewrite the verifier marker."""

    def __init__(self, increments=(0.1, 0.2, 0.3), refined_verifier: float = 0.9) -> None:
        self.increments = increments
        self.refined_verifier = refined_verifier
        self.calls: list[str] = []
        self.hypotheses: list[str] = []
        self._seen: set[str] = set()

    def generate(self, task, direction, parent):
        self.calls.append(direction.direction_id)
        self.hypotheses.append(direction.hypothesis)
        index = int(direction.direction_id.rsplit("-", 1)[1])
        first_draft = direction.direction_id not in self._seen
        self._seen.add(direction.direction_id)
        old = (parent.root / "alpha" / "SKILL.md").read_text(encoding="utf-8")
        if first_draft:
            new_score = _score(parent) + self.increments[index]
            new = re.sub(r"^score: [0-9.]+$", f"score: {new_score:.1f}", old, flags=re.MULTILINE)
            new = new.rstrip() + f"\ncandidate: {direction.direction_id}\n"
        else:
            new = re.sub(
                r"^verifier: [0-9.]+$", f"verifier: {self.refined_verifier:.1f}", old, flags=re.MULTILINE
            )
        diff = difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="a/alpha/SKILL.md",
            tofile="b/alpha/SKILL.md",
        )
        return "diff --git a/alpha/SKILL.md b/alpha/SKILL.md\n" + "".join(diff)


class FakeVerifierClient:
    """Scores the portfolio by its `verifier:` marker (fallback receipt r1..r6)."""

    def call_json(self, system, user, temperature=0.0):
        match = re.search(r"verifier: ([0-9.]+)", user)
        marker = float(match.group(1)) if match else 0.0
        if marker >= 0.85:
            hits = {"r1": True, "r2": True, "r3": True, "r4": True, "r5": True, "r6": False}
        elif marker >= 0.25:
            hits = {"r1": True, "r2": False, "r3": True, "r4": False, "r5": False, "r6": False}
        else:
            hits = {"r1": True, "r2": False, "r3": False, "r4": False, "r5": False, "r6": False}
        return {"criterion_hits": hits, "rationale": f"marker={marker}"}


class ModeRecordingVerifierClient(FakeVerifierClient):
    def __init__(self):
        self.prompts: list[str] = []

    def call_json(self, system, user, temperature=0.0):
        self.prompts.append(user)
        if "[Mode B Rules]" in user:
            match = re.search(r"score: ([0-9.]+)", user)
            if match:
                user = re.sub(
                    r"verifier: [0-9.]+",
                    f"verifier: {1.0 - float(match.group(1)):.1f}",
                    user,
                )
        return super().call_json(system, user, temperature)


class FakeAdapter:
    def __init__(
        self,
        seed: PortfolioRef,
        *,
        terminal: float = 10.0,
        candidate_runtime_warning: bool = False,
    ) -> None:
        self.seed = seed
        self.terminal = terminal
        self.candidate_runtime_warning = candidate_runtime_warning
        self.calls: list[str] = []

    def public_task(self, task_id):
        return PublicTask(task_id, "Do the public task", "task.md")

    def seed_portfolio(self, task_id):
        return self.seed

    def reward_spec(self):
        return RewardSpec(terminal_threshold=self.terminal)

    def recover(self, task_id, portfolio, trial):
        return None

    def evaluate(self, task_id, portfolio, trial: TrialSpec):
        self.calls.append(trial.phase)
        reward = 0.4 + 0.1 * trial.final_index if trial.phase == "final" else _score(portfolio)
        return CandidateEvaluation.valid(
            reward,
            result_hash=f"result-{trial.trial_id}",
        )


def _coordinator(
    tmp_path: Path,
    adapter: FakeAdapter,
    planner: FakePlanner,
    generator: FakeGenerator,
    verifier=FakeVerifierClient(),
    **config,
) -> SkillLiftPortfolioCoordinator:
    return SkillLiftPortfolioCoordinator(
        adapter=adapter,
        planner=planner,
        generator=generator,
        store=PortfolioStore(tmp_path / "run"),
        config=CoordinatorConfig(**config),
        verifier_client=verifier,
    )


def test_mode_a_refinement_adds_zero_oracle_calls(tmp_path):
    adapter = FakeAdapter(_seed(tmp_path))
    planner = FakePlanner()
    generator = FakeGenerator()
    result = _coordinator(tmp_path, adapter, planner, generator, mode_a_iters=2).evolve("task-1")

    candidate_evaluations = adapter.calls.count("candidate")
    # 3 rounds x 3 candidates evaluated once in Mode B; refinement stays verifier-only.
    assert candidate_evaluations == 9
    assert adapter.calls.count("anchor") == 1
    assert adapter.calls.count("final") == 2
    # Each branch got an initial patch plus at least one refinement patch.
    assert len(generator.calls) > 9
    assert result.stop_reason == "round_budget_exhausted"


def test_mode_a_scores_champion_once_per_round(tmp_path):
    adapter = FakeAdapter(_seed(tmp_path, score=0.0))
    verifier = ModeRecordingVerifierClient()
    _coordinator(
        tmp_path,
        adapter,
        FakePlanner(directions_per_round=1),
        FakeGenerator(increments=(0.1,)),
        verifier=verifier,
        max_rounds=1,
        mode_a_iters=0,
        mode_b_iters=0,
    ).evolve("task-1")

    champion_prompts = [
        prompt
        for prompt in verifier.prompts
        if "[Mode A Rules]" in prompt and "score: 0.0" in prompt
    ]
    assert len(champion_prompts) == 1


def test_verifier_degrading_refinement_is_rolled_back(tmp_path):
    adapter = FakeAdapter(_seed(tmp_path))
    planner = FakePlanner(directions_per_round=1)
    generator = FakeGenerator(increments=(0.5,), refined_verifier=0.2)
    _coordinator(tmp_path, adapter, planner, generator, mode_a_iters=2, max_rounds=1).evolve("task-1")

    # The refinement dropped the verifier score below the branch score and was rejected.
    assert generator.calls.count("d-0-0") >= 2
    round_result = json.loads(
        (tmp_path / "run" / "tasks" / "task-1" / "rounds" / "round-000" / "round_result.json").read_text()
    )
    # Initial branch (verifier marker 0.3 -> (4+2)/13) kept, not the degraded 0.2 refinement.
    assert round_result["verifier_scores"]["r000-c00"] == pytest.approx(6 / 13)
    assert round_result["outcomes"][0]["reward"] == pytest.approx(0.5)


def test_mode_b_writes_alignment_artifacts(tmp_path):
    adapter = FakeAdapter(_seed(tmp_path))
    planner = FakePlanner(directions_per_round=3)
    generator = FakeGenerator()
    _coordinator(tmp_path, adapter, planner, generator, max_rounds=1).evolve("task-1")

    task_root = tmp_path / "run" / "tasks" / "task-1"
    oracle_batch = task_root / "oracle_scores" / "outer_000_mode_b_batch.json"
    assert oracle_batch.is_file()
    round_state = task_root / "round_states" / "step_000_mode_b.json"
    assert round_state.is_file()
    payload = json.loads(round_state.read_text())
    assert payload["rank_alignment"] is not None
    assert all(score["rank"] >= 1 for score in payload["verifier_scores"].values())
    assert all(score["rank"] >= 1 for score in payload["oracle_scores"].values())

    verifier_by_score: dict[float, set[int]] = {}
    for score in payload["verifier_scores"].values():
        verifier_by_score.setdefault(score["normalized_score"], set()).add(score["rank"])
    assert all(len(ranks) == 1 for ranks in verifier_by_score.values())


def test_mode_b_pairs_frozen_champion_with_its_own_reward(tmp_path):
    adapter = FakeAdapter(_seed(tmp_path, score=0.2))
    _coordinator(
        tmp_path,
        adapter,
        FakePlanner(directions_per_round=1),
        FakeGenerator(increments=(0.3,)),
        max_rounds=1,
        mode_a_iters=0,
        mode_b_iters=1,
    ).evolve("task-1")

    task_root = tmp_path / "run" / "tasks" / "task-1"
    payload = json.loads(
        (task_root / "round_states" / "step_000_mode_b.json").read_text()
    )
    champion = payload["skills"]["s000_v001"]["files"]["alpha/SKILL.md"]
    candidate = payload["skills"]["s001_v001"]["files"]["alpha/SKILL.md"]
    assert "score: 0.2" in champion
    assert "score: 0.5" in candidate
    assert payload["oracle_scores"]["s000_v001"]["oracle_score"] == pytest.approx(0.2)
    assert payload["oracle_scores"]["s001_v001"]["oracle_score"] == pytest.approx(0.5)


def test_mode_b_uses_mode_b_prompt_and_persists_each_iteration(tmp_path):
    adapter = FakeAdapter(_seed(tmp_path))
    planner = FakePlanner(directions_per_round=2)
    generator = FakeGenerator()
    verifier = ModeRecordingVerifierClient()
    planner.client = verifier
    _coordinator(
        tmp_path,
        adapter,
        planner,
        generator,
        verifier=verifier,
        max_rounds=1,
        mode_b_iters=2,
        rank_alignment_threshold=0.99,
    ).evolve("task-1")

    mode_b_prompts = [prompt for prompt in verifier.prompts if "[Mode B Rules]" in prompt]
    assert mode_b_prompts
    assert all("Do not use oracle results" in prompt for prompt in mode_b_prompts)
    task_root = tmp_path / "run" / "tasks" / "task-1"
    assert (task_root / "round_states" / "step_000_mode_b.json").is_file()
    assert (task_root / "round_states" / "step_000_mode_b_iter_001.json").is_file()
    assert (task_root / "receipts" / "outer_000_mode_b_iter_000.json").is_file()
    assert (task_root / "receipts" / "outer_000_mode_b_iter_001.json").is_file()


def test_mode_b_keeps_earlier_receipt_when_alignment_ties(tmp_path):
    class EqualAlignmentClient:
        @staticmethod
        def _receipt(version: int) -> dict:
            return {
                "version": version,
                "rubrics": [
                    {
                        "rubric_id": f"r{index}",
                        "category": "Workflow",
                        "criterion": f"Includes observable step {index}",
                        "points": 1,
                    }
                    for index in range(1, 6)
                ],
                "removed_rubrics": [],
            }

        def call_json(self, system, user, temperature=0.0):
            if "rubric design" in system:
                return self._receipt(1)
            if "rubric reviser" in system:
                return self._receipt(2)
            match = re.search(r"score: ([0-9.]+)", user)
            score = float(match.group(1)) if match else 0.0
            hit_count = max(0, 5 - int(round(score * 10)))
            return {
                "criterion_hits": {
                    f"r{index}": index <= hit_count for index in range(1, 6)
                }
            }

    adapter = FakeAdapter(_seed(tmp_path))
    planner = FakePlanner(directions_per_round=2)
    verifier = EqualAlignmentClient()
    planner.client = verifier
    _coordinator(
        tmp_path,
        adapter,
        planner,
        FakeGenerator(),
        verifier=verifier,
        max_rounds=1,
        mode_a_iters=0,
        mode_b_iters=2,
        rank_alignment_threshold=0.99,
    ).evolve("task-1")

    task_root = tmp_path / "run" / "tasks" / "task-1"
    first = json.loads(
        (task_root / "round_states" / "step_000_mode_b.json").read_text()
    )
    second = json.loads(
        (task_root / "round_states" / "step_000_mode_b_iter_001.json").read_text()
    )
    final = json.loads(
        (task_root / "receipts" / "outer_000_mode_b_final.json").read_text()
    )
    assert first["rank_alignment"] == second["rank_alignment"] == -1.0
    assert first["receipt"]["version"] == 1
    assert second["receipt"]["version"] == 2
    assert final["version"] == 1


def test_planner_receives_verifier_feedback(tmp_path):
    adapter = FakeAdapter(_seed(tmp_path))
    planner = FakePlanner()
    generator = FakeGenerator()
    _coordinator(tmp_path, adapter, planner, generator, max_rounds=1).evolve("task-1")

    assert planner.feedback_seen is not None
    assert all("criterion_hits" in item for item in planner.feedback_seen)


def test_planner_without_feedback_kwarg_still_works(tmp_path):
    class LegacyPlanner(FakePlanner):
        def plan(self, task, parent, previous_plan, history, direction_history, prohibited):
            return super().plan(task, parent, previous_plan, history, direction_history, prohibited)

    adapter = FakeAdapter(_seed(tmp_path))
    planner = LegacyPlanner()
    generator = FakeGenerator()
    result = _coordinator(tmp_path, adapter, planner, generator, max_rounds=1).evolve("task-1")

    assert result.rounds_completed == 1


def test_oracle_tie_promotes_higher_verifier_branch(tmp_path):
    seed = _seed(tmp_path, score=0.5, verifier=0.3)  # champion oracle 0.5, verifier (4+2)/13
    adapter = FakeAdapter(seed)
    planner = FakePlanner(directions_per_round=1)
    generator = FakeGenerator(increments=(0.0,), refined_verifier=0.9)
    result = _coordinator(tmp_path, adapter, planner, generator, max_rounds=1).evolve("task-1")

    # Candidate ties the champion on oracle (0.5) but its refined verifier score
    # (1.0) beats the champion's (0.4615) -> promoted by tie-break.
    round_result = json.loads(
        (tmp_path / "run" / "tasks" / "task-1" / "rounds" / "round-000" / "round_result.json").read_text()
    )
    assert round_result["accepted_candidate_id"] == "r000-c00"
    assert round_result["accept_reason"] == "verifier_tiebreak"
    assert result.adaptation_reward == pytest.approx(0.5)


def test_oracle_tie_keeps_champion_when_verifier_not_better(tmp_path):
    seed = _seed(tmp_path, score=0.5, verifier=0.3)
    adapter = FakeAdapter(seed)
    planner = FakePlanner(directions_per_round=1)
    generator = FakeGenerator(increments=(0.0,), refined_verifier=0.2)
    _coordinator(tmp_path, adapter, planner, generator, max_rounds=1).evolve("task-1")

    round_result = json.loads(
        (tmp_path / "run" / "tasks" / "task-1" / "rounds" / "round-000" / "round_result.json").read_text()
    )
    assert round_result["accepted_candidate_id"] is None
    assert round_result["accept_reason"] is None


def test_first_draft_persists_original_direction(tmp_path):
    adapter = FakeAdapter(_seed(tmp_path))
    planner = FakePlanner(directions_per_round=1)
    generator = FakeGenerator()
    _coordinator(tmp_path, adapter, planner, generator, max_rounds=1).evolve("task-1")

    # The first draft persists the original direction (resume-stable). Champion
    # hits still reach the planner via verifier_feedback; refinement re-injects
    # branch-local hits in-memory only.
    assert "Unmet verifier criteria" not in generator.hypotheses[0]


def test_receipt_survives_state_round_trip(tmp_path):
    adapter = FakeAdapter(_seed(tmp_path))
    planner = FakePlanner()
    generator = FakeGenerator()
    coordinator = _coordinator(tmp_path, adapter, planner, generator, max_rounds=1)
    coordinator.evolve("task-1")
    state = json.loads((tmp_path / "run" / "tasks" / "task-1" / "state.json").read_text())
    assert state["receipt"]["version"] >= 1
    assert state["receipt"]["rubrics"]


def test_verifier_errors_are_structured_and_do_not_abort_batch(tmp_path):
    class BrokenVerifier:
        def __init__(self):
            self.calls = 0

        def call_json(self, system, user, temperature=0.0):
            self.calls += 1
            if self.calls == 1:
                return {
                    "rubrics": [
                        {"rubric_id": "r1", "category": "Planning", "criterion": "Has plan", "points": 1},
                        {"rubric_id": "r2", "category": "Tools", "criterion": "Uses tools", "points": 1},
                        {"rubric_id": "r3", "category": "Output", "criterion": "Checks output", "points": 1},
                        {"rubric_id": "r4", "category": "Safety", "criterion": "Avoids unsafe action", "points": 1},
                        {"rubric_id": "r5", "category": "Noise", "criterion": "Adds irrelevant noise", "points": -1},
                    ],
                }
            raise RuntimeError("provider unavailable")

    adapter = FakeAdapter(_seed(tmp_path))
    planner = FakePlanner(directions_per_round=1)
    generator = FakeGenerator(increments=(0.2,))
    _coordinator(
        tmp_path,
        adapter,
        planner,
        generator,
        verifier=BrokenVerifier(),
        max_rounds=1,
    ).evolve("task-1")

    state = json.loads(
        (tmp_path / "run" / "tasks" / "task-1" / "state.json").read_text()
    )
    diagnostic = state["diagnostics"][0]
    assert diagnostic["stage"] == "mode_a_champion"
    assert diagnostic["error_type"] == "RuntimeError"
    assert diagnostic["error"] == "provider unavailable"
    assert diagnostic["artifact_ref"].endswith("round_result.json")


def test_candidate_diagnostics_reference_task_root_artifacts(tmp_path):
    class BrokenPatchGenerator:
        def generate(self, task, direction, parent):
            return "not a unified diff"

    adapter = FakeAdapter(_seed(tmp_path))
    _coordinator(
        tmp_path,
        adapter,
        FakePlanner(directions_per_round=1),
        BrokenPatchGenerator(),
        max_rounds=1,
    ).evolve("task-1")

    state = json.loads(
        (tmp_path / "run" / "tasks" / "task-1" / "state.json").read_text()
    )
    assert state["diagnostics"][0]["artifact_ref"] == (
        "rounds/round-000/candidates/r000-c00"
    )


def test_candidate_runtime_warnings_are_not_copied_to_round_diagnostics(tmp_path):
    adapter = FakeAdapter(
        _seed(tmp_path), candidate_runtime_warning=True
    )
    _coordinator(
        tmp_path,
        adapter,
        FakePlanner(directions_per_round=1),
        FakeGenerator(increments=(0.1,)),
        max_rounds=1,
        mode_b_iters=0,
    ).evolve("task-1")

    state = json.loads(
        (tmp_path / "run" / "tasks" / "task-1" / "state.json").read_text()
    )
    assert all(item.get("error_type") != "agent_timeout" for item in state["diagnostics"])


def test_verifier_fallback_is_recorded_as_structured_diagnostic(tmp_path):
    class InvalidOutputVerifier:
        def call_json(self, system, user, temperature=0.0):
            if "rubric design" in system:
                return {
                    "rubrics": [
                        {
                            "rubric_id": f"r{index}",
                            "category": "Workflow",
                            "criterion": f"Includes observable step {index}",
                            "points": 1,
                        }
                        for index in range(1, 6)
                    ]
                }
            return {"rationale": "missing criterion hits"}

    adapter = FakeAdapter(_seed(tmp_path))
    _coordinator(
        tmp_path,
        adapter,
        FakePlanner(directions_per_round=1),
        FakeGenerator(increments=(0.1,)),
        verifier=InvalidOutputVerifier(),
        max_rounds=1,
        mode_a_iters=0,
        mode_b_iters=1,
    ).evolve("task-1")

    state = json.loads(
        (tmp_path / "run" / "tasks" / "task-1" / "state.json").read_text()
    )
    fallback = next(
        item
        for item in state["diagnostics"]
        if item["error_type"] == "deterministic_verifier_fallback"
    )
    assert fallback["stage"] == "mode_a_champion"
    assert fallback["skill"].startswith("s")
    assert "criterion_hits" in fallback["error"]
    assert fallback["artifact_ref"].endswith("round_result.json")

    mode_b_state = json.loads(
        (
            tmp_path
            / "run"
            / "tasks"
            / "task-1"
            / "round_states"
            / "step_000_mode_b.json"
        ).read_text()
    )
    mode_b_fallback = next(
        item
        for item in mode_b_state["diagnostics"]
        if item["stage"] == "mode_b_iter_0"
    )
    assert mode_b_fallback["artifact_ref"].endswith("round_result.json")
