from __future__ import annotations

import hashlib
import json
import math
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, Sequence

from ..errors import PersistenceError
from ..portfolio import (
    PortfolioError,
    PortfolioRef,
    apply_portfolio_patch,
    extract_unified_diff,
)
from ..portfolio import (
    PortfolioContextLimitError,
    PublicTask,
    RubricHypothesis,
    ScalarOutcome,
    SearchDirection,
    SearchPlan,
    serialize_search_history,
)
from ..portfolio import CandidateRecord, PortfolioStore


COORDINATOR_VERSION = "task_portfolio_coordinator_v2"


@dataclass(frozen=True)
class CoordinatorConfig:
    max_rounds: int = 3
    max_candidates_per_round: int = 3
    candidate_concurrency: int = 2
    attempts_per_cell: int = 2
    final_trials: int = 2
    mode_a_iters: int = 2
    mode_b_iters: int = 2
    mode_a_min_score_threshold: float = 0.85
    rank_alignment_threshold: float = 0.9
    max_context_chars: int = 120_000

    def __post_init__(self) -> None:
        if not 1 <= self.max_rounds <= 3:
            raise ValueError("max_rounds must be between 1 and 3")
        if not 1 <= self.max_candidates_per_round <= 3:
            raise ValueError("max_candidates_per_round must be between 1 and 3")
        if self.candidate_concurrency < 1:
            raise ValueError("candidate_concurrency must be positive")
        if self.attempts_per_cell != 2:
            raise ValueError("the locked protocol requires exactly two physical attempts per cell")
        if self.final_trials != 2:
            raise ValueError("the locked protocol requires exactly two final trials")
        if self.mode_a_iters < 0:
            raise ValueError("mode_a_iters must be non-negative")
        if self.mode_b_iters < 0:
            raise ValueError("mode_b_iters must be non-negative")
        if self.max_context_chars < 1:
            raise ValueError("max_context_chars must be positive")
        if not 0.0 <= self.mode_a_min_score_threshold <= 1.0:
            raise ValueError("mode_a_min_score_threshold must be in [0, 1]")
        if not 0.0 <= self.rank_alignment_threshold <= 1.0:
            raise ValueError("rank_alignment_threshold must be in [0, 1]")


@dataclass(frozen=True)
class RewardSpec:
    terminal_threshold: float
    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self) -> None:
        try:
            threshold = float(self.terminal_threshold)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("terminal_threshold must be finite") from exc
        if not math.isfinite(threshold):
            raise ValueError("terminal_threshold must be finite")
        if self.minimum is not None and self.maximum is not None:
            if float(self.minimum) > float(self.maximum):
                raise ValueError("reward minimum must not exceed maximum")
            if not float(self.minimum) <= threshold <= float(self.maximum):
                raise ValueError("terminal_threshold must be within reward bounds")

    def validate(self, reward: float) -> float:
        value = float(reward)
        if not math.isfinite(value):
            raise ValueError("reward must be finite")
        if self.minimum is not None and value < self.minimum:
            raise ValueError(f"reward {value} is below minimum {self.minimum}")
        if self.maximum is not None and value > self.maximum:
            raise ValueError(f"reward {value} is above maximum {self.maximum}")
        return value

    def is_terminal(self, reward: float) -> bool:
        return self.validate(reward) >= self.terminal_threshold

    def improves(self, candidate: float, parent: float) -> bool:
        return self.validate(candidate) > self.validate(parent)


@dataclass(frozen=True)
class TrialSpec:
    trial_id: str
    phase: str
    attempt: int
    round_index: int | None = None
    candidate_id: str | None = None
    final_index: int | None = None


@dataclass(frozen=True)
class CandidateEvaluation:
    reward: float | None
    infrastructure_error: str | None
    result_hash: str | None = None
    artifact_ref: str | None = None
    usage_ref: str | None = None

    def __post_init__(self) -> None:
        if self.reward is not None and self.infrastructure_error is not None:
            raise ValueError("a candidate evaluation cannot be valid and infrastructure-failed")
        if self.reward is None and not self.infrastructure_error:
            raise ValueError("an invalid candidate evaluation requires an infrastructure error")
        if self.reward is not None and not self.result_hash:
            raise ValueError("a valid candidate evaluation requires an exact result hash")

    @property
    def is_valid(self) -> bool:
        return self.reward is not None and self.infrastructure_error is None

    @classmethod
    def valid(
        cls,
        reward: float,
        *,
        result_hash: str,
        artifact_ref: str | None = None,
        usage_ref: str | None = None,
    ) -> CandidateEvaluation:
        return cls(
            float(reward),
            None,
            result_hash,
            artifact_ref,
            usage_ref,
        )

    @classmethod
    def infrastructure_failure(cls, error: str) -> CandidateEvaluation:
        return cls(None, error or "infrastructure failure")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reward": self.reward,
            "infrastructure_error": self.infrastructure_error,
            "result_hash": self.result_hash,
            "artifact_ref": self.artifact_ref,
            "usage_ref": self.usage_ref,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CandidateEvaluation:
        return cls(
            reward=payload.get("reward"),
            infrastructure_error=payload.get("infrastructure_error"),
            result_hash=payload.get("result_hash"),
            artifact_ref=payload.get("artifact_ref"),
            usage_ref=payload.get("usage_ref"),
        )


class TaskPortfolioAdapter(Protocol):
    def public_task(self, task_id: str) -> PublicTask: ...

    def seed_portfolio(self, task_id: str) -> PortfolioRef: ...

    def reward_spec(self) -> RewardSpec: ...

    def recover(
        self,
        task_id: str,
        portfolio: PortfolioRef,
        trial: TrialSpec,
    ) -> CandidateEvaluation | None: ...

    def evaluate(
        self,
        task_id: str,
        portfolio: PortfolioRef,
        trial: TrialSpec,
    ) -> CandidateEvaluation: ...


class RubricatorPlanner(Protocol):
    def plan(
        self,
        task: PublicTask,
        parent: PortfolioRef,
        previous_plan: SearchPlan | None,
        history: Sequence[ScalarOutcome],
        direction_history: Sequence[SearchDirection],
        prohibited_novelty_keys: frozenset[str],
    ) -> SearchPlan: ...


class PatchSkillGenerator(Protocol):
    def generate(
        self,
        task: PublicTask,
        direction: SearchDirection,
        parent: PortfolioRef,
    ) -> str: ...


@dataclass(frozen=True)
class TaskEvolutionResult:
    task_id: str
    cohort: str
    stop_reason: str
    winner_portfolio: PortfolioRef
    adaptation_reward: float | None
    final_rewards: tuple[float, ...]
    final_score: float | None
    history: tuple[ScalarOutcome, ...]
    rounds_completed: int
    logical_cells: int
    physical_attempts: int


@dataclass(frozen=True)
class _EvaluatedCandidate:
    record: CandidateRecord
    portfolio: PortfolioRef
    evaluation: CandidateEvaluation


class TaskPortfolioCoordinator:
    COORDINATOR_VERSION = COORDINATOR_VERSION

    def __init__(
        self,
        *,
        adapter: TaskPortfolioAdapter,
        planner: RubricatorPlanner,
        generator: PatchSkillGenerator,
        store: PortfolioStore,
        config: CoordinatorConfig | None = None,
    ) -> None:
        self.adapter = adapter
        self.planner = planner
        self.generator = generator
        self.store = store
        self.config = config or CoordinatorConfig()

    def evolve(self, task_id: str) -> TaskEvolutionResult:
        task = self.adapter.public_task(task_id)
        seed = self.adapter.seed_portfolio(task_id)
        reward_spec = self.adapter.reward_spec()
        if task.task_id != task_id or seed.task_id != task_id:
            raise ValueError("adapter task and seed identities must match the requested task")
        algorithm_hash = _stable_hash(
            {
                "version": self.COORDINATOR_VERSION,
                "config": asdict(self.config),
                "portfolio_config_hash": seed.config_hash,
                "reward_spec": asdict(reward_spec),
            }
        )
        state = self.store.initialize(task_id, seed, algorithm_hash)

        with tempfile.TemporaryDirectory(prefix=f"skilllift-{task_id}-") as temporary:
            workspace = Path(temporary)
            parent = self.store.materialize_parent(task_id, state, workspace / "accepted")
            history = _history_from_state(state)
            parent_reward = state.get("parent_reward")
            if parent_reward is None:
                anchor = self._evaluate_cell(
                    task_id,
                    parent,
                    reward_spec,
                    algorithm_hash,
                    phase="anchor",
                )
                if not anchor.is_valid:
                    state.update(status="completed", stop_reason="anchor_indeterminate", cohort="anchor_indeterminate")
                    self.store.save_state(task_id, state)
                    frozen = self.store.freeze_final(task_id, parent)
                    return self._result(task_id, state, frozen, history)
                parent_reward = reward_spec.validate(anchor.reward)
                state.update(status="evolving", parent_reward=parent_reward)
                self.store.save_state(task_id, state)

            parent_reward = reward_spec.validate(parent_reward)
            if reward_spec.is_terminal(parent_reward) and not state.get("accepted_candidates"):
                state.update(
                    status="completed",
                    stop_reason="anchor_terminal",
                    cohort="anchor_terminal",
                    final_rewards=[],
                    final_score=None,
                )
                self.store.save_state(task_id, state)
                frozen = self.store.freeze_final(task_id, parent)
                return self._result(task_id, state, frozen, history)

            if state.get("status") not in {"evolution_stopped", "completed"}:
                parent, parent_reward, history, state = self._run_rounds(
                    task,
                    parent,
                    parent_reward,
                    reward_spec,
                    history,
                    state,
                    algorithm_hash,
                    workspace,
                )

            frozen = self.store.freeze_final(task_id, parent)
            final_rewards = []
            for final_index in range(self.config.final_trials):
                evaluation = self._evaluate_cell(
                    task_id,
                    frozen,
                    reward_spec,
                    algorithm_hash,
                    phase="final",
                    final_index=final_index,
                )
                if evaluation.is_valid:
                    final_rewards.append(reward_spec.validate(evaluation.reward))
            final_score = max(final_rewards) if final_rewards else None
            state.update(
                status="completed",
                cohort=state.get("cohort") or "adapted_budget",
                final_rewards=final_rewards,
                final_score=final_score,
            )
            self.store.save_state(task_id, state)
            return self._result(task_id, state, frozen, history)

    def _run_rounds(
        self,
        task: PublicTask,
        parent: PortfolioRef,
        parent_reward: float,
        reward_spec: RewardSpec,
        history: tuple[ScalarOutcome, ...],
        state: dict[str, Any],
        algorithm_hash: str,
        workspace: Path,
    ) -> tuple[PortfolioRef, float, tuple[ScalarOutcome, ...], dict[str, Any]]:
        previous_plan = _plan_from_state(state.get("previous_plan"))
        direction_history = _direction_history_from_state(state)
        prohibited = frozenset(state.get("prohibited_novelty_keys", []))
        for round_index in range(int(state.get("next_round", 0)), self.config.max_rounds):
            plan = self.store.load_plan(task.task_id, round_index)
            if plan is None:
                try:
                    plan = self.planner.plan(
                        task,
                        parent,
                        previous_plan,
                        history,
                        direction_history,
                        prohibited,
                    )
                except PortfolioContextLimitError:
                    state.update(
                        status="evolution_stopped",
                        stop_reason="context_preflight_exceeded",
                        cohort="adaptation_skipped_context",
                    )
                    self.store.save_state(task.task_id, state)
                    break
                self.store.save_plan(task.task_id, round_index, plan)
            directions = plan.directions[: self.config.max_candidates_per_round]
            if not directions:
                state.update(status="evolution_stopped", stop_reason="no_valid_directions")
                self.store.save_state(task.task_id, state)
                break

            round_workspace = workspace / f"round-{round_index:03d}"
            round_workspace.mkdir(parents=True, exist_ok=True)
            candidates = [
                self._prepare_candidate(task, parent, round_index, index, direction, round_workspace)
                for index, direction in enumerate(directions)
            ]
            valid_candidates = [(record, portfolio) for record, portfolio in candidates if portfolio is not None]
            evaluated = self._evaluate_candidates(
                task.task_id,
                valid_candidates,
                reward_spec,
                algorithm_hash,
                round_index,
            )
            by_id = {item.record.candidate_id: item for item in evaluated}
            competitive = [item for item in evaluated if item.evaluation.is_valid]
            winner = _select_winner(competitive)
            accepted_id = None
            parent_reward_before = parent_reward
            if winner is not None and reward_spec.improves(winner.evaluation.reward, parent_reward):
                accepted_id = winner.record.candidate_id
                parent = winner.portfolio
                parent_reward = reward_spec.validate(winner.evaluation.reward)
                state["accepted_candidates"].append(
                    {"round_index": round_index, "candidate_id": accepted_id}
                )
                state["no_improvement_rounds"] = 0
            else:
                state["no_improvement_rounds"] = int(state.get("no_improvement_rounds", 0)) + 1

            round_history = _round_history(
                candidates,
                by_id,
                parent_reward_before=parent_reward_before,
                accepted_id=accepted_id,
            )
            history = (*history, *round_history)
            direction_history = (*direction_history, *directions)
            for record, _ in candidates:
                item = by_id.get(record.candidate_id)
                if item is None or not item.evaluation.is_valid or record.candidate_id != accepted_id:
                    prohibited = frozenset({*prohibited, record.direction.novelty_key})

            state.update(
                parent_reward=parent_reward,
                next_round=round_index + 1,
                history=serialize_search_history(history),
                direction_history=[item.to_dict() for item in direction_history],
                prohibited_novelty_keys=sorted(prohibited),
                previous_plan=plan.to_dict(),
            )
            stop_reason = None
            if not competitive:
                context_limited = all(
                    record.failure_kind == "context_preflight_exceeded" for record, _ in candidates
                )
                if context_limited and not state.get("accepted_candidates"):
                    stop_reason = "context_preflight_exceeded"
                    state["cohort"] = "adaptation_skipped_context"
                else:
                    stop_reason = "all_candidates_invalid"
            elif accepted_id is not None and reward_spec.is_terminal(parent_reward):
                stop_reason = "terminal_candidate"
            elif int(state["no_improvement_rounds"]) >= 2:
                stop_reason = "two_no_improvement_rounds"
            elif round_index + 1 >= self.config.max_rounds:
                stop_reason = "round_budget_exhausted"
            state.update(
                status="evolution_stopped" if stop_reason else "evolving",
                stop_reason=stop_reason,
            )
            self.store.save_round_result(
                task.task_id,
                round_index,
                {
                    "round_index": round_index,
                    "parent_hash_after": parent.tree_hash,
                    "parent_reward_after": parent_reward,
                    "accepted_candidate_id": accepted_id,
                    "outcomes": serialize_search_history(round_history),
                    "stop_reason": stop_reason,
                },
            )
            self.store.save_state(task.task_id, state)
            previous_plan = plan
            if stop_reason:
                break
        return parent, parent_reward, tuple(history), state

    def _prepare_candidate(
        self,
        task: PublicTask,
        parent: PortfolioRef,
        round_index: int,
        index: int,
        direction: SearchDirection,
        round_workspace: Path,
    ) -> tuple[CandidateRecord, PortfolioRef | None]:
        candidate_id = f"r{round_index:03d}-c{index:02d}"
        record = self.store.load_candidate(task.task_id, round_index, candidate_id)
        if record is not None:
            if record.direction != direction:
                raise PersistenceError(f"persisted direction mismatch for {candidate_id}")
            portfolio = (
                self.store.materialize_candidate(parent, record, round_workspace / candidate_id)
                if record.is_valid
                else None
            )
            return record, portfolio

        raw_patch = ""
        try:
            raw_patch = self.generator.generate(task, direction, parent)
            patch = extract_unified_diff(raw_patch)
            existing_top_level = {path.name for path in parent.root.iterdir()}
            new_skill_names = tuple(
                sorted(
                    {
                        PurePosixPath(scope).parts[0]
                        for scope in direction.target_scope
                        if PurePosixPath(scope).parts[0] not in existing_top_level
                    }
                )
            )
            result = apply_portfolio_patch(
                parent,
                patch,
                round_workspace / candidate_id,
                target_scope=direction.target_scope,
                new_skill_names=new_skill_names,
            )
            failure_kind = None
            error = None
        except PortfolioContextLimitError as exc:
            patch = raw_patch
            new_skill_names = ()
            result = None
            failure_kind = "context_preflight_exceeded"
            error = str(exc)
        except PortfolioError as exc:
            patch = raw_patch
            new_skill_names = ()
            result = None
            failure_kind = "invalid_patch"
            error = str(exc)
        record = self.store.save_candidate(
            task.task_id,
            round_index,
            candidate_id,
            direction,
            patch,
            parent.tree_hash,
            result,
            new_skill_names=new_skill_names,
            failure_kind=failure_kind,
            error=error,
        )
        return record, result.portfolio if result else None

    def _evaluate_candidates(
        self,
        task_id: str,
        candidates: list[tuple[CandidateRecord, PortfolioRef]],
        reward_spec: RewardSpec,
        algorithm_hash: str,
        round_index: int,
    ) -> list[_EvaluatedCandidate]:
        def evaluate(item: tuple[CandidateRecord, PortfolioRef]) -> _EvaluatedCandidate:
            record, portfolio = item
            evaluation = self._evaluate_cell(
                task_id,
                portfolio,
                reward_spec,
                algorithm_hash,
                phase="candidate",
                round_index=round_index,
                candidate_id=record.candidate_id,
            )
            return _EvaluatedCandidate(record, portfolio, evaluation)

        if not candidates:
            return []
        with ThreadPoolExecutor(max_workers=min(self.config.candidate_concurrency, len(candidates))) as executor:
            futures = [executor.submit(evaluate, item) for item in candidates]
            return [future.result() for future in futures]

    def _evaluate_cell(
        self,
        task_id: str,
        portfolio: PortfolioRef,
        reward_spec: RewardSpec,
        algorithm_hash: str,
        *,
        phase: str,
        round_index: int | None = None,
        candidate_id: str | None = None,
        final_index: int | None = None,
    ) -> CandidateEvaluation:
        cell_root = self.store.cell_root(
            task_id,
            phase=phase,
            round_index=round_index,
            candidate_id=candidate_id,
            final_index=final_index,
        )
        fingerprint = _stable_hash(
            {
                "algorithm_hash": algorithm_hash,
                "task_id": task_id,
                "portfolio_hash": portfolio.tree_hash,
                "phase": phase,
                "round_index": round_index,
                "candidate_id": candidate_id,
                "final_index": final_index,
            }
        )
        stored = self.store.load_cell_result(cell_root, fingerprint)
        if stored is not None:
            return CandidateEvaluation.from_dict(stored["evaluation"])

        attempts = self.store.attempts(cell_root, fingerprint)
        for marker in attempts:
            if marker["status"] == "completed" and marker.get("evaluation"):
                evaluation = CandidateEvaluation.from_dict(marker["evaluation"])
                self._save_evaluation_result(cell_root, fingerprint, evaluation)
                return evaluation
            if marker["status"] != "started":
                continue
            trial = TrialSpec(
                trial_id=marker["trial_id"],
                phase=phase,
                attempt=marker["attempt"],
                round_index=round_index,
                candidate_id=candidate_id,
                final_index=final_index,
            )
            recovered = self.adapter.recover(task_id, portfolio, trial)
            if recovered is not None and recovered.is_valid:
                reward_spec.validate(recovered.reward)
                self.store.finish_attempt(
                    cell_root,
                    fingerprint=fingerprint,
                    attempt=trial.attempt,
                    trial_id=trial.trial_id,
                    status="completed",
                    evaluation=recovered.to_dict(),
                )
                self._save_evaluation_result(cell_root, fingerprint, recovered)
                return recovered
            self.store.finish_attempt(
                cell_root,
                fingerprint=fingerprint,
                attempt=trial.attempt,
                trial_id=trial.trial_id,
                status="unrecoverable",
                evaluation=recovered.to_dict() if recovered else None,
            )

        consumed = len(self.store.attempts(cell_root, fingerprint))
        for attempt in range(consumed, self.config.attempts_per_cell):
            trial_id = f"{phase}-{fingerprint[:16]}-a{attempt}"
            trial = TrialSpec(
                trial_id=trial_id,
                phase=phase,
                attempt=attempt,
                round_index=round_index,
                candidate_id=candidate_id,
                final_index=final_index,
            )
            self.store.begin_attempt(
                cell_root,
                fingerprint=fingerprint,
                attempt=attempt,
                trial_id=trial_id,
            )
            evaluation = self.adapter.evaluate(task_id, portfolio, trial)
            status = "completed" if evaluation.is_valid else "infrastructure_failed"
            if evaluation.is_valid:
                reward_spec.validate(evaluation.reward)
            self.store.finish_attempt(
                cell_root,
                fingerprint=fingerprint,
                attempt=attempt,
                trial_id=trial_id,
                status=status,
                evaluation=evaluation.to_dict(),
            )
            if evaluation.is_valid:
                self._save_evaluation_result(cell_root, fingerprint, evaluation)
                return evaluation

        indeterminate = CandidateEvaluation.infrastructure_failure("physical attempt budget exhausted")
        self._save_evaluation_result(cell_root, fingerprint, indeterminate)
        return indeterminate

    def _save_evaluation_result(
        self,
        cell_root: Path,
        fingerprint: str,
        evaluation: CandidateEvaluation,
    ) -> None:
        self.store.save_cell_result(
            cell_root,
            {
                "fingerprint": fingerprint,
                "evaluation": evaluation.to_dict(),
            },
        )

    def _result(
        self,
        task_id: str,
        state: dict[str, Any],
        winner: PortfolioRef,
        history: tuple[ScalarOutcome, ...],
    ) -> TaskEvolutionResult:
        logical, physical = self.store.metrics(task_id)
        return TaskEvolutionResult(
            task_id=task_id,
            cohort=state.get("cohort") or "adapted_budget",
            stop_reason=state.get("stop_reason") or "round_budget_exhausted",
            winner_portfolio=winner,
            adaptation_reward=state.get("parent_reward"),
            final_rewards=tuple(state.get("final_rewards", [])),
            final_score=state.get("final_score"),
            history=history,
            rounds_completed=int(state.get("next_round", 0)),
            logical_cells=logical,
            physical_attempts=physical,
        )


def _select_winner(candidates: Sequence[_EvaluatedCandidate]) -> _EvaluatedCandidate | None:
    if not candidates:
        return None
    best_reward = max(float(item.evaluation.reward) for item in candidates)
    return min(
        (item for item in candidates if float(item.evaluation.reward) == best_reward),
        key=lambda item: item.portfolio.tree_hash,
    )


def _round_history(
    candidates: list[tuple[CandidateRecord, PortfolioRef | None]],
    evaluated: dict[str, _EvaluatedCandidate],
    *,
    parent_reward_before: float,
    accepted_id: str | None,
) -> tuple[ScalarOutcome, ...]:
    rewards = sorted(
        {float(item.evaluation.reward) for item in evaluated.values() if item.evaluation.is_valid},
        reverse=True,
    )
    rank_groups = {reward: index + 1 for index, reward in enumerate(rewards)}
    outcomes = []
    for record, _ in candidates:
        item = evaluated.get(record.candidate_id)
        if item is None or not item.evaluation.is_valid:
            outcomes.append(ScalarOutcome.indeterminate(record.candidate_id, record.direction.direction_id))
            continue
        reward = float(item.evaluation.reward)
        outcomes.append(
            ScalarOutcome(
                candidate_id=record.candidate_id,
                direction_id=record.direction.direction_id,
                reward=reward,
                reward_delta=reward - parent_reward_before,
                rank_group=rank_groups[reward],
                is_valid=True,
                is_accepted=record.candidate_id == accepted_id,
            )
        )
    return tuple(outcomes)


def _history_from_state(state: dict[str, Any]) -> tuple[ScalarOutcome, ...]:
    return tuple(
        ScalarOutcome(
            candidate_id=item["candidate_id"],
            direction_id=item["direction_id"],
            reward=item["reward"],
            reward_delta=item["reward_delta"],
            rank_group=item["rank_group"],
            is_valid=item["is_valid"],
            is_accepted=item["is_accepted"],
        )
        for item in state.get("history", [])
    )


def _plan_from_state(payload: dict[str, Any] | None) -> SearchPlan | None:
    if payload is None:
        return None
    rubrics = tuple(
        RubricHypothesis(item["rubric_id"], item["requirement"], tuple(item["evidence_refs"]))
        for item in payload["rubrics"]
    )
    directions = tuple(
        SearchDirection(
            item["direction_id"],
            item["rubric_id"],
            item["hypothesis"],
            item["capability"],
            tuple(item["target_scope"]),
            item.get("cross_skill_rationale"),
            tuple(item["evidence_refs"]),
            item["novelty_key"],
        )
        for item in payload["directions"]
    )
    return SearchPlan(payload["receipt_version"], rubrics, directions)


def _direction_history_from_state(state: dict[str, Any]) -> tuple[SearchDirection, ...]:
    return tuple(
        SearchDirection(
            item["direction_id"],
            item["rubric_id"],
            item["hypothesis"],
            item["capability"],
            tuple(item["target_scope"]),
            item.get("cross_skill_rationale"),
            tuple(item["evidence_refs"]),
            item["novelty_key"],
        )
        for item in state.get("direction_history", [])
    )


def _stable_hash(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
