from __future__ import annotations

import json
from pathlib import Path
import shutil
import uuid
from types import SimpleNamespace

from .common import append_event, endpoint, write_json


def clients(model, ledger, roles=("rubricator", "skill_generator", "verifier")):
    from skilllift.llm_client import LLMClient
    ep = endpoint(model)
    return {role: LLMClient(ep["BASE_URL"], ep["API_KEY"], ep["MODEL"], role=role, use_stream=True,
                           usage_callback=lambda event: append_event(ledger, {"kind": "framework_usage", **event}))
            for role in roles}


def run_skilllift(oracle, protocol, root, model, ledger):
    from skilllift import SkillLiftPortfolioCoordinator
    from skilllift.coordinator import CoordinatorConfig
    from skilllift.portfolio import PortfolioStore, LLMRubricatorPlanner
    from skilllift_eval.runners.bounded_edits_skill_generator import BoundedEditsSkillGenerator
    llms = clients(model, ledger)
    config = protocol["skilllift"]
    result = SkillLiftPortfolioCoordinator(
        adapter=oracle, planner=LLMRubricatorPlanner(llms["rubricator"], max_input_chars=config["max_context_chars"]),
        generator=BoundedEditsSkillGenerator(llms["skill_generator"], max_input_chars=config["max_context_chars"]),
        verifier_client=llms["verifier"], store=PortfolioStore(Path(root) / "coordinator"), config=CoordinatorConfig(**config),
    ).evolve(oracle.family)
    if result.stop_reason == "anchor_indeterminate":
        raise RuntimeError("SkillLift anchor did not yield a valid oracle result")
    # Never report result.final_score: legacy internal final uses training instances
    # and a maximum. The outer driver evaluates every frozen winner independently.
    return result.winner_portfolio.root, {"stop_reason": result.stop_reason, "rounds": result.rounds_completed,
                                         "internal_final_is_training": True}


def run_direct_oracle(oracle, protocol, root, model, ledger):
    from skilllift.coordinator import TrialSpec
    from skilllift.portfolio import SearchDirection, apply_portfolio_patch
    from skilllift_eval.runners.bounded_edits_skill_generator import BoundedEditsSkillGenerator
    from skilllift.errors import SkillLiftError
    config = protocol["skilllift"]
    generator = BoundedEditsSkillGenerator(clients(model, ledger, ("skill_generator",))["skill_generator"], max_input_chars=config["max_context_chars"])
    best = oracle.seed_portfolio(oracle.family)
    anchor = oracle.evaluate(oracle.family, best, TrialSpec("direct-anchor", "anchor", 0))
    if not anchor.is_valid:
        raise RuntimeError("Direct Oracle anchor failed: " + str(anchor.infrastructure_error))
    score, no_improvement = anchor.reward, 0
    feedback_by_hash = {best.tree_hash: dict(oracle.last_feedback)}
    if score >= 1:
        return best.root, {"stop_reason": "anchor_terminal", "rounds": 0}
    for round_id in range(config["max_rounds"]):
        champion, champion_score = best, score
        round_feedback = feedback_by_hash[best.tree_hash]
        for candidate in range(config["max_candidates_per_round"]):
            branch, branch_score, feedback = champion, champion_score, round_feedback
            for edit in range(1 + config["mode_a_iters"]):
                if feedback.get("trace_missing"):
                    raise RuntimeError("Direct Oracle needs this trial's actual execution feedback; refusing a score-only substitute")
                label = f"direct-r{round_id}-c{candidate}-e{edit}"
                scopes = tuple(sorted(path.name for path in branch.root.iterdir() if path.is_dir()))
                direction = SearchDirection(label, "execution-feedback", "Improve this reusable skill using actual execution feedback. The last attempted candidate can differ from the current best skill; respect their hashes. Do not invent a rubric.\n" + json.dumps({"current_skill_hash": branch.tree_hash, "last_attempt_feedback": feedback}, ensure_ascii=False),
                                            "correct execution", scopes, None, (oracle.public_task(oracle.family).evidence_ref,), label)
                try:
                    patch = generator.generate(oracle.public_task(oracle.family), direction, branch)
                    proposed = apply_portfolio_patch(branch, patch, Path(root) / label, target_scope=scopes).portfolio
                except SkillLiftError as exc:
                    append_event(ledger, {"kind": "invalid_candidate", "candidate": label, "error_type": type(exc).__name__})
                    break
                result = oracle.evaluate(oracle.family, proposed, TrialSpec(label, "candidate", 0, round_index=round_id, candidate_id=label))
                if not result.is_valid:
                    raise RuntimeError("Direct Oracle execution failed: " + str(result.infrastructure_error))
                feedback = dict(oracle.last_feedback)
                feedback["evaluated_skill_hash"] = proposed.tree_hash
                feedback_by_hash[proposed.tree_hash] = feedback
                if result.reward > branch_score:
                    branch, branch_score = proposed, result.reward
                if branch_score >= 1:
                    break
            if branch_score > score:
                best, score = branch, branch_score
        no_improvement = no_improvement + 1 if score <= champion_score else 0
        write_json(Path(root) / f"round-{round_id}.json", {"reward": score, "winner": best.tree_hash, "no_improvement": no_improvement})
        if score >= 1 or no_improvement >= 2:
            return best.root, {"stop_reason": "terminal_candidate" if score >= 1 else "two_no_improvement_rounds", "rounds": round_id + 1}
    return best.root, {"stop_reason": "round_budget_exhausted", "rounds": config["max_rounds"]}


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
               slow_update_gate_with_selection=True)
    # Official trainer does not redact role-specific API-key fields. Never put
    # secrets in this dictionary: configure the backend in process memory instead.
    return cfg


def run_skillopt(oracle, protocol, root, model, ledger, run_index):
    from skillopt.engine.trainer import ReflACTTrainer
    from skillopt.envs.base import EnvAdapter
    from skillopt.model import configure_openai_compatible, get_token_summary, reset_token_tracker
    from skilllift.coordinator import TrialSpec
    from skilllift.portfolio import PortfolioRef
    seed_files = list(oracle.seed.glob("*/SKILL.md"))
    if len(seed_files) != 1:
        raise ValueError("SkillLearnBench SkillOpt adaptation requires the single frozen GPT-6-SOL skill per family")
    ep = endpoint(model)
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
                name = "execution-" + uuid.uuid4().hex
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

    reset_token_tracker()
    try:
        ReflACTTrainer(cfg, Adapter()).train()
    finally:
        append_event(ledger, {"kind": "skillopt_framework_usage", "summary": get_token_summary(), "count_total_only": True})
    winner = Path(cfg["out_root"]) / "best_skill.md"
    if not winner.is_file():
        raise RuntimeError("Official SkillOpt did not produce best_skill.md")
    target = Path(root) / "winner"
    shutil.copytree(oracle.seed, target)
    (target / seed_files[0].relative_to(oracle.seed)).write_text(winner.read_text())
    return target, {"algorithm": "official SkillOpt", "selection": "fresh training-instance executions", "revision": protocol["skillopt"]["revision"]}
