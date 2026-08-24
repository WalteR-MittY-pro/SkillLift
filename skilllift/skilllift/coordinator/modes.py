"""SkillLift Mode A/B portfolio coordinator.

Extends the task-portfolio state machine with the SkillLift protocol:
Mode A refines candidate branches with a rubric verifier (zero oracle calls),
Mode B aligns the verifier ranking with the oracle ranking and revises the
receipt. Benchmark differences stay in the injected adapter/planner/generator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ..errors import LLMOutputError, VerifierError
from ..persistence import ExperimentStore
from ..portfolio import (
    PortfolioError,
    PortfolioRef,
    apply_portfolio_patch,
    extract_unified_diff,
    portfolio_manifest,
)
from .task import CoordinatorConfig, TaskPortfolioCoordinator, _EvaluatedCandidate, _direction_history_from_state, _plan_from_state, _round_history
from ..portfolio import CandidateRecord
from ..portfolio import (
    PortfolioContextLimitError,
    PublicTask,
    ScalarOutcome,
    SearchDirection,
    serialize_search_history,
)
from ..ranking import (
    assign_tie_aware_ranks,
    build_criterion_contrast,
    build_rank_contrast,
    rank_score_groups,
    tie_aware_rank_alignment,
)
from ..rubricator import generate_initial_receipt, revise_receipt
from ..schemas import (
    EvoSkill,
    OracleFeedback,
    OracleScore,
    Receipt,
    ReceiptRevisionInput,
    RoundState,
    SkillKey,
    TaskSpec,
    VerifierScore,
)
from ..verifier import score_skills


SKILLLIFT_COORDINATOR_VERSION = "skilllift_portfolio_coordinator_v3"


@dataclass(frozen=True)
class _Branch:
    record: CandidateRecord
    portfolio: PortfolioRef | None
    verifier_score: float | None
    criterion_hits: dict[str, bool]
    refinement_attempts: int = 0


class SkillLiftPortfolioCoordinator(TaskPortfolioCoordinator):
    COORDINATOR_VERSION = SKILLLIFT_COORDINATOR_VERSION

    def __init__(
        self,
        *,
        adapter: Any,
        planner: Any,
        generator: Any,
        store: Any,
        config: CoordinatorConfig | None = None,
        verifier_client: Any | None = None,
        verifier_skill_format: str = "markdown_guide",
    ) -> None:
        super().__init__(adapter=adapter, planner=planner, generator=generator, store=store, config=config)
        self.verifier_client = verifier_client
        self.verifier_skill_format = verifier_skill_format
        self._verifier_diagnostics: list[dict[str, Any]] = []

    def _run_rounds(
        self,
        task: PublicTask,
        parent: PortfolioRef,
        parent_reward: float,
        reward_spec: Any,
        history: tuple[ScalarOutcome, ...],
        state: dict[str, Any],
        algorithm_hash: str,
        workspace: Path,
    ) -> tuple[PortfolioRef, float, tuple[ScalarOutcome, ...], dict[str, Any]]:
        task_spec = _task_spec(task)
        receipt = _receipt_from_state(state)
        if receipt is None:
            try:
                receipt = generate_initial_receipt(
                    task_spec,
                    _seed_reference_material(parent),
                    getattr(self.planner, "client", self.verifier_client),
                    max_input_chars=self.config.max_context_chars,
                )
            except PortfolioContextLimitError:
                state.update(
                    status="evolution_stopped",
                    stop_reason="context_preflight_exceeded",
                    cohort="adaptation_skipped_context",
                )
                self.store.save_state(task.task_id, state)
                return parent, parent_reward, history, state
        state.setdefault("receipt", receipt.to_dict())
        experiment_store = ExperimentStore(self.store.task_root(task.task_id))
        previous_plan = _plan_from_state(state.get("previous_plan"))
        direction_history = _direction_history_from_state(state)
        prohibited = frozenset(state.get("prohibited_novelty_keys", []))
        for round_index in range(int(state.get("next_round", 0)), self.config.max_rounds):
            self._verifier_diagnostics = []
            champion_before_round = parent
            champion_reward_before = parent_reward
            plan = self.store.load_plan(task.task_id, round_index)
            champion_scores = self._score_portfolio(
                task_spec, champion_before_round, receipt, mode="mode_a", stage="mode_a_champion"
            )
            if plan is None:
                try:
                    plan = self._plan(
                        task,
                        parent,
                        previous_plan,
                        history,
                        direction_history,
                        prohibited,
                        [score.to_dict() for score in champion_scores.values()],
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
            branches = [
                self._prepare_branch(task, parent, round_index, index, direction, round_workspace)
                for index, direction in enumerate(directions)
            ]
            branches = [
                self._refine_branch(task, task_spec, branch, direction, receipt, round_workspace)
                for branch, direction in zip(branches, directions)
            ]

            valid_candidates = [
                (branch.record, branch.portfolio) for branch in branches if branch.portfolio is not None
            ]
            evaluated = self._evaluate_candidates(
                task.task_id,
                valid_candidates,
                reward_spec,
                algorithm_hash,
                round_index,
            )
            by_id = {item.record.candidate_id: item for item in evaluated}
            competitive = [item for item in evaluated if item.evaluation.is_valid]
            self._verifier_diagnostics.extend(
                _candidate_diagnostics(branches, evaluated)
            )
            self._verifier_diagnostics = _attach_round_artifact_refs(
                self._verifier_diagnostics, round_index
            )
            branch_verifier = {branch.record.candidate_id: branch.verifier_score for branch in branches}
            champion_verifier = _mean_normalized(champion_scores)
            winner = _select_skilllift_winner(competitive, branch_verifier)
            accepted_id = None
            accept_reason = None
            parent_reward_before = champion_reward_before
            if winner is not None:
                winner_verifier = branch_verifier.get(winner.record.candidate_id)
                if reward_spec.improves(winner.evaluation.reward, parent_reward):
                    accept_reason = "oracle_improved"
                elif (
                    reward_spec.validate(winner.evaluation.reward) == parent_reward
                    and winner_verifier is not None
                    and champion_verifier is not None
                    and winner_verifier > champion_verifier
                ):
                    # Oracle tie: promote the branch whose verifier score is higher
                    # instead of discarding otherwise-good evolution work.
                    accept_reason = "verifier_tiebreak"
            if accept_reason is not None:
                accepted_id = winner.record.candidate_id
                parent = winner.portfolio
                parent_reward = reward_spec.validate(winner.evaluation.reward)
                state["accepted_candidates"].append(
                    {"round_index": round_index, "candidate_id": accepted_id}
                )
                state["no_improvement_rounds"] = 0
            else:
                state["no_improvement_rounds"] = int(state.get("no_improvement_rounds", 0)) + 1

            try:
                receipt, alignment = self._mode_b(
                    task_spec,
                    task,
                    champion_before_round,
                    round_index,
                    receipt,
                    champion_reward_before,
                    reward_spec,
                    branches,
                    evaluated,
                    experiment_store,
                )
            except PortfolioContextLimitError:
                state.update(
                    status="evolution_stopped",
                    stop_reason="context_preflight_exceeded",
                    cohort="adaptation_skipped_context",
                    diagnostics=list(self._verifier_diagnostics),
                )
                self.store.save_state(task.task_id, state)
                break
            self._verifier_diagnostics = _attach_round_artifact_refs(
                self._verifier_diagnostics, round_index
            )
            round_history = _round_history(
                [(branch.record, branch.portfolio) for branch in branches],
                by_id,
                parent_reward_before=parent_reward_before,
                accepted_id=accepted_id,
            )
            history = (*history, *round_history)
            direction_history = (*direction_history, *directions)
            for branch in branches:
                item = by_id.get(branch.record.candidate_id)
                if item is None or not item.evaluation.is_valid or branch.record.candidate_id != accepted_id:
                    prohibited = frozenset({*prohibited, branch.record.direction.novelty_key})

            state.update(
                parent_reward=parent_reward,
                next_round=round_index + 1,
                history=serialize_search_history(history),
                direction_history=[item.to_dict() for item in direction_history],
                prohibited_novelty_keys=sorted(prohibited),
                previous_plan=plan.to_dict(),
                receipt=receipt.to_dict(),
                rank_alignment=alignment,
                diagnostics=list(self._verifier_diagnostics),
            )
            stop_reason = None
            if not competitive:
                context_limited = all(
                    branch.record.failure_kind == "context_preflight_exceeded" for branch in branches
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
                    "verifier_scores": {
                        branch.record.candidate_id: branch.verifier_score for branch in branches
                    },
                    "rank_alignment": alignment,
                    "receipt_version": receipt.version,
                    "accept_reason": accept_reason,
                    "champion_verifier_score": champion_verifier,
                    "diagnostics": list(self._verifier_diagnostics),
                },
            )
            self.store.save_state(task.task_id, state)
            previous_plan = plan
            if stop_reason:
                break
        return parent, parent_reward, tuple(history), state

    def _plan(
        self,
        task: PublicTask,
        parent: PortfolioRef,
        previous_plan: Any,
        history: tuple[ScalarOutcome, ...],
        direction_history: Any,
        prohibited: frozenset[str],
        verifier_feedback: list[dict[str, Any]],
    ) -> Any:
        """Planners predating the verifier-feedback contract keep working without it."""
        parameters = getattr(self.planner.plan, "__signature_params__", None)
        if parameters is None:
            import inspect

            try:
                parameters = inspect.signature(self.planner.plan).parameters
            except (TypeError, ValueError):
                parameters = {}
        if "verifier_feedback" in parameters or any(
            param.kind == param.VAR_KEYWORD for param in parameters.values()
        ):
            return self.planner.plan(
                task,
                parent,
                previous_plan,
                history,
                direction_history,
                prohibited,
                verifier_feedback=verifier_feedback or None,
            )
        return self.planner.plan(task, parent, previous_plan, history, direction_history, prohibited)

    def _prepare_branch(
        self,
        task: PublicTask,
        parent: PortfolioRef,
        round_index: int,
        index: int,
        direction: SearchDirection,
        round_workspace: Path,
    ) -> _Branch:
        # The first draft persists the original direction (resume-stable); the
        # planner already saw champion hits via verifier_feedback, and the
        # refinement pass re-injects branch-local hits in-memory.
        record, portfolio = self._prepare_candidate(task, parent, round_index, index, direction, round_workspace)
        return _Branch(record=record, portfolio=portfolio, verifier_score=None, criterion_hits={})

    def _refine_branch(
        self,
        task: PublicTask,
        task_spec: TaskSpec,
        branch: _Branch,
        direction: SearchDirection,
        receipt: Receipt,
        round_workspace: Path,
    ) -> _Branch:
        """Mode A: verifier-guided branch-local refinement, zero oracle calls."""
        if branch.portfolio is None:
            return branch
        current = branch
        current_score = self._branch_score(
            task_spec, current.portfolio, receipt, mode="mode_a", stage="mode_a_branch"
        )
        if current_score is not None:
            current = _Branch(
                record=current.record,
                portfolio=current.portfolio,
                verifier_score=current_score.normalized_score,
                criterion_hits=current_score.criterion_hits,
            )
        threshold = self.config.mode_a_min_score_threshold
        for _ in range(self.config.mode_a_iters):
            if current.verifier_score is not None and current.verifier_score >= threshold:
                break
            refined = self._patch_refinement(task, current, direction, receipt, round_workspace)
            if refined is None:
                break
            refined_score = self._branch_score(
                task_spec, refined, receipt, mode="mode_a", stage="mode_a_refinement"
            )
            if refined_score is None:
                break
            if refined_score.normalized_score >= (current.verifier_score or 0.0):
                current = _Branch(
                    record=current.record,
                    portfolio=refined,
                    verifier_score=refined_score.normalized_score,
                    criterion_hits=refined_score.criterion_hits,
                    refinement_attempts=current.refinement_attempts + 1,
                )
            else:
                break
        return current

    def _patch_refinement(
        self,
        task: PublicTask,
        branch: _Branch,
        direction: SearchDirection,
        receipt: Receipt,
        round_workspace: Path,
    ) -> PortfolioRef | None:
        assert branch.portfolio is not None
        try:
            raw_patch = self.generator.generate(
                task,
                _refined_direction(direction, branch.criterion_hits, receipt),
                branch.portfolio,
            )
            patch = extract_unified_diff(raw_patch)
            destination = (
                round_workspace
                / f"{branch.record.candidate_id}-refined-{branch.refinement_attempts + 1}"
            )
            result = apply_portfolio_patch(
                branch.portfolio,
                patch,
                destination,
                target_scope=direction.target_scope,
                new_skill_names=(),
            )
            return result.portfolio
        except PortfolioError as exc:
            self._verifier_diagnostics.append(
                {
                    "stage": "mode_a_refinement_patch",
                    "mode": "mode_a",
                    "candidate_id": branch.record.candidate_id,
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                    "artifact_ref": f"candidates/{branch.record.candidate_id}",
                }
            )
            return None

    def _score_portfolio(
        self,
        task_spec: TaskSpec,
        portfolio: PortfolioRef,
        receipt: Receipt,
        mode: str = "mode_a",
        stage: str | None = None,
    ) -> dict[SkillKey, VerifierScore]:
        skills = _portfolio_to_skills(portfolio)
        if not skills:
            return {}
        try:
            scores = score_skills(
                task_spec,
                skills,
                receipt,
                self.verifier_client,
                mode=mode,
                skill_format=self.verifier_skill_format,
            )
            for key, score in scores.items():
                if not score.rationale.startswith(
                    "fallback_invalid_verifier_output:"
                ):
                    continue
                self._verifier_diagnostics.append(
                    {
                        "stage": stage or mode,
                        "mode": mode,
                        "skill": key.token(),
                        "error_type": "deterministic_verifier_fallback",
                        "error": score.rationale,
                        "artifact_ref": None,
                    }
                )
            return scores
        except (LLMOutputError, VerifierError, RuntimeError, OSError, TimeoutError) as exc:
            self._record_verifier_error(stage or mode, mode, exc)
            return {}

    def _record_verifier_error(
        self, stage: str, mode: str, exc: Exception
    ) -> None:
        self._verifier_diagnostics.append(
            {
                "stage": stage,
                "mode": mode,
                "error_type": exc.__class__.__name__,
                "error": str(exc),
                "artifact_ref": None,
            }
        )

    def _branch_score(
        self,
        task_spec: TaskSpec,
        portfolio: PortfolioRef,
        receipt: Receipt,
        *,
        mode: str = "mode_a",
        stage: str | None = None,
    ) -> VerifierScore | None:
        scores = self._score_portfolio(
            task_spec, portfolio, receipt, mode=mode, stage=stage
        )
        if not scores:
            return None
        mean_normalized = sum(score.normalized_score for score in scores.values()) / len(scores)
        raw = sum(score.raw_score for score in scores.values())
        merged_hits = _merged_hits(scores)
        return VerifierScore(
            skill=next(iter(scores)),
            receipt_version=receipt.version,
            criterion_hits=merged_hits,
            raw_score=raw,
            normalized_score=mean_normalized,
            positive_requirement_missing=[
                rubric.rubric_id
                for rubric in receipt.rubrics
                if rubric.points > 0 and not merged_hits.get(rubric.rubric_id, False)
            ],
            negative_violation_present=[
                rubric.rubric_id
                for rubric in receipt.rubrics
                if rubric.points < 0 and merged_hits.get(rubric.rubric_id, False)
            ],
        )

    def _mode_b(
        self,
        task_spec: TaskSpec,
        task: PublicTask,
        parent: PortfolioRef,
        round_index: int,
        receipt: Receipt,
        parent_reward: float,
        reward_spec: Any,
        branches: Sequence[_Branch],
        evaluated: Sequence[_EvaluatedCandidate],
        experiment_store: ExperimentStore,
    ) -> tuple[Receipt, float | None]:
        """Align a frozen pre-round champion and candidates under public evidence."""
        items: dict[str, PortfolioRef | None] = {"champion": parent}
        rewards: dict[str, float] = {"champion": parent_reward}
        for item in evaluated:
            if item.evaluation.is_valid:
                items[item.record.candidate_id] = item.portfolio
                rewards[item.record.candidate_id] = item.evaluation.reward
        if len(items) < 2:
            experiment_store.append_event(
                {
                    "event": "mode_b_skipped",
                    "outer_round": round_index,
                    "stage": "mode_b",
                    "reason": "fewer_than_two_valid_portfolios",
                    "artifact_ref": str(experiment_store.exp_dir),
                }
            )
            experiment_store.save_receipt(
                receipt, f"outer_{round_index:03d}_mode_b_final"
            )
            return receipt, None

        current_receipt = receipt
        best_receipt = receipt
        best_alignment: float | None = None
        alignment: float | None = None
        rubricator_client = getattr(self.planner, "client", None)
        for mode_iter in range(self.config.mode_b_iters):
            item_keys = {
                name: SkillKey(slot=index, version=current_receipt.version)
                for index, name in enumerate(sorted(items))
            }
            verifier_scores: dict[SkillKey, VerifierScore] = {}
            skills: dict[SkillKey, EvoSkill] = {}
            for name, key in item_keys.items():
                portfolio = items.get(name)
                if portfolio is None:
                    continue
                score = self._branch_score(
                    task_spec,
                    portfolio,
                    current_receipt,
                    mode="mode_b",
                    stage=f"mode_b_iter_{mode_iter}",
                )
                if score is None:
                    continue
                score.skill = key
                verifier_scores[key] = score
                skills[key] = _portfolio_to_single_skill(portfolio, key)
            oracle_scores = {
                item_keys[name]: OracleScore(
                    skill=item_keys[name],
                    oracle_score=float(reward),
                    oracle_pass=int(reward_spec.is_terminal(float(reward))),
                    feedback=OracleFeedback(summary="portfolio oracle reward"),
                )
                for name, reward in rewards.items()
            }
            common = sorted(set(verifier_scores) & set(oracle_scores))
            if len(common) < 2:
                experiment_store.append_event(
                    {
                        "event": "mode_b_iteration_skipped",
                        "outer_round": round_index,
                        "mode_iter": mode_iter,
                        "stage": "mode_b",
                        "reason": "fewer_than_two_verifier_scores",
                        "artifact_ref": str(experiment_store.exp_dir),
                    }
                )
                break
            verifier_values = {
                key: verifier_scores[key].normalized_score for key in common
            }
            oracle_values = {key: oracle_scores[key].oracle_score for key in common}
            for key, rank in assign_tie_aware_ranks(verifier_values).items():
                verifier_scores[key].rank = rank
            for key, rank in assign_tie_aware_ranks(oracle_values).items():
                oracle_scores[key].rank = rank
            verifier_groups = rank_score_groups(verifier_values)
            oracle_groups = rank_score_groups(oracle_values)
            verifier_rank = [key for group in verifier_groups for key in group]
            oracle_rank = [key for group in oracle_groups for key in group]
            alignment = tie_aware_rank_alignment(verifier_values, oracle_values)
            contrast = build_rank_contrast(verifier_values, oracle_values)
            contrast["criterion_contrast"] = build_criterion_contrast(
                verifier_scores, current_receipt
            )
            experiment_store.save_oracle_scores(
                oracle_scores, f"outer_{round_index:03d}_mode_b_batch"
            )
            experiment_store.save_oracle_scores(
                oracle_scores, f"outer_{round_index:03d}_mode_b_iter_{mode_iter:03d}"
            )
            experiment_store.save_verifier_scores(
                verifier_scores, f"outer_{round_index:03d}_mode_b_iter_{mode_iter:03d}"
            )
            self._verifier_diagnostics = _attach_round_artifact_refs(
                self._verifier_diagnostics, round_index
            )
            experiment_store.save_round_state(
                RoundState(
                    step=round_index,
                    outer_round=round_index,
                    mode="mode_b",
                    mode_iter=mode_iter,
                    skills=skills,
                    receipt=current_receipt,
                    verifier_scores=verifier_scores,
                    oracle_scores=oracle_scores,
                    verifier_rank=verifier_rank,
                    oracle_rank=oracle_rank,
                    rank_alignment=alignment,
                    diagnostics=list(self._verifier_diagnostics),
                )
            )
            experiment_store.save_receipt(
                current_receipt, f"outer_{round_index:03d}_mode_b_iter_{mode_iter:03d}"
            )
            if alignment is not None and (
                best_alignment is None or alignment > best_alignment
            ):
                best_alignment = alignment
                best_receipt = current_receipt
            if (
                alignment is None
                or alignment >= self.config.rank_alignment_threshold
                or rubricator_client is None
            ):
                break
            if mode_iter == self.config.mode_b_iters - 1:
                break
            revision_input = ReceiptRevisionInput(
                task=task_spec,
                receipt=current_receipt,
                skills=skills,
                verifier_scores=verifier_scores,
                oracle_scores=oracle_scores,
                verifier_rank=verifier_rank,
                oracle_rank=oracle_rank,
                rank_alignment=alignment if alignment is not None else 0.0,
                oracle_contrast=contrast,
            )
            experiment_store.current_outer_round = round_index
            try:
                current_receipt = revise_receipt(
                    revision_input,
                    rubricator_client,
                    experiment_store,
                    max_input_chars=self.config.max_context_chars,
                )
            except LLMOutputError as exc:
                # Receipt revision is best-effort: a flaky gateway must not lose
                # the round. Keep the best-aligned receipt seen so far.
                experiment_store.append_event(
                    {
                        "event": "receipt_revision_failed",
                        "outer_round": round_index,
                        "mode_iter": mode_iter,
                        "stage": "mode_b_receipt_revision",
                        "artifact_ref": str(experiment_store.exp_dir),
                        "error_type": exc.__class__.__name__,
                        "error": str(exc),
                    }
                )
                self._verifier_diagnostics.append(
                    {
                        "stage": "mode_b_receipt_revision",
                        "mode": "mode_b",
                        "error_type": exc.__class__.__name__,
                        "error": str(exc),
                        "artifact_ref": str(experiment_store.exp_dir),
                    }
                )
                break
        selected_alignment = best_alignment if best_alignment is not None else alignment
        if best_alignment is not None:
            current_receipt = best_receipt
        experiment_store.save_receipt(
            current_receipt, f"outer_{round_index:03d}_mode_b_final"
        )
        return current_receipt, selected_alignment


def _select_skilllift_winner(
    competitive: Sequence[_EvaluatedCandidate],
    branch_verifier: dict[str, float | None],
) -> _EvaluatedCandidate | None:
    """Oracle first, verifier as tie-break among oracle-equal candidates."""
    if not competitive:
        return None
    best_reward = max(float(item.evaluation.reward) for item in competitive)
    tied = [
        item
        for item in competitive
        if abs(float(item.evaluation.reward) - best_reward) <= 1e-9
    ]
    return max(
        tied,
        key=lambda item: (
            branch_verifier.get(item.record.candidate_id)
            if branch_verifier.get(item.record.candidate_id) is not None
            else float("-inf"),
            # Stable identity is only a final choice after all scores tie; it
            # never creates an improvement over the frozen champion.
            "".join(reversed(item.record.candidate_id)),
        ),
        default=None,
    )


def _mean_normalized(scores: dict[SkillKey, VerifierScore]) -> float | None:
    if not scores:
        return None
    return sum(score.normalized_score for score in scores.values()) / len(scores)


def _candidate_diagnostics(
    branches: Sequence[_Branch], evaluated: Sequence[_EvaluatedCandidate]
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for branch in branches:
        if branch.record.failure_kind:
            diagnostics.append(
                {
                    "stage": "candidate_generation",
                    "mode": "mode_a",
                    "candidate_id": branch.record.candidate_id,
                    "error_type": branch.record.failure_kind,
                    "error": branch.record.error,
                    "artifact_ref": f"candidates/{branch.record.candidate_id}",
                }
            )
    for item in evaluated:
        if not item.evaluation.is_valid:
            diagnostics.append(
                {
                    "stage": "oracle_evaluation",
                    "mode": "oracle",
                    "candidate_id": item.record.candidate_id,
                    "error_type": "infrastructure_error",
                    "error": item.evaluation.infrastructure_error,
                    "artifact_ref": item.evaluation.artifact_ref,
                }
            )
    return diagnostics


def _attach_round_artifact_refs(
    diagnostics: Sequence[dict[str, Any]], round_index: int
) -> list[dict[str, Any]]:
    fallback_ref = f"rounds/round-{round_index:03d}/round_result.json"
    attached: list[dict[str, Any]] = []
    for item in diagnostics:
        artifact_ref = item.get("artifact_ref") or fallback_ref
        if isinstance(artifact_ref, str) and artifact_ref.startswith("candidates/"):
            artifact_ref = f"rounds/round-{round_index:03d}/{artifact_ref}"
        attached.append(
            {
                **item,
                "stage": item.get("stage") or "unknown",
                "error_type": item.get("error_type") or "unknown",
                "error": item.get("error") or "unspecified error",
                "artifact_ref": artifact_ref,
            }
        )
    return attached


def _merged_hits(scores: dict[SkillKey, VerifierScore]) -> dict[str, bool]:
    merged: dict[str, bool] = {}
    for score in scores.values():
        for rubric_id, hit in score.criterion_hits.items():
            merged[rubric_id] = merged.get(rubric_id, False) or hit
    return merged


def _task_spec(task: PublicTask) -> TaskSpec:
    return TaskSpec(task_name=task.task_id, task_description=task.text)


def _seed_reference_material(portfolio: PortfolioRef) -> str:
    """Return the complete seed portfolio without dropping any skill files."""
    entries = portfolio_manifest(portfolio.root)
    sections = [
        "[Seed Portfolio Manifest]\n"
        + json.dumps(entries, ensure_ascii=False, sort_keys=True)
    ]
    skill_entries = [entry for entry in entries if str(entry["path"]).endswith("SKILL.md")]
    for entry in skill_entries:
        path = str(entry["path"])
        content = (portfolio.root / path).read_text(encoding="utf-8")
        sections.append(f"[Seed File: {path}]\n{content}")
    for entry in entries:
        path = str(entry["path"])
        if path.endswith("SKILL.md"):
            continue
        content = (portfolio.root / path).read_text(encoding="utf-8")
        sections.append(f"[Seed Auxiliary File: {path}]\n{content}")
    return "\n\n".join(sections)


def _receipt_from_state(state: dict[str, Any]) -> Receipt | None:
    payload = state.get("receipt")
    if not payload:
        return None
    try:
        return Receipt.from_dict(payload)
    except (KeyError, TypeError, ValueError):
        return None


def _portfolio_to_skills(portfolio: PortfolioRef) -> dict[SkillKey, EvoSkill]:
    entries = portfolio_manifest(portfolio.root)
    by_top: dict[str, dict[str, str]] = {}
    for entry in entries:
        path = str(entry["path"])
        top = path.split("/", 1)[0]
        by_top.setdefault(top, {})[path] = (portfolio.root / path).read_text(encoding="utf-8")
    skills: dict[SkillKey, EvoSkill] = {}
    for slot, (name, files) in enumerate(sorted(by_top.items())):
        skills[SkillKey(slot=slot, version=1)] = EvoSkill(
            key=SkillKey(slot=slot, version=1),
            skill_name=name,
            files=files,
        )
    return skills


def _portfolio_to_single_skill(portfolio: PortfolioRef, key: SkillKey) -> EvoSkill:
    files = {
        str(entry["path"]): (portfolio.root / str(entry["path"])).read_text(encoding="utf-8")
        for entry in portfolio_manifest(portfolio.root)
    }
    return EvoSkill(key=key, skill_name=portfolio.root.name, files=files)


def _refined_direction(direction: SearchDirection, hits: dict[str, bool], receipt: Receipt) -> SearchDirection:
    targets = []
    for rubric in receipt.rubrics:
        hit = hits.get(rubric.rubric_id, False)
        if rubric.points > 0 and not hit:
            targets.append(f"MISSING_REQUIRED_BEHAVIOR: {rubric.criterion}")
        elif rubric.points < 0 and hit:
            targets.append(f"FORBIDDEN_BEHAVIOR_PRESENT: {rubric.criterion}")
    if not targets:
        return direction
    return SearchDirection(
        direction_id=direction.direction_id,
        rubric_id=direction.rubric_id,
        hypothesis=f"{direction.hypothesis}\nVerifier feedback to address: " + "; ".join(targets),
        capability=direction.capability,
        target_scope=direction.target_scope,
        cross_skill_rationale=direction.cross_skill_rationale,
        evidence_refs=direction.evidence_refs,
        novelty_key=direction.novelty_key,
    )
