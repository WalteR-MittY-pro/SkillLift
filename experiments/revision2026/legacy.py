"""Legacy evidence recovery and explicit fresh-cohort rebuilding.

Historical means are never combined with variance from new executions.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from .common import ROOT, append_event, endpoint, read_json, tree_hash, write_json

HISTORICAL_MODELS = {"skillsbench": ["gpt_skillsbench", "glm", "deepseek_pro"],
                     "wildclawbench": ["gpt_wildclaw", "glm", "deepseek_pro"]}
TABLE_METHODS = ["no_skill", "human_written", "llm_written", "skillopt", "coevoskills", "skilllift"]


def audit(archive, output):
    subprocess.run([sys.executable, "-m", "experiments.revision2026.legacy_audit", "--archive", str(Path(archive).resolve()), "--output", str(Path(output).resolve())], cwd=ROOT, check=True)


def recovery_plan(archive, output):
    from skilllift.portfolio import portfolio_tree_hash
    archive, output = Path(archive).resolve(), Path(output).resolve()
    audit(archive, output)
    model_map = {"gpt": "gpt_skillsbench", "glm": "glm", "deepseek": "deepseek_pro"}
    expected_models = {"gpt": "vllm/gpt-5.4-mini", "glm": "vllm/glm-5.1", "deepseek": "vllm/deepseek-v4-pro"}
    observed_models = {row["group"]: {m.lower() for m in row["actual_models_in_final_raw_results"]}
                       for row in read_json(output / "groups.json")}
    assets, missing = [], []
    for group, model in model_map.items():
        for manifest in sorted((archive / "runs/skillsbench" / group).glob("*/run_manifest.json")):
            domain = manifest.parent
            summary = read_json(domain / "summary.json") if (domain / "summary.json").exists() else {}
            rows = {row["task_id"]: row for row in summary.get("tasks", [])}
            for task in read_json(manifest)["task_ids"]:
                source = domain / "tasks" / task / "final_portfolio"
                row = rows.get(task, {})
                reason = None
                if observed_models.get(group) != {expected_models[group]}:
                    reason = "historical_model_identity_unproven"
                elif not source.is_dir():
                    reason = "missing_frozen_portfolio"
                elif not row.get("winner_hash"):
                    reason = "missing_historical_winner_hash"
                else:
                    try:
                        if portfolio_tree_hash(source) != row["winner_hash"]:
                            reason = "portfolio_does_not_match_historical_winner_hash"
                    except (OSError, ValueError) as exc:
                        reason = type(exc).__name__
                item = {"benchmark": "skillsbench", "model": model, "method": "skilllift", "task_id": task,
                        "family": task, "evolution_run": 0, "source_dir": str(source), "historical_winner_hash": row.get("winner_hash")}
                if reason:
                    missing.append({**item, "reason": reason})
                else:
                    assets.append({**item, "content_hash": tree_hash(source)})
    plan = {"schema_version": 1, "archive": str(archive), "assets": assets, "unresolved": missing,
            "scope": "New independent reevaluation of verifiable frozen skills. One archived skill per task does not prove three complete evolution runs.",
            "other_table2_cells": "Other baseline assets are not inferred from directory names. Add verified assets to this manifest or use table2-rebuild for a separately labelled fresh 36-cell experiment."}
    write_json(output / "repair_plan.json", plan)
    return plan


def repair_jobs(plan_path, root, protocol, config_hash):
    from .runner import cell_path, freeze_cell
    plan = read_json(plan_path)
    jobs = []
    for asset in plan["assets"]:
        source = Path(asset["source_dir"])
        if tree_hash(source) != asset["content_hash"]:
            raise ValueError("Archive asset changed after planning: " + str(source))
        common = dict(benchmark=asset["benchmark"], model=asset["model"], method=asset["method"], family=asset["family"],
                      run=asset["evolution_run"], root=str(Path(root).resolve()), protocol=protocol, config_hash=config_hash)
        cell = cell_path(root, common["benchmark"], common["model"], common["method"], common["run"], common["family"])
        if not (cell / "frozen.json").exists():
            cell.mkdir(parents=True, exist_ok=True)
            freeze_cell(source, cell, common, {"restored_from": str(source), "historical_hash_verified": True,
                                               "historical_independent_evolution_count_proven": False})
        for repeat in range(protocol["ablation"]["final_repeats"]):
            jobs.append({**common, "kind": "evaluate", "phase": "independent_final", "task_id": asset["task_id"], "repeat": repeat})
    # Missing assets stay in the expected denominator. They are blocked execution
    # cells, not exclusions that can turn a selected subset into a full-bench CI.
    for asset in plan["unresolved"]:
        common = dict(benchmark=asset["benchmark"], model=asset["model"], method=asset["method"], family=asset["family"],
                      run=asset["evolution_run"], root=str(Path(root).resolve()), protocol=protocol, config_hash=config_hash)
        for repeat in range(protocol["ablation"]["final_repeats"]):
            jobs.append({**common, "kind": "evaluate", "phase": "independent_final", "task_id": asset["task_id"],
                         "repeat": repeat, "blocked_reason": asset["reason"]})
    write_json(Path(root) / "repair_unresolved.json", plan["unresolved"])
    return jobs


def rebuild_jobs(root, protocol, config_hash):
    from .oracles import native_task_ids
    jobs = []
    for benchmark, models in HISTORICAL_MODELS.items():
        for task in native_task_ids(benchmark):
            jobs.append(dict(kind="write_seed", benchmark=benchmark, family=task, model=models[0], method="llm_written",
                             root=str(Path(root).resolve()), protocol=protocol, config_hash=config_hash, run=0))
            for model in models:
                for method in TABLE_METHODS:
                    for run in range(protocol["evolution_runs"]):
                        common = dict(benchmark=benchmark, family=task, model=model, method=method, run=run,
                                      root=str(Path(root).resolve()), protocol=protocol, config_hash=config_hash, fresh_table2=True)
                        jobs.append({**common, "kind": "train"})
                        for repeat in range(protocol["ablation"]["final_repeats"]):
                            jobs.append({**common, "kind": "evaluate", "phase": "independent_final", "task_id": task, "repeat": repeat})
    return jobs


def write_seed(spec):
    from .oracles import native_adapter
    from skilllift.llm_client import LLMClient
    from skilllift.portfolio import PortfolioRef
    root = Path(spec["root"]) / "shared_llm_seeds" / spec["benchmark"] / spec["family"]
    marker = root / "generated.json"
    if marker.exists():
        if read_json(marker)["skill_hash"] != tree_hash(root / "skills"):
            raise ValueError("Shared LLM-written seed changed")
        return
    adapter = native_adapter(spec["benchmark"], spec["family"], spec["model"], root / "native", spec["protocol"])
    ep = endpoint("writer")
    if ep["MODEL"].lower() != "gpt-6-sol":
        raise ValueError("The agreed common LLM-written generator is GPT-6-SOL; configure WRITER_MODEL accordingly")
    client = LLMClient(ep["BASE_URL"], ep["API_KEY"], ep["MODEL"], role="llm_written",
                       usage_callback=lambda e: append_event(root / "usage.jsonl", {"kind": "framework_usage", **e}))
    prompt = "Write one reusable SKILL.md, not the task answer. Use only this public task description. Return raw Markdown with YAML name: shared-task-guidance and a useful description. Give reusable tools, workflows and checks; parameterize input-specific values. No evaluation feedback is available.\n" + adapter.public_task(spec["family"]).text
    text = client.call_text("You author reusable agent skills from public training descriptions.", prompt, temperature=0)
    target = root / "skills/shared-task-guidance/SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    PortfolioRef.from_directory(task_id=spec["family"], root=root / "skills", config_hash=spec["config_hash"], generated_skill_names=["shared-task-guidance"])
    write_json(marker, {"model": ep["MODEL"], "skill_hash": tree_hash(root / "skills"), "config_hash": spec["config_hash"],
                       "scope": "Fresh Table2 rebuilding only; the SkillLearnBench GPT-6-SOL assets are already frozen and are not regenerated."})


def run_coevo(oracle, protocol, root, model, ledger, journal=None):
    """Reuse the repository's existing native CoEvoSkills implementation."""
    from .sources import source_path
    from .oracles import native_adapter
    from .optimization import clients
    from coevoskills.skill_generator import LLMSkillGenerator
    from coevoskills.surrogate_verifier import LLMSurrogateVerifier
    ep = endpoint(model)
    llms = clients(model, ledger)
    generator = LLMSkillGenerator(llms["skill_generator"], max_context_chars=200000)
    verifier = LLMSurrogateVerifier(llms["verifier"])
    params = {"evaluation_repeats": 1, "token_budget": 0,
              "surrogate_docker_image": os.getenv("SKILLLIFTSKILLS_SURROGATE_DOCKER_IMAGE") or os.getenv("DOCKER_IMAGE") or "wildclawbench-ubuntu:v1.3"}
    if oracle.benchmark == "skillsbench":
        from skilllift_eval.runners.coevoskills_skillsbench import CoEvoSkillsSkillsBenchRunner
        from skilllift_eval.runners.skillsbench_evolution_adapter import SkillsBenchEvolutionSettings, SkillsBenchEvolutionAdapter
        settings = SkillsBenchEvolutionSettings(project_root=ROOT, run_root=Path(root) / "coevo", tasks_root=source_path("skillsbench") / "tasks",
            split_path=Path(root) / "unused-split.json", python_executable=Path(sys.executable), model="vllm/" + ep["MODEL"], sandbox="docker",
            base_url=ep["BASE_URL"], api_key=ep["API_KEY"], patch_source=ROOT / "scripts/oh_skill_patch.py", agent="openhands-sse",
            framework_model=ep["MODEL"], use_stream=True, algorithm_params=params,
            prebuilt_image_template="prewarm/{task_id}:prewarm")
        CoEvoSkillsSkillsBenchRunner(settings, generator=generator, verifier=verifier).run_task(oracle.family)
        experiment = settings.run_root / "tasks" / oracle.family / "coevoskills_experiment"
        best = read_json(experiment / "checkpoint.json")["result"]["best_skill"]
        wrapper = SkillsBenchEvolutionAdapter(native_adapter("skillsbench", oracle.family, model, Path(root) / "coevo-materialize", protocol))
        portfolio = wrapper._portfolio(oracle.family, "coevoskills-generated", best["files"])
        return portfolio.root, {"algorithm": "repository CoEvoSkills", "initialization": "native baseline initialization; not inferred to match historical publication", "internal_evaluation_used_for_reporting": False}
    from dataclasses import asdict
    from skilllift_eval.runners.coevoskills_wildclaw import CoEvoSkillsWildClawRunSettings, CoEvoSkillsWildClawRunner
    native = oracle.native
    values = asdict(native.settings_factory(Path(root) / "coevo"))
    fields = CoEvoSkillsWildClawRunSettings.__dataclass_fields__
    values = {k: v for k, v in values.items() if k in fields}
    values.update(model_endpoint=native.settings_factory(Path(root) / "coevo").model_endpoint, project_root=ROOT,
                  skilllift_root=ROOT / "skilllift", algorithm_params=params)
    settings = CoEvoSkillsWildClawRunSettings(**values)
    CoEvoSkillsWildClawRunner(generator=generator, verifier=verifier).run(settings)
    best = read_json(settings.run_root / "coevoskills_experiment/checkpoint.json")["result"]["best_skill"]
    destination = Path(root) / "winner" / "coevoskills-generated"
    destination.mkdir(parents=True)
    for name, content in best["files"].items():
        target = destination / name
        if not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError("Unsafe generated skill path")
        target.parent.mkdir(parents=True, exist_ok=True)
        if name == "SKILL.md":
            import yaml
            if content.startswith("---\n") and "\n---\n" in content[4:]:
                head, body = content[4:].split("\n---\n", 1)
                metadata = yaml.safe_load(head) or {}
            else:
                metadata, body = {}, content
            metadata["name"] = "coevoskills-generated"
            metadata.setdefault("description", "CoEvoSkills task-solving guidance")
            content = "---\n" + yaml.safe_dump(metadata, sort_keys=False) + "---\n" + body
        target.write_text(content)
    return destination.parent, {"algorithm": "repository CoEvoSkills", "internal_evaluation_used_for_reporting": False}
