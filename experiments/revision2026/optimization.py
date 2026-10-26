from __future__ import annotations

import json
from pathlib import Path
import shutil
from types import SimpleNamespace

from .common import append_event, endpoint, write_json


def clients(model, ledger, roles=("rubricator", "skill_generator", "verifier"), *, protocol=None, journal=None):
    """Framework LLM clients with long transient retries and journal replay after a restart."""
    from skilllift.llm_client import LLMClient
    from .resilience import ResilientLLM, policy_from
    ep = endpoint(model)
    policy = policy_from(protocol)
    log = lambda event: append_event(ledger, event)
    failure_state = {}
    return {role: ResilientLLM(LLMClient(ep["BASE_URL"], ep["API_KEY"], ep["MODEL"], role=role, use_stream=True, max_retries=0,
                                         usage_callback=lambda event: append_event(ledger, {"kind": "framework_usage", **event})),
                               policy, journal, log, failure_state)
            for role in roles}


def check_training_failure(oracle, llms=()):
    if getattr(oracle, "failed_error", None):
        raise RuntimeError(oracle.failed_error)
    for client in llms:
        error = getattr(client, "failure_state", {}).get("error")
        if error:
            raise error


def run_skilllift(oracle, protocol, root, model, ledger, journal=None):
    from skilllift import SkillLiftPortfolioCoordinator
    from skilllift.coordinator import CoordinatorConfig
    from skilllift.portfolio import PortfolioStore, LLMRubricatorPlanner
    from skilllift_eval.runners.bounded_edits_skill_generator import BoundedEditsSkillGenerator
    llms = clients(model, ledger, protocol=protocol, journal=journal)
    config = protocol["skilllift"]
    result = SkillLiftPortfolioCoordinator(
        adapter=oracle, planner=LLMRubricatorPlanner(llms["rubricator"], max_input_chars=config["max_context_chars"]),
        generator=BoundedEditsSkillGenerator(llms["skill_generator"], max_input_chars=config["max_context_chars"]),
        verifier_client=llms["verifier"], store=PortfolioStore(Path(root) / "coordinator"), config=CoordinatorConfig(**config),
    ).evolve(oracle.family)
    check_training_failure(oracle, llms.values())
    if result.stop_reason == "anchor_indeterminate":
        raise RuntimeError("SkillLift anchor did not yield a valid oracle result")
    # Never report result.final_score: legacy internal final uses training instances
    # and a maximum. The outer driver evaluates every frozen winner independently.
    return result.winner_portfolio.root, {"stop_reason": result.stop_reason, "rounds": result.rounds_completed,
                                         "internal_final_is_training": True}


DIRECT_ORACLE_INSTRUCTION = (
    "Improve this reusable skill using actual oracle execution feedback; there is no rubric. "
    "Edit the CURRENT skill (current_skill_hash). Its own execution trace and per-criterion oracle outcomes are in "
    "current_skill_feedback. previous_round_candidates are earlier edits that were executed by the oracle; they may "
    "differ from the current skill, so use their outcomes as evidence but respect their hashes. "
    "already_proposed_this_round lists sibling edits generated for this round that have not been executed yet: "
    "take a clearly different approach from them (address different failed criteria or failure modes). "
    "Do not invent a rubric, do not hard-code answers, and keep the skill reusable for other instances of this task family.\n"
)


def _criterion_pass_fraction(feedback):
    """Oracle-derived tie-break: share of verifier criteria that passed, or None if unavailable."""
    rows = [row for row in (feedback or {}).get("oracle_criterion_outcomes") or [] if isinstance(row, dict)]
    if not rows:
        return None
    return sum(str(row.get("status", "")).lower() == "passed" for row in rows) / len(rows)


def _ledger_tokens(ledger):
    """Cumulative recorded usage at this moment; missing usage stays unknown, never zero.

    Archived ledgers of interrupted attempts of the same cell are included, so totals count
    every paid call once. Curve points produced while replaying a journal therefore carry the
    pre-crash spend early; they are flagged with oracle_replayed_cumulative > 0."""
    totals = {"framework_tokens": 0, "oracle_tokens": 0, "records_without_usage": 0}
    path = Path(ledger)
    paths = [path] if path.is_file() else []
    if path.parent.name == "work":
        paths += sorted(path.parent.parent.glob("attempts/*/usage.jsonl"))
    lines = [line for item in paths for line in item.read_text(encoding="utf-8").splitlines()]
    for line in lines:
        if not line.strip():
            continue
        event = json.loads(line)
        role = {"framework_usage": "framework_tokens", "solver_usage": "oracle_tokens"}.get(event.get("kind"))
        if role is None:
            continue
        value = (event.get("usage") or {}).get("total_tokens")
        if isinstance(value, (int, float)):
            totals[role] += value
        else:
            totals["records_without_usage"] += 1
    return totals


def _fit_payload(payload, limit):
    """Keep the feedback prompt inside the generator context: trim the oldest part of the
    champion's solver trace first (its tail holds the final actions and outcome)."""
    text = json.dumps(payload, ensure_ascii=False)
    feedback = payload.get("current_skill_feedback") or {}
    trace = str(feedback.get("solver_conversation", ""))
    if len(text) <= limit or not trace:
        return text, False
    keep = max(0, len(trace) - (len(text) - limit) - 80)  # 80: room for the truncation marker field
    payload = {**payload, "current_skill_feedback": {**feedback, "solver_conversation": trace[len(trace) - keep:],
                                                     "solver_conversation_truncated_chars": len(trace) - keep}}
    return json.dumps(payload, ensure_ascii=False), True


def run_direct_oracle(oracle, protocol, root, model, ledger, journal=None):
    """Direct Oracle ablation with the same oracle structure as SkillLift.

    SkillLift spends oracle rollouts only on the K candidates of each round (its inner
    edits are scored by the rubric, not the oracle). This arm removes the rubricator,
    verifier and rank alignment and, per round, generates the same K candidates from the
    current champion, one bounded edit each, and executes each candidate once. Selection,
    acceptance, infrastructure retries and stopping mirror the SkillLift coordinator; the
    rubric/verifier tie-break is replaced by the oracle's own criterion pass fraction.
    """
    from skilllift.coordinator import TrialSpec
    from skilllift.portfolio import SearchDirection, apply_portfolio_patch
    from skilllift_eval.runners.bounded_edits_skill_generator import BoundedEditsSkillGenerator
    from skilllift.errors import SkillLiftError
    config, rules = protocol["skilllift"], protocol["direct_oracle"]
    if rules["edits_per_candidate"] != 1 or rules["structure"] != "matched_to_skilllift":
        raise ValueError("The agreed Direct Oracle protocol uses one oracle-evaluated edit per candidate")
    rounds, width = int(config["max_rounds"]), int(config["max_candidates_per_round"])
    infra_limit = int(config.get("max_consecutive_infra_failures", 3))  # SkillLift CoordinatorConfig default
    excerpt = int(rules["patch_excerpt_chars"])
    spec = oracle.reward_spec()
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    llms = clients(model, ledger, ("skill_generator",), protocol=protocol, journal=journal)
    generator = BoundedEditsSkillGenerator(llms["skill_generator"],
                                           max_input_chars=config["max_context_chars"])
    task = oracle.public_task(oracle.family)
    counters = {"oracle_rollouts": 0, "generator_calls": 0}

    def evaluate(portfolio, label, phase, round_index=None):
        # Like the SkillLift coordinator: an infrastructure failure is retried on the same
        # candidate and never becomes a score; after the fuse trips the candidate is dropped.
        for attempt in range(infra_limit):
            counters["oracle_rollouts"] += 1
            result = oracle.evaluate(oracle.family, portfolio, TrialSpec(f"{label}-a{attempt}", phase, attempt, round_index=round_index, candidate_id=label))
            if result.is_valid:
                feedback = dict(oracle.last_feedback)
                if feedback.get("trace_missing"):
                    raise RuntimeError("Direct Oracle needs this trial's actual execution feedback; refusing a score-only substitute")
                feedback["evaluated_skill_hash"] = portfolio.tree_hash
                return spec.validate(result.reward), feedback
            append_event(ledger, {"kind": "oracle_infrastructure_failure", "candidate": label, "attempt": attempt,
                                  "error": str(result.infrastructure_error)[:500]})
        return None, None

    def summary(item):
        feedback = item["feedback"]
        return {"candidate": item["label"], "skill_hash": item["portfolio"].tree_hash, "reward": item["reward"],
                "criterion_pass_fraction": item["fraction"],
                "oracle_criterion_outcomes": feedback.get("oracle_criterion_outcomes", []),
                "solver_last_message": str(feedback.get("solver_last_message", ""))[-2000:],
                "patch_excerpt": item["patch"][:excerpt]}

    curve = []

    def record(round_index, accept_reason, stop_reason, extra=None):
        point = {"round_index": round_index, "champion_hash": champion.tree_hash, "champion_reward": champion_reward,
                 "champion_criterion_pass_fraction": champion_fraction, "accept_reason": accept_reason, "stop_reason": stop_reason,
                 "oracle_rollouts_cumulative": counters["oracle_rollouts"], "generator_calls_cumulative": counters["generator_calls"],
                 "oracle_replayed_cumulative": getattr(oracle, "replayed", 0),
                 **_ledger_tokens(ledger), **(extra or {})}
        curve.append(point)
        write_json(root / "curve.json", curve)
        if round_index >= 0:
            write_json(root / f"round-{round_index}.json", point)

    champion = oracle.seed_portfolio(oracle.family)
    champion_reward, champion_feedback = evaluate(champion, "direct-anchor", "anchor")
    if champion_reward is None:
        raise RuntimeError("Direct Oracle anchor did not yield a valid oracle result")
    champion_fraction = _criterion_pass_fraction(champion_feedback)
    if spec.is_terminal(champion_reward):
        record(-1, None, "anchor_terminal")
        return champion.root, {"stop_reason": "anchor_terminal", "rounds": 0, **counters}
    record(-1, None, None)
    previous_round, no_improvement, stop_reason, completed = [], 0, "round_budget_exhausted", 0
    for round_index in range(rounds):
        scopes = tuple(sorted(path.name for path in champion.root.iterdir() if path.is_dir()))
        proposed, invalid = [], 0
        # Phase 1: generate all K siblings from the same champion and the same evidence,
        # as SkillLift does; siblings see each other's patches only for diversity.
        for index in range(width):
            label = f"direct-r{round_index}-c{index}"
            payload = {"candidate_slot": f"{index + 1} of {width}", "current_skill_hash": champion.tree_hash,
                       "current_skill_reward": champion_reward, "current_skill_feedback": champion_feedback,
                       "previous_round_candidates": previous_round,
                       "already_proposed_this_round": [{"candidate": p["label"], "patch_excerpt": p["patch"][:excerpt]} for p in proposed]}
            text, trimmed = _fit_payload(payload, int(rules["max_feedback_chars"]))
            if trimmed:
                append_event(ledger, {"kind": "direct_oracle_feedback_trimmed", "candidate": label})
            direction = SearchDirection(label, "execution-feedback", DIRECT_ORACLE_INSTRUCTION + text,
                                        "correct execution", scopes, None, (task.evidence_ref,), label)
            try:
                counters["generator_calls"] += 1
                patch = generator.generate(task, direction, champion)
                candidate = apply_portfolio_patch(champion, patch, root / label, target_scope=scopes).portfolio
            except SkillLiftError as exc:
                invalid += 1
                append_event(ledger, {"kind": "invalid_candidate", "candidate": label, "error_type": type(exc).__name__})
                continue
            proposed.append({"label": label, "portfolio": candidate, "patch": patch})
        # Phase 2: one oracle execution per valid candidate (same count as SkillLift's round).
        evaluated = []
        for item in proposed:
            reward, feedback = evaluate(item["portfolio"], item["label"], "candidate", round_index)
            if reward is not None:
                evaluated.append({**item, "reward": reward, "feedback": feedback, "fraction": _criterion_pass_fraction(feedback)})
        completed = round_index + 1
        accept_reason = None
        if evaluated:
            winner = max(evaluated, key=lambda item: (item["reward"], -1 if item["fraction"] is None else item["fraction"]))
            if spec.improves(winner["reward"], champion_reward):
                accept_reason = "oracle_improved"
            elif (winner["reward"] == champion_reward and winner["fraction"] is not None and champion_fraction is not None
                  and winner["fraction"] > champion_fraction):
                accept_reason = "criterion_tiebreak"
            if accept_reason:
                champion, champion_reward = winner["portfolio"], winner["reward"]
                champion_fraction, champion_feedback = winner["fraction"], winner["feedback"]
        no_improvement = 0 if accept_reason else no_improvement + 1
        previous_round = [summary(item) for item in evaluated]
        stop = None
        if not evaluated:
            stop = "all_candidates_invalid"
        elif accept_reason and spec.is_terminal(champion_reward):
            stop = "terminal_candidate"
        elif no_improvement >= 2:
            stop = "two_no_improvement_rounds"
        elif round_index + 1 >= rounds:
            stop = "round_budget_exhausted"
        record(round_index, accept_reason, stop, {"candidates_generated": width, "candidates_invalid": invalid,
                                                  "candidates_evaluated": len(evaluated), "no_improvement_rounds": no_improvement})
        if stop:
            stop_reason = stop
            break
    check_training_failure(oracle, llms.values())
    return champion.root, {"stop_reason": stop_reason, "rounds": completed, "structure": "matched_to_skilllift", **counters}


def skillopt_config(protocol, root, seed_file, ep, run_index):
    cfg = dict(protocol["skillopt"])
    cfg.pop("revision", None)
    cfg.pop("selection_protocol", None)
    cfg.update(out_root=str(root), skill_init=str(seed_file), train_size=1, batch_size=1, accumulation=1,
               seed=run_index, minibatch_size=1, test_env_num=0, eval_test=False, use_gate=True,
               skill_update_mode="patch", lr_scheduler="cosine", model_backend="openai_compatible",
               optimizer_backend="openai_compatible", target_backend="openai_compatible",
               optimizer_model=ep["MODEL"], target_model=ep["MODEL"],
               optimizer_openai_compatible_base_url=ep["BASE_URL"], target_openai_compatible_base_url=ep["BASE_URL"],
               slow_update_gate_with_selection=True, use_slow_update=False, use_meta_skill=False)
    # Official trainer does not redact role-specific API-key fields. Never put
    # secrets in this dictionary: configure the backend in process memory instead.
    return cfg


def _harden_skillopt(protocol, ledger, journal=None):
    """Bounded retries and journal replay around the pinned backend's HTTP boundary."""
    from skillopt.model import openai_compatible_backend as backend
    from .resilience import policy_from, retry_call, request_key
    import inspect
    policy = policy_from(protocol)
    if getattr(backend._chat_messages_impl, "_revision_retry", False):
        return backend._revision_failure_state
    failure_state = {}
    original_client, original_impl = backend._get_client, backend._chat_messages_impl
    signature = inspect.signature(original_impl)

    def get_client(role):
        # Trainer may reset the lazy clients after configuration. Wrap each
        # returned client rather than only the initial cached instance.
        client = original_client(role).with_options(max_retries=0)
        def create(**kwargs):
            if failure_state.get("error"):
                raise failure_state["error"]
            key = request_key("skillopt_llm", role, kwargs)
            if journal is not None:
                hit, saved = journal.lookup(key)
                if hit:
                    from openai.types.chat import ChatCompletion
                    append_event(ledger, {"kind": "skillopt_replayed_from_journal", "role": role})
                    return ChatCompletion.model_validate({**saved["response"], "usage": None})
            try:
                response = retry_call(lambda: client.chat.completions.create(**kwargs), attempts=policy["api_max_attempts"],
                                      base=policy["api_base_seconds"], cap=policy["api_max_seconds"], describe=f"skillopt:{role}",
                                      log=lambda event: append_event(ledger, event))
            except Exception as exc:
                failure_state["error"] = exc
                if journal is not None:
                    journal.release(key)
                raise
            usage = getattr(response, "usage", None)
            append_event(ledger, {"kind": "skillopt_api_usage", "role": role, "usage": usage.model_dump() if usage else None})
            if journal is not None:
                journal.record(key, {"response": response.model_dump()})
            return response
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.arguments["retries"] = 1  # Official generic loop must not multiply HTTP retries.
        return original_impl(*bound.args, **bound.kwargs)

    wrapped._revision_retry = True
    backend._get_client, backend._chat_messages_impl = get_client, wrapped
    backend._revision_failure_state = failure_state
    return failure_state


def run_skillopt(oracle, protocol, root, model, ledger, run_index, journal=None):
    from .budget import OracleBudget, skillopt_budget
    from skillopt.engine.trainer import ReflACTTrainer
    from skillopt.envs.base import EnvAdapter
    from skillopt.model import configure_openai_compatible, get_token_summary, reset_token_tracker
    from skilllift.coordinator import TrialSpec
    from skilllift.portfolio import PortfolioRef
    seed_files = list(oracle.seed.glob("*/SKILL.md"))
    if len(seed_files) != 1:
        raise ValueError("SkillLearnBench SkillOpt adaptation requires the single frozen GPT-6-SOL skill per family")
    ep = endpoint(model)
    budget = skillopt_budget(protocol)
    execution_budget = OracleBudget(budget["maximum"])
    cfg = skillopt_config(protocol, Path(root) / "official", seed_files[0], ep, run_index)
    configure_openai_compatible(optimizer_base_url=ep["BASE_URL"], optimizer_api_key=ep["API_KEY"], optimizer_model=ep["MODEL"],
                               target_base_url=ep["BASE_URL"], target_api_key=ep["API_KEY"], target_model=ep["MODEL"])

    class Adapter(EnvAdapter):
        def __init__(self):
            super().__init__()
            self.analyst_workers, self.failure_only = cfg["analyst_workers"], False
            self.minibatch_size, self.edit_budget = cfg["minibatch_size"], cfg["edit_budget"]

        def build_train_env(self, batch_size, seed, **kwargs):
            return SimpleNamespace(count=batch_size, phase="train")

        def build_eval_env(self, env_num, split, seed, **kwargs):
            if split != "valid_seen":
                raise ValueError("SkillOpt may not access held-out instances")
            return SimpleNamespace(count=env_num, phase="selection_on_train")

        def get_task_types(self):
            return [oracle.family]

        def rollout(self, env_manager, skill_content, out_dir, **kwargs):
            rows = []
            for _ in range(env_manager.count):
                if failure_state.get("error"):
                    raise failure_state["error"]
                execution_budget.consume()
                name = f"execution-{execution_budget.used:05d}"  # stable prompt paths on journal replay
                folder = Path(out_dir) / "predictions" / name
                seed_root = folder / "skill"
                shutil.copytree(oracle.seed, seed_root)
                (seed_root / seed_files[0].relative_to(oracle.seed)).write_text(skill_content)
                portfolio = PortfolioRef.from_directory(task_id=oracle.family, root=seed_root, config_hash="skillopt-adapted")
                evaluation = oracle.evaluate(oracle.family, portfolio, TrialSpec(name, env_manager.phase, 0))
                if not evaluation.is_valid:
                    raise RuntimeError("SkillOpt oracle failed: " + str(evaluation.infrastructure_error))
                conversation = list(oracle.last_conversation)
                if not conversation:
                    raise RuntimeError("SkillOpt requires real solver trajectories for official reflection")
                write_json(folder / "conversation.json", conversation)
                rows.append({"id": name, "hard": evaluation.reward, "soft": evaluation.reward, "task_type": oracle.family,
                             "task_description": oracle.public_task(oracle.family).text,
                             "fail_reason": json.dumps(oracle.last_feedback, ensure_ascii=False), "n_turns": len(conversation)})
            return rows

    failure_state = _harden_skillopt(protocol, ledger, journal=journal)
    reset_token_tracker()
    try:
        ReflACTTrainer(cfg, Adapter()).train()
    finally:
        append_event(ledger, {"kind": "skillopt_framework_usage", "summary": get_token_summary(), "count_total_only": True})
    if failure_state.get("error"):
        raise failure_state["error"]
    check_training_failure(oracle)
    winner = Path(cfg["out_root"]) / "best_skill.md"
    if not winner.is_file():
        raise RuntimeError("Official SkillOpt did not produce best_skill.md")
    target = Path(root) / "winner"
    shutil.copytree(oracle.seed, target)
    (target / seed_files[0].relative_to(oracle.seed)).write_text(winner.read_text())
    return target, {"algorithm": "official SkillOpt", "selection": "fresh training-instance executions", "revision": protocol["skillopt"]["revision"],
                    "oracle_budget": budget, "logical_oracle_executions": execution_budget.used}
