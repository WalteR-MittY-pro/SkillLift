#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
import re as _re

ROOT = Path(__file__).resolve().parents[1]
AGENTCLAWBENCH = ROOT / "skilllift"
TAU2_SRC = ROOT / "tau2-bench" / "src"
TAU2_PYTHON = ROOT / "tau2-bench" / ".venv" / "bin" / "python"
TAU2_DOMAINS_ROOT = ROOT / "tau2-bench" / "data" / "tau2" / "domains"

for path in [str(ROOT), str(AGENTCLAWBENCH), str(TAU2_SRC)]:
    if path not in sys.path:
        sys.path.insert(0, path)

from skilllift.persistence import ExperimentStore, write_json
from skilllift.rubricator import generate_initial_receipt, revise_receipt
from skilllift.schemas import (
    SkillLiftConfig,
    EvidenceBudgetConfig,
    EvoSkill,
    Receipt,
    SkillKey,
    TaskSpec,
)
from skilllift.verifier import score_skills
from scripts.skilllift_tau2_real_llm_train_evolve_smoke import (
    D12RepairMetrics,
    _artifact_privacy_grep,
    _build_llm_clients,
    _call_fusion_llm,
    _initialize_markdown_skills,
    _resolve_model_ids,
    _summarize_tau2_llm_args,
    _tau2_llm_args,
    _update_markdown_skill_group,
)
from skilllift_eval.runners.skilllift_tau2 import (
    SkillLiftTau2FusionResult,
    SkillLiftTau2TrainConfig,
    _default_tau2_run_single_task,
    build_receipt_revision_input,
    evaluate_tau2_skill_batch,
    load_test_eval_clusters,
    load_train_clusters,
    run_heldout_test_eval,
    run_master_skill_fusion_smoke,
    write_train_cluster_artifact,
)
from skilllift_eval.skills.tau2_task_clustering import (
    DEFAULT_MAX_CLUSTER_SIZE,
    DEFAULT_SEED,
    SUPPORTED_DOMAINS,
    build_cluster_manifest,
    write_cluster_manifest,
)
from skilllift_eval.runners.skilllift_thresholds import sanitize_probability_fields


def main() -> int:
    args = parse_args()
    _maybe_reexec_tau2_venv()

    started = time.time()
    run_root = args.run_root.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    summary_path = run_root / "skilllift_tau2_domain_summary.json"
    summary: dict[str, Any] = {
        "domain": args.domain,
        "run_root": str(run_root),
        "status": "started",
    }
    try:
        result = run_domain(args, run_root)
        summary.update(result)
        summary["status"] = "completed"
        summary["elapsed_seconds"] = round(time.time() - started, 2)
        write_json(summary_path, summary)
        print(json.dumps({"status": "completed", "summary_path": str(summary_path)}, sort_keys=True))
        return 0
    except Exception as exc:
        summary.update(
            {
                "status": "failed",
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
                "elapsed_seconds": round(time.time() - started, 2),
            }
        )
        write_json(summary_path, summary)
        print(json.dumps({"status": "failed", "summary_path": str(summary_path), "error": str(exc)}, sort_keys=True))
        return 1


def parse_args() -> argparse.Namespace:
    defaults = SkillLiftConfig(skill_format="markdown_guide")
    parser = argparse.ArgumentParser(
        description="Run manifest-driven CoEvo x tau2 for one full domain."
    )
    parser.add_argument("--domain", required=True, choices=SUPPORTED_DOMAINS)
    parser.add_argument("--cluster-size", type=int, default=DEFAULT_MAX_CLUSTER_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--models-config", default=str(AGENTCLAWBENCH / "models_config.json"))
    parser.add_argument("--model", default="")
    parser.add_argument("--skill-generator-model", default="")
    parser.add_argument("--rubricator-model", default="")
    parser.add_argument("--verifier-model", default="")
    parser.add_argument("--fusion-model", default="")
    parser.add_argument("--tau2-model", default="")
    parser.add_argument("--max-steps", type=int, default=100)
    parser.add_argument("--skill-count", type=int, default=defaults.skill_count)
    parser.add_argument("--outer-rounds", type=int, default=defaults.outer_rounds)
    parser.add_argument("--oracle-threshold", type=float, default=defaults.oracle_threshold)
    parser.add_argument(
        "--rank-alignment-threshold",
        type=float,
        default=defaults.rank_alignment_threshold,
    )
    parser.add_argument("--domain-policy", default="")
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Disable final-artifact resume and rerun all stages.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Alias for disabling final-artifact resume for this run.",
    )
    parser.set_defaults(dry_run=False)
    return parser.parse_args()


def run_domain(args: argparse.Namespace, run_root: Path) -> dict[str, Any]:
    sanitize_probability_fields(
        args,
        run_root,
        {"oracle_threshold": 0.9, "rank_alignment_threshold": 0.9},
    )
    _ensure_tau2_importable()
    from tau2.run import get_tasks

    manifest_dir = run_root / "manifests"
    train_manifest_path = manifest_dir / "tau2_train_cluster_manifest.json"
    test_manifest_path = manifest_dir / "tau2_test_eval_manifest.json"
    train_manifest = build_cluster_manifest(
        TAU2_DOMAINS_ROOT,
        split="train",
        phase="train_evolve",
        domains=(args.domain,),
        max_cluster_size=args.cluster_size,
        seed=args.seed,
    )
    test_manifest = build_cluster_manifest(
        TAU2_DOMAINS_ROOT,
        split="test",
        phase="test_eval",
        domains=(args.domain,),
        max_cluster_size=args.cluster_size,
        seed=args.seed,
    )
    write_cluster_manifest(train_manifest, train_manifest_path)
    write_cluster_manifest(test_manifest, test_manifest_path)

    train_clusters = load_train_clusters(train_manifest_path)
    skip_train_clusters = {
        s.strip()
        for s in os.environ.get("SKILLLIFT_TAU2_SKIP_TRAIN_CLUSTERS", "").split(",")
        if s.strip()
    }
    if skip_train_clusters:
        train_clusters = [c for c in train_clusters if c.cluster_id not in skip_train_clusters]
    test_clusters = load_test_eval_clusters(test_manifest_path)
    train_task_ids = [task_id for cluster in train_clusters for task_id in cluster.task_ids]
    test_task_ids = [task_id for cluster in test_clusters for task_id in cluster.task_ids]
    overlap = sorted(set(train_task_ids) & set(test_task_ids))
    if overlap:
        raise ValueError(f"train/test task_id overlap is not allowed: {overlap[:5]}")

    model_ids = _resolve_model_ids(args)
    llm_clients = _build_llm_clients(args, model_ids)
    tau2_llm_args = _tau2_llm_args(args, model_ids["tau2"])
    domain_policy = args.domain_policy or f"Use only public {args.domain} policy and visible runtime state."

    base_config = SkillLiftTau2TrainConfig(
        run_root=run_root,
        manifest_path=train_manifest_path,
        model=model_ids["tau2"],
        oracle_threshold=args.oracle_threshold,
        seed=args.seed,
        max_steps=args.max_steps,
        domain_policy_by_domain={args.domain: domain_policy},
        tau2_llm_args=tau2_llm_args,
    )
    skilllift_defaults = SkillLiftConfig(skill_format="markdown_guide")
    d12_metrics = D12RepairMetrics()
    cluster_artifact_paths: list[Path] = []
    rank_alignment_by_cluster_round: list[dict[str, Any]] = []
    resume_enabled = _resume_enabled(args)
    skipped_train_clusters: list[str] = []

    for cluster in train_clusters:
        final_train_artifact = _final_train_artifact_path(
            run_root,
            cluster.domain,
            cluster.cluster_id,
            args.outer_rounds,
        )
        if resume_enabled and final_train_artifact.is_file():
            cluster_artifact_paths.append(final_train_artifact)
            rank_alignment_by_cluster_round.extend(_load_cluster_round_metrics(run_root, cluster.cluster_id))
            skipped_train_clusters.append(cluster.cluster_id)
            continue
        cluster_result = _run_train_cluster(
            args=args,
            cluster=cluster,
            tasks=get_tasks(cluster.domain, task_split_name="train", task_ids=cluster.task_ids),
            config=base_config,
            model_ids=model_ids,
            llm_clients=llm_clients,
            d12_metrics=d12_metrics,
            skilllift_defaults=skilllift_defaults,
            domain_policy=domain_policy,
        )
        cluster_artifact_paths.append(cluster_result["cluster_artifact_path"])
        rank_alignment_by_cluster_round.extend(cluster_result["round_metrics"])

    action_types = sorted({cluster.semantic_key for cluster in train_clusters if cluster.semantic_key})
    fusion_skipped = False
    fusion_result = _existing_fusion_result(run_root, args.domain) if resume_enabled else None
    if fusion_result is None:
        fusion_result = run_master_skill_fusion_smoke(
            base_config,
            cluster_artifact_paths=cluster_artifact_paths,
            domain=args.domain,
            action_types=action_types,
            fusion_model=model_ids["fusion"],
            fusion_llm=lambda prompt, model: _call_fusion_llm(llm_clients["fusion"], prompt, model),
            repair_metrics={
                "parse_error_count": d12_metrics.parse_error_count,
                "total_generation_attempts": d12_metrics.total_generation_attempts,
            },
        )
    else:
        fusion_skipped = True

    test_eval_result = _run_all_test_clusters(
        args=args,
        run_root=run_root,
        base_config=base_config,
        test_manifest=test_manifest,
        test_clusters=test_clusters,
        source_fusion_artifact_path=fusion_result.artifact_path,
        get_tasks=get_tasks,
        resume_enabled=resume_enabled,
    )
    privacy_status = _artifact_privacy_grep(run_root)

    return {
        "domain": args.domain,
        "cluster_size": args.cluster_size,
        "model_ids": model_ids,
        "tau2_llm_args_summary": _summarize_tau2_llm_args(tau2_llm_args),
        "skilllift_params": {
            "skill_count": args.skill_count,
            "outer_rounds": args.outer_rounds,
            "mode_a_iters": skilllift_defaults.mode_a_iters,
            "mode_b_iters": skilllift_defaults.mode_b_iters,
            "mode_a_min_score_threshold": skilllift_defaults.mode_a_min_score_threshold,
            "oracle_threshold": args.oracle_threshold,
            "rank_alignment_threshold": args.rank_alignment_threshold,
            "max_steps": args.max_steps,
        },
        "train_manifest_path": str(train_manifest_path.resolve()),
        "test_manifest_path": str(test_manifest_path.resolve()),
        "train_cluster_count": len(train_clusters),
        "test_cluster_count": len(test_clusters),
        "train_task_ids": train_task_ids,
        "test_task_ids": test_task_ids,
        "train_test_overlap": False,
        "d12_repair_metrics": d12_metrics.to_dict(),
        "rank_alignment_by_cluster_round": rank_alignment_by_cluster_round,
        "mode_a_outcome_summary": _aggregate_mode_a_outcomes(rank_alignment_by_cluster_round),
        "resume": {
            "enabled": resume_enabled,
            "skipped_train_clusters": skipped_train_clusters,
            "fusion_skipped": fusion_skipped,
            "skipped_test_clusters": test_eval_result["skipped_test_clusters"],
        },
        "fusion_artifact_path": str(fusion_result.artifact_path.resolve()),
        "master_skill_path": str(fusion_result.master_skill_path.resolve()),
        "best_skill_bundle_path": str(fusion_result.best_skill_bundle_path.resolve()),
        "test_eval_artifact_paths": test_eval_result["artifact_paths"],
        "final_heldout_test_score": test_eval_result["heldout_test_score"],
        "artifact_privacy_grep_status": privacy_status,
    }


def _mode_a_update_outcomes(skills: Any) -> dict[str, dict[str, Any]]:
    """每个候选 skill 的 Mode A 变异有效性标记,用于事后区分真变异与 inert 占位。

    时序:round_record 在 Mode A 更新前写出,因此读到的 metadata 反映的是
    "上一轮 Mode A 导致当前 skills 状态的 outcome"。首轮 seed 全为 active。
    """
    outcomes: dict[str, dict[str, Any]] = {}
    for key, skill in skills.items():
        if skill.metadata.get("mode_a_update_skipped"):
            outcome = "no_actionable_skipped"
        elif skill.metadata.get("mode_a_update_failed"):
            outcome = "mode_a_failed_preserved"
        else:
            outcome = "active"
        outcomes[key.token()] = {"outcome": outcome}
    return outcomes


def _reference_with_seed_skill(
    reference_material: str, skills: dict[SkillKey, EvoSkill]
) -> str:
    seed = skills.get(SkillKey(0, 1))
    if seed is None:
        return reference_material
    return reference_material + "\n\n# Seed Skill Evidence\n" + seed.files.get("SKILL.md", "")


def _aggregate_mode_a_outcomes(round_metrics_list: list[dict[str, Any]]) -> dict[str, Any]:
    """聚合所有 cluster/round 的 Mode A outcome,给出 inert_rate 供事后判断数据可信度。"""
    counts = {"active": 0, "no_actionable_skipped": 0, "mode_a_failed_preserved": 0}
    for rm in round_metrics_list:
        for outcome in rm.get("mode_a_update_outcomes", {}).values():
            o = outcome.get("outcome", "active")
            counts[o] = counts.get(o, 0) + 1
    total = sum(counts.values())
    inert = counts["no_actionable_skipped"] + counts["mode_a_failed_preserved"]
    return {
        "counts": counts,
        "inert_total": inert,
        "inert_rate": (inert / total) if total else 0.0,
    }

def _first_sentence(text: str) -> str:
    """取 purpose 的第一句, 保留句末标点。"""
    text = text.strip()
    if not text:
        return ""
    parts = _re.split(r"(?<=[.!?])\s+", text, maxsplit=1)
    return parts[0]

def _run_train_cluster(
    *,
    args: argparse.Namespace,
    cluster: Any,
    tasks: list[Any],
    config: SkillLiftTau2TrainConfig,
    model_ids: dict[str, str],
    llm_clients: dict[str, Any],
    d12_metrics: D12RepairMetrics,
    skilllift_defaults: SkillLiftConfig,
    domain_policy: str,
) -> dict[str, Any]:
    
    task_by_id = {t.id: t for t in tasks}
    overview_parts: list[str] = []
    for task_id in sorted(cluster.task_ids, key=lambda x: int(x) if x.isdigit() else 10**9):
        task = task_by_id.get(task_id)
        if task is None or not task.description or not task.description.purpose:
            continue
        overview_parts.append(f"[{task_id}] {_first_sentence(task.description.purpose)}")
    task_suite_overview = " ".join(overview_parts)

    task_spec = TaskSpec(
        task_name=f"tau2_{cluster.domain}_{cluster.cluster_id}",
        task_description=(
            f"CoEvo algorithm train_evolve tasks on {cluster.domain} related works with {len(cluster.task_ids)} tasks. "
            f"Task suite overview: {task_suite_overview}"
        ),
    )
    reference_material = "\n".join(
        [
            f"# Public tau2 {cluster.domain} Domain Policy",
            domain_policy,
            "",
        ]
    )
    store = ExperimentStore(config.run_root / "skilllift_state" / cluster.cluster_id)
    skilllift_config = SkillLiftConfig(
        exp_root=str(config.run_root / "skilllift_state"),
        exp_name=cluster.cluster_id,
        model=model_ids["tau2"],
        models_config=args.models_config,
        skill_count=args.skill_count,
        outer_rounds=args.outer_rounds,
        mode_a_iters=skilllift_defaults.mode_a_iters,
        mode_b_iters=skilllift_defaults.mode_b_iters,
        skill_format="markdown_guide",
        rank_alignment_threshold=args.rank_alignment_threshold,
        oracle_threshold=args.oracle_threshold,
        mode_a_min_score_threshold=skilllift_defaults.mode_a_min_score_threshold,
        use_llm=True,
        skill_generator_model=model_ids["skill_generator"],
        rubricator_model=model_ids["rubricator"],
        verifier_model=model_ids["verifier"],
        evidence_budget=EvidenceBudgetConfig(level="auto"),
    )
    store.save_config(skilllift_config)
    store.save_task_spec(task_spec)

    round_metrics: list[dict[str, Any]] = []
    final_artifact_path: Path | None = None

    resume_ckpt = (
        _load_cluster_checkpoint(config.run_root, cluster.cluster_id)
        if _resume_enabled(args) else None
    )
    if resume_ckpt is not None:
        skills = resume_ckpt["skills"]
        receipt = resume_ckpt["receipt"]
        round_metrics = resume_ckpt["round_metrics"]
        start_round = resume_ckpt["start_round"]
    else:
        skills = _initialize_markdown_skills(
            task_spec=task_spec,
            reference_material=reference_material,
            llm_client=llm_clients["skill_generator"],
            skill_count=args.skill_count,
            d12_metrics=d12_metrics,
            source_model=model_ids["skill_generator"],
        )
        for skill in skills.values():
            store.save_skill(skill, "initial")
        receipt = generate_initial_receipt(
            task_spec,
            _reference_with_seed_skill(reference_material, skills),
            llm_clients["rubricator"],
        )
        store.save_receipt(receipt, "receipt_initial")
        start_round = 0

    for round_index in range(start_round, args.outer_rounds):
        store.current_outer_round = round_index
        verifier_scores = score_skills(
            task_spec,
            skills,
            receipt,
            llm_clients["verifier"],
            mode="mode_b",
            skill_format="markdown_guide",
        )
        store.save_verifier_scores(verifier_scores, f"outer_{round_index:03d}_mode_b_verifier")
        batch_result = evaluate_tau2_skill_batch(
            tasks,
            skills,
            config,
            domain=cluster.domain,
            cluster_id=f"{cluster.cluster_id}_outer_{round_index:03d}",
            run_single_task=_default_tau2_run_single_task,
        )
        best_oracle_score = max(
            (score.oracle_score for score in batch_result.aggregate_oracle_scores.values()),
            default=0.0,
        )
        mode_b_oracle_threshold_met = best_oracle_score >= skilllift_config.oracle_threshold

        store.save_oracle_scores(
            batch_result.aggregate_oracle_scores,
            f"outer_{round_index:03d}_mode_b_tau2_batch",
        )
        revision_input = build_receipt_revision_input(
            task=task_spec,
            receipt=receipt,
            skills=skills,
            verifier_scores=verifier_scores,
            batch_result=batch_result,
        )
        round_record = {
            "cluster_id": cluster.cluster_id,
            "round_id": f"outer_{round_index:03d}",
            "verifier_rank": [key.token() for key in revision_input.verifier_rank],
            "aggregate_oracle_rank": [key.token() for key in revision_input.oracle_rank],
            "rank_alignment": revision_input.rank_alignment,
            "oracle_contrast": revision_input.oracle_contrast,
            "receipt_version_before": receipt.version,
            "aggregate_oracle_scores": {
                key.token(): score.to_dict()
                for key, score in revision_input.oracle_scores.items()
            },
            "per_task_rankings": [ranking.to_dict() for ranking in batch_result.per_task_rankings],
            "d12_repair_metrics": d12_metrics.to_dict(),
            "mode_a_update_outcomes": _mode_a_update_outcomes(skills),
            "best_oracle_score": best_oracle_score,
            "mode_b_oracle_threshold_met": mode_b_oracle_threshold_met,
        }
        round_metrics.append(round_record)
        write_json(
            config.run_root / "round_metrics" / cluster.cluster_id / f"outer_{round_index:03d}.json",
            round_record,
        )
        final_artifact_path = write_train_cluster_artifact(
            config=config,
            cluster_id=f"{cluster.cluster_id}_outer_{round_index:03d}",
            domain=cluster.domain,
            task_ids=cluster.task_ids,
            skills=skills,
            batch_result=batch_result,
            source_train_run_id=f"skilllift_tau2_domain:{cluster.cluster_id}",
        )
        if mode_b_oracle_threshold_met:
            break
        if (
            revision_input.rank_alignment is not None
            and revision_input.rank_alignment < args.rank_alignment_threshold
        ):
            receipt = revise_receipt(
                revision_input,
                llm_clients["rubricator"],
                store,
                skilllift_config.evidence_budget,
            )
            store.save_receipt(receipt, f"receipt_outer_{round_index:03d}_mode_b")
        store.save_receipt(receipt, f"receipt_outer_{round_index:03d}_mode_b_final")
        if round_index != args.outer_rounds - 1:
            mode_a_scores = score_skills(
                task_spec,
                skills,
                receipt,
                llm_clients["verifier"],
                mode="mode_a",
                skill_format="markdown_guide",
            )
            mode_a_verifier_threshold_met = bool(mode_a_scores) and min(
                score.normalized_score for score in mode_a_scores.values()
            ) >= skilllift_config.mode_a_min_score_threshold
            if mode_a_verifier_threshold_met:
                _write_cluster_checkpoint(
                    config.run_root, cluster.cluster_id, round_index, skills, receipt, round_metrics
                )
                continue
            skills = _update_markdown_skill_group(
                task_spec=task_spec,
                skills=skills,
                receipt=receipt,
                scores=mode_a_scores,
                llm_client=llm_clients["skill_generator"],
                d12_metrics=d12_metrics,
                source_model=model_ids["skill_generator"],
            )
            for skill in skills.values():
                store.save_skill(skill, f"outer_{round_index:03d}_mode_a_candidate")
        if round_index != args.outer_rounds - 1:
            _write_cluster_checkpoint(
                config.run_root, cluster.cluster_id, round_index, skills, receipt, round_metrics
            )

    if final_artifact_path is None:
        raise ValueError(f"cluster {cluster.cluster_id} produced no train artifact")
    _ckpt = _cluster_checkpoint_path(config.run_root, cluster.cluster_id)
    if _ckpt.is_file():
        _ckpt.unlink()
    return {"cluster_artifact_path": final_artifact_path, "round_metrics": round_metrics}


def _run_all_test_clusters(
    *,
    args: argparse.Namespace,
    run_root: Path,
    base_config: SkillLiftTau2TrainConfig,
    test_manifest: dict[str, Any],
    test_clusters: list[Any],
    source_fusion_artifact_path: Path,
    get_tasks: Any,
    resume_enabled: bool,
) -> dict[str, Any]:
    artifact_paths: list[str] = []
    all_scores: list[dict[str, Any]] = []
    per_cluster_scores: list[dict[str, Any]] = []
    skipped_test_clusters: list[str] = []
    cluster_manifest_dir = run_root / "manifests" / "test_eval_clusters"
    for cluster in test_clusters:
        final_test_artifact = _final_test_artifact_path(run_root, cluster.domain, cluster.cluster_id)
        if resume_enabled and final_test_artifact.is_file():
            artifact = _read_json(final_test_artifact)
            artifact_paths.append(str(final_test_artifact.resolve()))
            _accumulate_test_artifact(
                cluster=cluster,
                artifact=artifact,
                artifact_path=final_test_artifact,
                all_scores=all_scores,
                per_cluster_scores=per_cluster_scores,
            )
            skipped_test_clusters.append(cluster.cluster_id)
            continue
        single_manifest = {
            **test_manifest,
            "domains": {
                cluster.domain: [
                    {
                        "cluster_id": cluster.cluster_id,
                        "issue_type": cluster.issue_type,
                        "semantic_key": cluster.semantic_key,
                        "task_ids": list(cluster.task_ids),
                        "size": len(cluster.task_ids),
                    }
                ]
            },
        }
        single_manifest_path = cluster_manifest_dir / f"{cluster.cluster_id}.json"
        write_cluster_manifest(single_manifest, single_manifest_path)
        cluster_config = SkillLiftTau2TrainConfig(
            run_root=base_config.run_root,
            manifest_path=single_manifest_path,
            model=base_config.model,
            oracle_threshold=base_config.oracle_threshold,
            token_budget=base_config.token_budget,
            seed=base_config.seed,
            max_steps=base_config.max_steps,
            domain_policy_by_domain=base_config.domain_policy_by_domain,
            tau2_llm_args=base_config.tau2_llm_args,
        )
        result = run_heldout_test_eval(
            cluster_config,
            source_fusion_artifact_path=source_fusion_artifact_path,
            task_loader=lambda selected, _get_tasks=get_tasks: _get_tasks(
                selected.domain,
                task_split_name="test",
                task_ids=selected.task_ids,
            ),
            run_single_task=_default_tau2_run_single_task,
            no_skill_test_score=None,
            eval_id=f"skilllift-tau2-domain-{args.domain}",
            resume_enabled=resume_enabled,
        )
        artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
        artifact_paths.append(str(result.artifact_path.resolve()))
        _accumulate_test_artifact(
            cluster=cluster,
            artifact=artifact,
            artifact_path=result.artifact_path,
            all_scores=all_scores,
            per_cluster_scores=per_cluster_scores,
        )
    rewards = [float(score["reward"]) for score in all_scores]
    heldout_test_score = sum(rewards) / len(rewards) if rewards else 0.0
    aggregate_path = run_root / "test_eval" / args.domain / "domain_heldout_test_score.json"
    write_json(
        aggregate_path,
        {
            "tau2_split": "test",
            "domain": args.domain,
            "cluster_count": len(test_clusters),
            "task_count": len(all_scores),
            "per_cluster_scores": per_cluster_scores,
            "per_task_scores": all_scores,
            "heldout_test_score": heldout_test_score,
            "skilllift_loaded_skill_test_score": heldout_test_score,
            "cluster_artifact_paths": artifact_paths,
            "test_artifact_paths": artifact_paths,
            "source_fusion_artifact_path": str(source_fusion_artifact_path.resolve()),
        },
    )
    artifact_paths.append(str(aggregate_path.resolve()))
    return {
        "artifact_paths": artifact_paths,
        "heldout_test_score": heldout_test_score,
        "skipped_test_clusters": skipped_test_clusters,
    }


def _resume_enabled(args: argparse.Namespace) -> bool:
    return not (args.no_resume or args.force)


def _final_train_artifact_path(
    run_root: Path,
    domain: str,
    cluster_id: str,
    outer_rounds: int,
) -> Path:
    final_round = max(outer_rounds - 1, 0)
    return (
        run_root
        / "train_evolve"
        / domain
        / f"{cluster_id}_outer_{final_round:03d}"
        / "cluster_train_artifact.json"
    )


def _existing_fusion_result(run_root: Path, domain: str) -> SkillLiftTau2FusionResult | None:
    artifact_dir = run_root / "master_skill_fusion" / domain
    artifact_path = artifact_dir / "fusion_artifact.json"
    master_skill_path = artifact_dir / "MASTER_SKILL.md"
    best_skill_bundle_path = artifact_dir / "best_skill_bundle.json"
    if all(path.is_file() for path in [artifact_path, master_skill_path, best_skill_bundle_path]):
        return SkillLiftTau2FusionResult(
            artifact_path=artifact_path,
            master_skill_path=master_skill_path,
            best_skill_bundle_path=best_skill_bundle_path,
        )
    return None


def _final_test_artifact_path(run_root: Path, domain: str, cluster_id: str) -> Path:
    return run_root / "test_eval" / domain / cluster_id / "heldout_test_score.json"


def _load_cluster_round_metrics(run_root: Path, cluster_id: str) -> list[dict[str, Any]]:
    metrics_dir = run_root / "round_metrics" / cluster_id
    if not metrics_dir.is_dir():
        return []
    return [_read_json(path) for path in sorted(metrics_dir.glob("outer_*.json"))]


def _cluster_checkpoint_path(run_root: Path, cluster_id: str) -> Path:
    return run_root / "skilllift_state" / cluster_id / "round_checkpoint.json"


def _load_cluster_checkpoint(run_root: Path, cluster_id: str) -> dict[str, Any] | None:
    """读取 round 级 checkpoint;返回下一轮起点所需的 skills/receipt/round_metrics。

    与 round_metrics/outer_*.json 的区别:本文件是跨轮可变状态的权威快照,
    在每轮 mode_a 更新完成后才写入,故恢复出的 skills 即下一轮输入。
    """
    path = _cluster_checkpoint_path(run_root, cluster_id)
    if not path.is_file():
        return None
    data = _read_json(path)
    skills = {
        SkillKey.from_token(token): EvoSkill.from_dict(skill)
        for token, skill in data["skills"].items()
    }
    return {
        "start_round": int(data["completed_round_index"]) + 1,
        "skills": skills,
        "receipt": Receipt.from_dict(data["receipt"]),
        "round_metrics": data.get("round_metrics", []),
    }


def _write_cluster_checkpoint(
    run_root: Path,
    cluster_id: str,
    completed_round_index: int,
    skills: dict[SkillKey, EvoSkill],
    receipt: Receipt,
    round_metrics: list[dict[str, Any]],
) -> None:
    """落盘 round 级 checkpoint,供崩溃后从下一轮恢复。"""
    write_json(
        _cluster_checkpoint_path(run_root, cluster_id),
        {
            "cluster_id": cluster_id,
            "completed_round_index": completed_round_index,
            "skills": skills,
            "receipt": receipt,
            "round_metrics": round_metrics,
        },
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _accumulate_test_artifact(
    *,
    cluster: Any,
    artifact: dict[str, Any],
    artifact_path: Path,
    all_scores: list[dict[str, Any]],
    per_cluster_scores: list[dict[str, Any]],
) -> None:
    scores = artifact.get("per_task_scores") or []
    all_scores.extend(scores)
    per_cluster_scores.append(
        {
            "cluster_id": cluster.cluster_id,
            "task_count": len(scores),
            "heldout_test_score": artifact.get("heldout_test_score"),
            "artifact_path": str(artifact_path.resolve()),
        }
    )


def _ensure_tau2_importable() -> None:
    if str(TAU2_SRC) not in sys.path:
        sys.path.insert(0, str(TAU2_SRC))


def _maybe_reexec_tau2_venv() -> None:
    if os.environ.get("SKILLLIFT_TAU2_DOMAIN_REEXEC") == "1":
        return
    if not TAU2_PYTHON.exists():
        return
    if Path(sys.executable).resolve() == TAU2_PYTHON.resolve():
        return
    env = dict(os.environ)
    pythonpath = os.pathsep.join(
        [
            str(ROOT),
            str(AGENTCLAWBENCH),
            str(TAU2_SRC),
            env.get("PYTHONPATH", ""),
        ]
    ).rstrip(os.pathsep)
    env["PYTHONPATH"] = pythonpath
    env["SKILLLIFT_TAU2_DOMAIN_REEXEC"] = "1"
    os.execvpe(str(TAU2_PYTHON), [str(TAU2_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]], env)


if __name__ == "__main__":
    raise SystemExit(main())
