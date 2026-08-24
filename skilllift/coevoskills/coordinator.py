from __future__ import annotations

from typing import Any

from .config import CoEvoSkillsConfig
from .persistence import ExperimentStore
from .protocols import (
    ArtifactRolloutBackend,
    OracleBackend,
    SkillGeneratorBackend,
    SurrogateRuntimeBackend,
    SurrogateVerifierBackend,
    TokenBudgetBackend,
)
from .schemas import (
    ExperimentResult,
    LoopState,
    SkillVersion,
    TaskInput,
    TestAssertion,
    TestSuiteVersion,
)


def run_experiment(
    task: TaskInput,
    config: CoEvoSkillsConfig,
    *,
    generator: SkillGeneratorBackend,
    verifier: SurrogateVerifierBackend,
    rollout: ArtifactRolloutBackend,
    oracle: OracleBackend,
    surrogate_runtime: SurrogateRuntimeBackend,
    store: ExperimentStore,
    budget: TokenBudgetBackend | None = None,
) -> ExperimentResult:
    if config.token_budget > 0 and budget is None:
        raise ValueError("token_budget requires a TokenBudgetBackend")
    checkpoint = store.load_checkpoint(task, config)
    if checkpoint is None:
        store.reset()
        store.save_initial(task, config)
        current = generator.initialize(task)
        store.save_skill(current)
        best_skill = current
        best_score: float | None = None
        suite: TestSuiteVersion | None = None
        surrogate_retries = 0
        oracle_interventions = 0
        step = 0
        turns_completed = 0
        stop_reason = "unknown"
        if _budget_exhausted(
            budget,
            turns_completed,
            "initialization",
            min_turns=config.min_turns,
        ):
            stop_reason = "token_budget_exhausted"
        _save_checkpoint(
            store,
            task,
            config,
            generator,
            current,
            best_skill,
            best_score,
            suite,
            surrogate_retries,
            oracle_interventions,
            step,
            turns_completed,
            stop_reason,
        )
    else:
        if checkpoint.get("phase") == "complete":
            return _result_from_dict(checkpoint["result"])
        current = _skill_from_dict(checkpoint["current_skill"])
        best_skill = _skill_from_dict(checkpoint["best_skill"])
        best_score = checkpoint.get("best_score")
        suite_payload = checkpoint.get("suite")
        suite = _suite_from_dict(suite_payload) if suite_payload else None
        surrogate_retries = int(checkpoint.get("surrogate_retries", 0))
        oracle_interventions = int(checkpoint.get("oracle_interventions", 0))
        step = int(checkpoint.get("step", 0))
        turns_completed = int(checkpoint.get("turns_completed", 0))
        stop_reason = str(checkpoint.get("stop_reason", "unknown"))
        restore_generator = getattr(generator, "restore_state", None)
        if callable(restore_generator):
            restore_generator(checkpoint.get("generator_state", {}))
        store.append_event(
            {
                "event": "resumed",
                "step": step,
                "turns_completed": turns_completed,
            }
        )

    while stop_reason == "unknown":
        if turns_completed >= config.max_turns:
            stop_reason = "max_turns_reached"
            _state(store, step, current, suite, surrogate_retries, oracle_interventions, generator.context_ratio(), stop_reason)
            _save_checkpoint(
                store, task, config, generator, current, best_skill, best_score,
                suite, surrogate_retries, oracle_interventions, step,
                turns_completed, stop_reason,
            )
            break
        context_ratio = generator.context_ratio()
        if context_ratio > config.context_cap_ratio:
            stop_reason = "context_cap_reached"
            _state(store, step, current, suite, surrogate_retries, oracle_interventions, context_ratio, stop_reason)
            _save_checkpoint(
                store, task, config, generator, current, best_skill, best_score,
                suite, surrogate_retries, oracle_interventions, step,
                turns_completed, stop_reason,
            )
            break

        snapshot = rollout.rollout(task, current)
        store.save_snapshot(step, snapshot)
        if suite is None:
            suite = verifier.initialize_suite(task, snapshot)
            store.save_suite(suite)
            store.append_event({"event": "suite_initialized", "step": step, "suite": suite.token})

        surrogate_result = surrogate_runtime.run(suite, snapshot)
        store.save_surrogate_result(step, surrogate_result)

        if not surrogate_result.passed:
            if surrogate_retries >= config.max_surrogate_retries:
                turns_completed += 1
                stop_reason = (
                    "token_budget_exhausted"
                    if _budget_exhausted(
                        budget,
                        turns_completed,
                        "surrogate_retry_rejected",
                        min_turns=config.min_turns,
                    )
                    else "surrogate_retry_budget_exhausted"
                )
                _state(store, step, current, suite, surrogate_retries, oracle_interventions, generator.context_ratio(), stop_reason)
                _save_checkpoint(
                    store, task, config, generator, current, best_skill, best_score,
                    suite, surrogate_retries, oracle_interventions, step,
                    turns_completed, stop_reason,
                )
                break
            diagnostic = verifier.diagnose(task, snapshot, suite, surrogate_result)
            store.save_diagnostic(step, diagnostic)
            current = generator.refine(task, current, diagnostic)
            surrogate_retries += 1
            step += 1
            turns_completed += 1
            store.save_skill(current)
            transition = (
                "token_budget_exhausted"
                if _budget_exhausted(
                    budget,
                    turns_completed,
                    "skill_refined",
                    min_turns=config.min_turns,
                )
                else "skill_refined"
            )
            _state(store, step, current, suite, surrogate_retries, oracle_interventions, generator.context_ratio(), transition)
            _save_checkpoint(
                store, task, config, generator, current, best_skill, best_score,
                suite, surrogate_retries, oracle_interventions, step,
                turns_completed, transition if transition == "token_budget_exhausted" else "unknown",
            )
            if transition == "token_budget_exhausted":
                stop_reason = transition
                break
            continue

        oracle_result = oracle.evaluate(task, current)
        oracle_interventions += 1
        turns_completed += 1
        store.save_oracle(oracle_interventions, oracle_result)
        if best_score is None or oracle_result.score > best_score:
            best_score = oracle_result.score
            best_skill = current

        budget_exhausted = _budget_exhausted(
            budget,
            turns_completed,
            "oracle_evaluated",
            min_turns=config.min_turns,
        )
        if oracle_result.passed and turns_completed >= config.min_turns:
            stop_reason = "oracle_passed"
            _state(store, step, current, suite, surrogate_retries, oracle_interventions, generator.context_ratio(), stop_reason)
            _save_checkpoint(
                store, task, config, generator, current, best_skill, best_score,
                suite, surrogate_retries, oracle_interventions, step,
                turns_completed, stop_reason,
            )
            break
        if budget_exhausted:
            stop_reason = "token_budget_exhausted"
            _state(store, step, current, suite, surrogate_retries, oracle_interventions, generator.context_ratio(), stop_reason)
            _save_checkpoint(
                store, task, config, generator, current, best_skill, best_score,
                suite, surrogate_retries, oracle_interventions, step,
                turns_completed, stop_reason,
            )
            break
        if oracle_result.passed:
            step += 1
            _state(
                store,
                step,
                current,
                suite,
                surrogate_retries,
                oracle_interventions,
                generator.context_ratio(),
                "minimum_turns_pending",
            )
            _save_checkpoint(
                store, task, config, generator, current, best_skill, best_score,
                suite, surrogate_retries, oracle_interventions, step,
                turns_completed, "unknown",
            )
            continue
        if oracle_interventions >= config.max_oracle_interventions:
            stop_reason = "oracle_budget_exhausted"
            _state(store, step, current, suite, surrogate_retries, oracle_interventions, generator.context_ratio(), stop_reason)
            _save_checkpoint(
                store, task, config, generator, current, best_skill, best_score,
                suite, surrogate_retries, oracle_interventions, step,
                turns_completed, stop_reason,
            )
            break

        generator.note_oracle_failure()
        suite = verifier.escalate_suite(task, snapshot, suite)
        store.save_suite(suite)
        step += 1
        transition = (
            "token_budget_exhausted"
            if _budget_exhausted(
                budget,
                turns_completed,
                "suite_escalated",
                min_turns=config.min_turns,
            )
            else "suite_escalated"
        )
        _state(store, step, current, suite, surrogate_retries, oracle_interventions, generator.context_ratio(), transition)
        _save_checkpoint(
            store, task, config, generator, current, best_skill, best_score,
            suite, surrogate_retries, oracle_interventions, step,
            turns_completed, transition if transition == "token_budget_exhausted" else "unknown",
        )
        if transition == "token_budget_exhausted":
            stop_reason = transition
            break

    result = ExperimentResult(
        final_skill=current,
        best_skill=best_skill,
        best_oracle_score=best_score,
        stop_reason=stop_reason,
        surrogate_retries=surrogate_retries,
        oracle_interventions=oracle_interventions,
        steps=step,
        summary={
            "initial_suite_policy": config.initial_suite_policy,
            "surrogate_counter_policy": config.surrogate_counter_policy,
            "min_turns": config.min_turns,
            "max_turns": config.max_turns,
            "turns_completed": turns_completed,
            "token_budget": config.token_budget,
            "stop_priority": ["min_turns", "token_budget", "max_turns"],
        },
    )
    store.save_result(result)
    payload = _checkpoint_payload(
        task,
        config,
        generator,
        current,
        best_skill,
        best_score,
        suite,
        surrogate_retries,
        oracle_interventions,
        step,
        turns_completed,
        stop_reason,
    )
    payload.update(phase="complete", result=result.to_dict())
    store.save_checkpoint(payload)
    return result


def _save_checkpoint(
    store: ExperimentStore,
    task: TaskInput,
    config: CoEvoSkillsConfig,
    generator: SkillGeneratorBackend,
    current: SkillVersion,
    best_skill: SkillVersion,
    best_score: float | None,
    suite: TestSuiteVersion | None,
    surrogate_retries: int,
    oracle_interventions: int,
    step: int,
    turns_completed: int,
    stop_reason: str,
) -> None:
    store.save_checkpoint(
        _checkpoint_payload(
            task,
            config,
            generator,
            current,
            best_skill,
            best_score,
            suite,
            surrogate_retries,
            oracle_interventions,
            step,
            turns_completed,
            stop_reason,
        )
    )


def _checkpoint_payload(
    task: TaskInput,
    config: CoEvoSkillsConfig,
    generator: SkillGeneratorBackend,
    current: SkillVersion,
    best_skill: SkillVersion,
    best_score: float | None,
    suite: TestSuiteVersion | None,
    surrogate_retries: int,
    oracle_interventions: int,
    step: int,
    turns_completed: int,
    stop_reason: str,
) -> dict[str, Any]:
    export_generator = getattr(generator, "export_state", None)
    generator_state = export_generator() if callable(export_generator) else {}
    return {
        "schema_version": 1,
        "phase": "loop",
        "task": task.to_dict(),
        "config": config.to_dict(),
        "current_skill": current.to_dict(),
        "best_skill": best_skill.to_dict(),
        "best_score": best_score,
        "suite": suite.to_dict() if suite else None,
        "surrogate_retries": surrogate_retries,
        "oracle_interventions": oracle_interventions,
        "step": step,
        "turns_completed": turns_completed,
        "stop_reason": stop_reason,
        "generator_state": generator_state,
    }


def _skill_from_dict(payload: dict[str, Any]) -> SkillVersion:
    return SkillVersion(
        version=int(payload["version"]),
        name=str(payload["name"]),
        files={str(key): str(value) for key, value in payload["files"].items()},
        entrypoint=payload.get("entrypoint"),
        metadata=dict(payload.get("metadata", {})),
    )


def _suite_from_dict(payload: dict[str, Any]) -> TestSuiteVersion:
    return TestSuiteVersion(
        version=int(payload["version"]),
        code=str(payload["code"]),
        assertions=[
            TestAssertion(
                assertion_id=str(item["assertion_id"]),
                description=str(item["description"]),
            )
            for item in payload.get("assertions", [])
        ],
        metadata=dict(payload.get("metadata", {})),
    )


def _result_from_dict(payload: dict[str, Any]) -> ExperimentResult:
    return ExperimentResult(
        final_skill=_skill_from_dict(payload["final_skill"]),
        best_skill=_skill_from_dict(payload["best_skill"]),
        best_oracle_score=payload.get("best_oracle_score"),
        stop_reason=str(payload["stop_reason"]),
        surrogate_retries=int(payload["surrogate_retries"]),
        oracle_interventions=int(payload["oracle_interventions"]),
        steps=int(payload["steps"]),
        summary=dict(payload.get("summary", {})),
    )


def _budget_exhausted(
    budget: TokenBudgetBackend | None,
    turns_completed: int,
    stage: str,
    *,
    min_turns: int,
) -> bool:
    limit_reached = bool(
        budget
        and budget.checkpoint(
            turns_completed=turns_completed,
            stage=stage,
        )
    )
    return turns_completed >= min_turns and limit_reached


def _state(
    store: ExperimentStore,
    step: int,
    skill: SkillVersion,
    suite: TestSuiteVersion | None,
    surrogate_retries: int,
    oracle_interventions: int,
    context_ratio: float,
    transition: str,
) -> None:
    state = LoopState(
        step=step,
        skill_version=skill.version,
        suite_version=suite.version if suite else None,
        surrogate_retries=surrogate_retries,
        oracle_interventions=oracle_interventions,
        context_ratio=context_ratio,
        transition=transition,
    )
    store.save_state(state)
    store.append_event({"event": "transition", **state.to_dict()})
