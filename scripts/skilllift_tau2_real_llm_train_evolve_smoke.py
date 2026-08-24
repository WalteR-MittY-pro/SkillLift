#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AGENTCLAWBENCH = ROOT / "skilllift"
TAU2_SRC = ROOT / "tau2-bench" / "src"
TAU2_PYTHON = ROOT / "tau2-bench" / ".venv" / "bin" / "python"
for path in [str(ROOT), str(AGENTCLAWBENCH), str(TAU2_SRC)]:
    if path not in sys.path:
        sys.path.insert(0, path)

from skilllift.errors import LLMOutputError, SkillPackageError
from skilllift.llm_client import LLMClient, create_llm_client, resolve_llm_config
from skilllift.persistence import ExperimentStore, write_json
from skilllift.baselines.prompts import (
    build_sg_repair_prompt,
    build_sg_seed_prompt,
    build_sg_update_prompt,
    build_sg_variant_prompt,
)
from skilllift.rubricator import generate_initial_receipt, revise_receipt
from skilllift.schemas import (
    SkillLiftConfig,
    EvidenceBudgetConfig,
    EvoSkill,
    Receipt,
    SkillKey,
    TaskSpec,
    VerifierScore,
)
from skilllift.baselines.sg import (
    _actionable_rubrics,
    _content_block_token,
    _fallback_package,
    _normalize_skill_files,
    _validate_mode_a_update_payload,
    parse_markdown_guide_output,
)
from skilllift.baselines.skill_package import GeneratedSkillPackage
from skilllift.verifier import fallback_score_skill, score_skills
from skilllift_eval.runners.skilllift_tau2 import (
    SkillLiftTau2TrainConfig,
    _default_tau2_run_single_task,
    build_receipt_revision_input,
    evaluate_tau2_skill_batch,
    load_train_clusters,
    run_heldout_test_eval,
    run_master_skill_fusion_smoke,
    write_train_cluster_artifact,
)
from skilllift_eval.runners.skilllift_thresholds import sanitize_probability_fields


FORBIDDEN_ARTIFACT_PATTERNS = [
    "evaluation_criteria",
    "gold_action",
    "target_db",
    "messages",
    "transcript",
]


@dataclass
class D12RepairMetrics:
    parse_error_count: int = 0
    total_generation_attempts: int = 0
    repaired_success_count: int = 0
    failed_after_repair_count: int = 0
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        rate = (
            self.parse_error_count / self.total_generation_attempts
            if self.total_generation_attempts
            else 0.0
        )
        return {
            "parse_error_count": self.parse_error_count,
            "total_generation_attempts": self.total_generation_attempts,
            "repair_trigger_rate": rate,
            "repaired_success_count": self.repaired_success_count,
            "failed_after_repair_count": self.failed_after_repair_count,
            "attempts": self.attempts,
        }


class DryRunLLMClient:
    def __init__(self, model: str) -> None:
        self.model = model
        self.display_name = f"dry-run/{model}"
        self.base_url = "dry-run://local"
        self.provider = "dry-run"
        self.request_count = 0
        self.total_tokens = 0

    def call_text(self, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        del system_prompt, temperature
        self.request_count += 1
        block = _extract_prompt_content_block(user_prompt)
        if "Invalid Markdown Guide Package" in user_prompt:
            return _dry_markdown_package(block, "mode_a_update", mode="patch")
        if "[Variant Slot]" in user_prompt:
            return _dry_markdown_package(block, "variant", marker="variant")
        if "[Patch Output Contract]" in user_prompt:
            return _dry_markdown_package(block, "mode_a_update", mode="patch", marker="updated")
        return _dry_markdown_package(block, "seed", marker="seed")

    def call_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> dict[str, Any]:
        del system_prompt, temperature
        self.request_count += 1
        if "[Required JSON Schema]" in user_prompt and "criterion_hits" in user_prompt:
            rubrics = _extract_current_receipt_rubrics(user_prompt)
            return {
                "criterion_hits": {
                    rubric_id: points > 0 for rubric_id, points in rubrics.items()
                },
                "rationale": "dry-run verifier accepts visible markdown workflow",
            }
        if "[Revision Input]" in user_prompt:
            current = _extract_json_after_marker(user_prompt, "[Revision Input]")
            receipt = (current or {}).get("current_receipt") or {}
            receipt["version"] = int(receipt.get("version", 0)) + 1
            receipt["removed_rubrics"] = []
            receipt["metadata"] = {**(receipt.get("metadata") or {}), "dry_run_revised": True}
            return receipt
        return {
            "version": 1,
            "rubrics": [
                {
                    "rubric_id": "r1",
                    "category": "Workflow",
                    "criterion": "Skill tells the agent to verify visible customer state before action",
                    "points": 2,
                },
                {
                    "rubric_id": "r2",
                    "category": "Workflow",
                    "criterion": "Skill tells the agent to check results after action",
                    "points": 2,
                },
                {
                    "rubric_id": "r3",
                    "category": "Safety",
                    "criterion": "Skill forbids using hidden evaluator-only data",
                    "points": 2,
                },
                {
                    "rubric_id": "r4",
                    "category": "Completeness",
                    "criterion": "Skill gives an ordered operational workflow",
                    "points": 2,
                },
                {
                    "rubric_id": "r5",
                    "category": "Noise",
                    "criterion": "Skill fabricates private telecom policy",
                    "points": -2,
                },
            ],
            "maximum_score": 8,
            "minimum_score": -2,
            "baseline_score": 0,
            "metadata": {"source": "dry_run"},
        }


def main() -> int:
    args = parse_args()
    _maybe_reexec_tau2_venv()
    started = time.time()
    run_root = args.run_root.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    summary_path = run_root / "real_llm_train_evolve_smoke_summary.json"
    summary: dict[str, Any] = {
        "skilllift_phase": "train_evolve+test_eval_smoke",
        "domain": args.domain,
        "dry_run": args.dry_run,
        "outer_round_count": args.outer_rounds,
        "run_root": str(run_root.resolve()),
        "models_config": str(Path(args.models_config).resolve()),
        "status": "started",
    }

    try:
        result = run_smoke(args, run_root)
        summary.update(result)
        summary["status"] = "completed"
        summary["elapsed_seconds"] = round(time.time() - started, 2)
        write_json(summary_path, summary)
        print(json.dumps({"summary_path": str(summary_path), "status": "completed"}, sort_keys=True))
        return 0
    except Exception as exc:
        summary.update(
            {
                "status": "failed",
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
                "failure_context": _failure_context(args, run_root),
                "elapsed_seconds": round(time.time() - started, 2),
            }
        )
        write_json(summary_path, summary)
        print(json.dumps({"summary_path": str(summary_path), "status": "failed", "error": str(exc)}, sort_keys=True))
        return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", default="telecom")
    parser.add_argument("--issue-type", default="", help="Optional telecom issue_type bucket, e.g. mobile_data_issue")
    parser.add_argument("--train-tasks", type=int, default=1)
    parser.add_argument("--test-tasks", type=int, default=1)
    parser.add_argument("--skills", type=int, default=2)
    parser.add_argument("--outer-rounds", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--run-root", type=Path, default=ROOT / "runs" / "skilllift_tau2_real_llm_smoke")
    parser.add_argument("--models-config", default=str(AGENTCLAWBENCH / "models_config.json"))
    parser.add_argument("--model", default="")
    parser.add_argument("--skill-generator-model", default="")
    parser.add_argument("--rubricator-model", default="")
    parser.add_argument("--verifier-model", default="")
    parser.add_argument("--fusion-model", default="")
    parser.add_argument("--tau2-model", default="")
    parser.add_argument("--max-steps", type=int, default=3)
    parser.add_argument("--oracle-threshold", type=float, default=0.8)
    parser.add_argument("--rank-alignment-threshold", type=float, default=0.9)
    parser.add_argument("--domain-policy", default="Use only public telecom policy and visible runtime state.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def run_smoke(args: argparse.Namespace, run_root: Path) -> dict[str, Any]:
    sanitize_probability_fields(
        args,
        run_root,
        {"oracle_threshold": 0.8, "rank_alignment_threshold": 0.9},
    )
    if args.domain != "telecom":
        raise ValueError("real LLM smoke is intentionally limited to domain=telecom")
    if args.skills != 2:
        raise ValueError("real LLM smoke currently expects --skills 2")

    _ensure_tau2_importable()
    from tau2.run import get_tasks

    if args.dry_run:
        _install_tau2_dry_run_generate()

    model_ids = _resolve_model_ids(args)
    if args.issue_type:
        train_task_ids = _select_bucket_task_ids(args.domain, "train", args.issue_type, args.train_tasks)
        test_task_ids = _select_bucket_task_ids(args.domain, "test", args.issue_type, args.test_tasks)
        train_tasks = get_tasks(args.domain, task_split_name="train", task_ids=train_task_ids)
        test_tasks = get_tasks(args.domain, task_split_name="test", task_ids=test_task_ids)
    else:
        train_tasks = get_tasks(args.domain, task_split_name="train", num_tasks=args.train_tasks)
        test_tasks = get_tasks(args.domain, task_split_name="test", num_tasks=args.test_tasks)
    train_task_ids = [str(task.id) for task in train_tasks]
    test_task_ids = [str(task.id) for task in test_tasks]
    if set(train_task_ids) & set(test_task_ids):
        raise ValueError("train/test task_id overlap is not allowed")

    manifest_dir = run_root / "manifests"
    train_manifest = manifest_dir / "tau2_train_cluster_manifest.json"
    test_manifest = manifest_dir / "tau2_test_eval_manifest.json"
    _write_single_cluster_manifest(
        train_manifest,
        split="train",
        phase="train_evolve",
        domain=args.domain,
        cluster_id=f"{args.domain}_real_llm_train_00",
        issue_type=args.issue_type or "real_llm_smoke",
        task_ids=train_task_ids,
        seed=args.seed,
    )
    _write_single_cluster_manifest(
        test_manifest,
        split="test",
        phase="test_eval",
        domain=args.domain,
        cluster_id=f"{args.domain}_real_llm_test_00",
        issue_type=args.issue_type or "real_llm_smoke",
        task_ids=test_task_ids,
        seed=args.seed,
    )

    llm_clients = _build_llm_clients(args, model_ids)
    tau2_llm_args = _tau2_llm_args(args, model_ids["tau2"])
    train_config = SkillLiftTau2TrainConfig(
        run_root=run_root,
        manifest_path=train_manifest,
        model=model_ids["tau2"],
        oracle_threshold=args.oracle_threshold,
        seed=args.seed,
        max_steps=args.max_steps,
        domain_policy_by_domain={args.domain: args.domain_policy},
        tau2_llm_args=tau2_llm_args,
    )
    test_config = SkillLiftTau2TrainConfig(
        run_root=run_root,
        manifest_path=test_manifest,
        model=model_ids["tau2"],
        oracle_threshold=args.oracle_threshold,
        seed=args.seed,
        max_steps=args.max_steps,
        domain_policy_by_domain={args.domain: args.domain_policy},
        tau2_llm_args=tau2_llm_args,
    )
    skilllift_config = SkillLiftConfig(
        exp_root=str(run_root / "skilllift_state"),
        exp_name="real_llm_min_train_evolve",
        model=model_ids["tau2"],
        models_config=args.models_config,
        skill_count=args.skills,
        outer_rounds=args.outer_rounds,
        mode_a_iters=1,
        mode_b_iters=1,
        skill_format="markdown_guide",
        rank_alignment_threshold=args.rank_alignment_threshold,
        oracle_threshold=args.oracle_threshold,
        use_llm=True,
        skill_generator_model=model_ids["skill_generator"],
        rubricator_model=model_ids["rubricator"],
        verifier_model=model_ids["verifier"],
        evidence_budget=EvidenceBudgetConfig(level="auto"),
    )
    store = ExperimentStore(run_root / "skilllift_state" / "real_llm_min_train_evolve")
    store.save_config(skilllift_config)

    task_spec = TaskSpec(
        task_name=f"tau2_{args.domain}_train_cluster",
        task_description=(
            f"CoEvo tau2 train_evolve smoke for {args.domain}. "
            f"Train task ids: {', '.join(train_task_ids)}. "
            "Generate markdown guide skills for customer-service agents using only public policy and visible runtime context."
        ),
    )
    store.save_task_spec(task_spec)
    reference_material = "\n".join(
        [
            "# Public tau2 Domain Policy",
            args.domain_policy,
            "",
            "# Train Cluster",
            "\n".join(f"- {task_id}" for task_id in train_task_ids),
        ]
    )

    d12_metrics = D12RepairMetrics()
    skills = _initialize_markdown_skills(
        task_spec=task_spec,
        reference_material=reference_material,
        llm_client=llm_clients["skill_generator"],
        skill_count=args.skills,
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

    train_cluster = load_train_clusters(train_manifest)[0]
    rank_alignment_by_round: list[dict[str, Any]] = []
    cluster_artifact_paths: list[Path] = []

    for round_index in range(args.outer_rounds):
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
            train_tasks,
            skills,
            train_config,
            domain=args.domain,
            run_single_task=_default_tau2_run_single_task,
        )
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
        }
        rank_alignment_by_round.append(round_record)
        write_json(run_root / "round_metrics" / f"outer_{round_index:03d}.json", round_record)

        cluster_artifact_paths.append(
            write_train_cluster_artifact(
                config=train_config,
                cluster_id=f"{train_cluster.cluster_id}_outer_{round_index:03d}",
                domain=train_cluster.domain,
                task_ids=train_cluster.task_ids,
                skills=skills,
                batch_result=batch_result,
                source_train_run_id="real_llm_min_train_evolve_smoke",
            )
        )

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

    fusion_result = run_master_skill_fusion_smoke(
        train_config,
        cluster_artifact_paths=[cluster_artifact_paths[-1]],
        domain=args.domain,
        action_types=["verify_account"],
        fusion_model=model_ids["fusion"],
        fusion_llm=lambda prompt, model: _call_fusion_llm(llm_clients["fusion"], prompt, model),
        repair_metrics={
            "parse_error_count": d12_metrics.parse_error_count,
            "total_generation_attempts": d12_metrics.total_generation_attempts,
        },
    )

    test_result = run_heldout_test_eval(
        test_config,
        source_fusion_artifact_path=fusion_result.artifact_path,
        task_loader=lambda cluster: get_tasks(cluster.domain, task_split_name="test", task_ids=cluster.task_ids),
        run_single_task=_default_tau2_run_single_task,
        no_skill_test_score=None,
        eval_id="real-llm-min-train-evolve-smoke",
    )
    heldout_artifact = json.loads(test_result.artifact_path.read_text(encoding="utf-8"))
    privacy_status = _artifact_privacy_grep(run_root)

    return {
        "domain": args.domain,
        "issue_type": args.issue_type or None,
        "train_task_ids": train_task_ids,
        "test_task_ids": test_task_ids,
        "train_test_overlap": False,
        "model_ids": model_ids,
        "tau2_llm_args_summary": _summarize_tau2_llm_args(tau2_llm_args),
        "outer_round_count": args.outer_rounds,
        "d12_repair_metrics": d12_metrics.to_dict(),
        "rank_alignment_by_round": rank_alignment_by_round,
        "fusion_artifact_path": str(fusion_result.artifact_path.resolve()),
        "master_skill_path": str(fusion_result.master_skill_path.resolve()),
        "best_skill_bundle_path": str(fusion_result.best_skill_bundle_path.resolve()),
        "heldout_test_artifact_path": str(test_result.artifact_path.resolve()),
        "final_heldout_test_score": heldout_artifact.get("heldout_test_score"),
        "artifact_privacy_grep_status": privacy_status,
    }


def _reference_with_seed_skill(
    reference_material: str, skills: dict[SkillKey, EvoSkill]
) -> str:
    seed = skills.get(SkillKey(0, 1))
    if seed is None:
        return reference_material
    return reference_material + "\n\n# Seed Skill Evidence\n" + seed.files.get("SKILL.md", "")


def _initialize_markdown_skills(
    *,
    task_spec: TaskSpec,
    reference_material: str,
    llm_client: LLMClient | DryRunLLMClient,
    skill_count: int,
    d12_metrics: D12RepairMetrics,
    source_model: str,
) -> dict[SkillKey, EvoSkill]:
    seed_package = _generate_seed_package(
        task_spec, reference_material, llm_client, d12_metrics
    )
    seed = _skill_from_package(
        task_spec,
        seed_package,
        SkillKey(0, 1),
        "seed",
        source_model,
    )
    skills = {seed.key: seed}
    for slot in range(1, skill_count):
        package = _generate_variant_package(task_spec, seed, slot, llm_client, d12_metrics)
        skill = _skill_from_package(
            task_spec,
            package,
            SkillKey(slot, 1),
            f"variant_{slot}",
            source_model,
        )
        skills[skill.key] = skill
    return skills


def _generate_seed_package(
    task_spec: TaskSpec,
    reference_material: str,
    llm_client: LLMClient | DryRunLLMClient,
    d12_metrics: D12RepairMetrics,
) -> GeneratedSkillPackage:
    content_block = _content_block_token()
    system, user = build_sg_seed_prompt(
        task_spec,
        reference_material,
        skill_format="markdown_guide",
        content_block=content_block,
    )
    return _call_and_parse_markdown_package(
        task_spec,
        strategy="seed",
        system=system,
        user=user,
        content_block=content_block,
        llm_client=llm_client,
        d12_metrics=d12_metrics,
        temperature=0.0,
    )


def _generate_variant_package(
    task_spec: TaskSpec,
    seed: EvoSkill,
    slot: int,
    llm_client: LLMClient | DryRunLLMClient,
    d12_metrics: D12RepairMetrics,
) -> GeneratedSkillPackage:
    content_block = _content_block_token()
    system, user = build_sg_variant_prompt(
        task_spec,
        seed,
        slot,
        skill_format="markdown_guide",
        content_block=content_block,
    )
    return _call_and_parse_markdown_package(
        task_spec,
        strategy=f"variant_{slot}",
        system=system,
        user=user,
        content_block=content_block,
        llm_client=llm_client,
        d12_metrics=d12_metrics,
        temperature=0.5,
    )


def _preserve_markdown_skill_after_mode_a_failure(
    skill: EvoSkill,
    reason: str,
    *,
    skipped: bool = False,
    repair_attempted: bool = False,
) -> EvoSkill:
    """Mode A update package invalid 时保留原 key(不递增 version)、原文件,打失败/跳过标记。

    对齐 skilllift/skilllift/baselines/sg.py::_preserve_old_skill 的既有语义:
    无真变异产出,就不递增 version,事后可从 version delta 判断哪些 slot 没演化。
    """
    metadata = dict(skill.metadata)
    if skipped:
        metadata.update({"mode_a_update_skipped": True, "mode_a_update_skip_reason": reason})
    else:
        metadata.update(
            {
                "mode_a_update_failed": True,
                "mode_a_update_skip_reason": reason,
                "repair_attempted": repair_attempted,
            }
        )
    return EvoSkill(
        key=skill.key,
        skill_name=skill.skill_name,
        files=dict(skill.files),
        entrypoint=skill.entrypoint,
        metadata=metadata,
    )


def _update_markdown_skill_group(
    *,
    task_spec: TaskSpec,
    skills: dict[SkillKey, EvoSkill],
    receipt: Receipt,
    scores: dict[SkillKey, VerifierScore],
    llm_client: LLMClient | DryRunLLMClient,
    d12_metrics: D12RepairMetrics,
    source_model: str,
) -> dict[SkillKey, EvoSkill]:
    updated: dict[SkillKey, EvoSkill] = {}
    for key, skill in sorted(skills.items()):
        score = scores[key]
        if not _actionable_rubrics(receipt, score):
            updated[key] = _preserve_markdown_skill_after_mode_a_failure(
                skill, "no_actionable_rubrics", skipped=True
            )
            continue
        new_key = SkillKey(skill.key.slot, skill.key.version + 1)
        content_block = _content_block_token()
        system, user = build_sg_update_prompt(
            task_spec,
            skill,
            receipt,
            score,
            skill_format="markdown_guide",
            content_block=content_block,
        )
        try:
            package = _call_and_parse_markdown_package(
                task_spec,
                strategy="mode_a_update",
                system=system,
                user=user,
                content_block=content_block,
                llm_client=llm_client,
                d12_metrics=d12_metrics,
                temperature=0.2,
                receipt=receipt,
                score=score,
            )
            updated[new_key] = _skill_from_package(
                task_spec,
                package,
                new_key,
                "mode_a_update",
                source_model,
            )
        except SkillPackageError as exc:
            # Mode A update package invalid (parse/repair/validation 失败):
            # 保留旧 skill,不递增 version,继续处理同组其他 skill。
            # LLMOutputError(call_text 层,底层已重试)不在此捕获,继续向上 raise。
            d12_metrics.attempts.append(
                {
                    "strategy": "mode_a_update",
                    "outcome": "failed_after_repair_preserved",
                    "error": str(exc),
                }
            )
            updated[key] = _preserve_markdown_skill_after_mode_a_failure(
                skill, str(exc), repair_attempted=True
            )
    return updated


def _call_and_parse_markdown_package(
    task_spec: TaskSpec,
    *,
    strategy: str,
    system: str,
    user: str,
    content_block: str,
    llm_client: LLMClient | DryRunLLMClient,
    d12_metrics: D12RepairMetrics,
    temperature: float,
    receipt: Receipt | None = None,
    score: VerifierScore | None = None,
) -> GeneratedSkillPackage:
    raw = llm_client.call_text(system, user, temperature=temperature)
    d12_metrics.total_generation_attempts += 1
    try:
        package = parse_markdown_guide_output(raw, "markdown_guide", content_block)
        if receipt is not None and score is not None:
            _validate_mode_a_update_payload(
                _markdown_validation_payload(package), package, receipt, score
            )
        d12_metrics.attempts.append({"strategy": strategy, "initial_parse": "ok"})
        return package
    except SkillPackageError as exc:
        d12_metrics.parse_error_count += 1
        repair_system, repair_user = build_sg_repair_prompt(
            task_spec,
            strategy,
            raw,
            {"error_type": exc.__class__.__name__, "message": str(exc)},
            skill_format="markdown_guide",
            content_block=content_block,
        )
        repaired = llm_client.call_text(repair_system, repair_user, temperature=0.0)
        try:
            package = parse_markdown_guide_output(repaired, "markdown_guide", content_block)
            if receipt is not None and score is not None:
                _validate_mode_a_update_payload(
                    _markdown_validation_payload(package), package, receipt, score
                )
            d12_metrics.repaired_success_count += 1
            d12_metrics.attempts.append(
                {"strategy": strategy, "initial_parse": "failed", "repair_parse": "ok", "error": str(exc)}
            )
            return package
        except SkillPackageError as repair_exc:
            d12_metrics.failed_after_repair_count += 1
            d12_metrics.attempts.append(
                {
                    "strategy": strategy,
                    "initial_parse": "failed",
                    "repair_parse": "failed",
                    "error": str(exc),
                    "repair_error": str(repair_exc),
                }
            )
            raise


def _skill_from_package(
    task_spec: TaskSpec,
    package: GeneratedSkillPackage,
    key: SkillKey,
    strategy: str,
    source_model: str,
) -> EvoSkill:
    skill_name = f"{_safe_name(task_spec.task_name)}_{strategy}"
    metadata = {
        **package.metadata,
        "strategy": strategy,
        "source_model": source_model,
        "prompt_hash": f"sg:{strategy}:{uuid.uuid4().hex[:8]}",
    }
    return EvoSkill(
        key=key,
        skill_name=skill_name,
        files=_normalize_skill_files(package.files, skill_name, package.metadata.get("summary")),
        entrypoint=None,
        metadata=metadata,
    )


def _call_fusion_llm(llm_client: LLMClient | DryRunLLMClient, prompt: str, model: str) -> str:
    if isinstance(llm_client, DryRunLLMClient):
        del prompt, model
        return "\n".join(
            [
                "`````",
                "# MASTER_SKILL",
                "",
                "## Action Checkpoint Index",
                "- verify_account",
                "",
                "## verify_account",
                "- Verify visible account or service state before action.",
                "- Check the result after each action and stop if the requested issue is resolved.",
                "`````",
            ]
        )
    return llm_client.call_text(
        "You fuse markdown guide skills. Return only the requested fenced MASTER_SKILL.md.",
        prompt,
        temperature=0.0,
    )


def _build_llm_clients(args: argparse.Namespace, model_ids: dict[str, str]) -> dict[str, LLMClient | DryRunLLMClient]:
    if args.dry_run:
        return {
            "skill_generator": DryRunLLMClient(model_ids["skill_generator"]),
            "rubricator": DryRunLLMClient(model_ids["rubricator"]),
            "verifier": DryRunLLMClient(model_ids["verifier"]),
            "fusion": DryRunLLMClient(model_ids["fusion"]),
        }
    return {
        "skill_generator": create_llm_client(args.models_config, model_ids["skill_generator"], role="skill_generator"),
        "rubricator": create_llm_client(args.models_config, model_ids["rubricator"], role="rubricator"),
        "verifier": create_llm_client(args.models_config, model_ids["verifier"], role="verifier"),
        "fusion": create_llm_client(args.models_config, model_ids["fusion"], role="skill_generator"),
    }


def _resolve_model_ids(args: argparse.Namespace) -> dict[str, str]:
    if args.dry_run:
        fallback = args.model or "dry-run-model"
        return {
            "skill_generator": args.skill_generator_model or fallback,
            "rubricator": args.rubricator_model or fallback,
            "verifier": args.verifier_model or fallback,
            "fusion": args.fusion_model or fallback,
            "tau2": args.tau2_model or fallback,
        }
    config_path = Path(args.models_config)
    try:
        skill_generator = args.skill_generator_model or args.model or resolve_llm_config(config_path, None).model
        rubricator = args.rubricator_model or args.model or skill_generator
        verifier = args.verifier_model or args.model or skill_generator
        fusion = args.fusion_model or args.model or skill_generator
        tau2 = args.tau2_model or args.model or skill_generator
        return {
            "skill_generator": skill_generator,
            "rubricator": rubricator,
            "verifier": verifier,
            "fusion": fusion,
            "tau2": tau2,
        }
    except Exception as exc:
        raise LLMOutputError(f"failed to resolve models from {config_path}: {exc}") from exc


def _failure_context(args: argparse.Namespace, run_root: Path) -> dict[str, Any]:
    context: dict[str, Any] = {
        "run_root": str(run_root.resolve()),
        "existing_artifact_paths": [
            str(path.resolve()) for path in sorted(run_root.rglob("*")) if path.is_file()
        ],
    }
    try:
        model_ids = _resolve_model_ids(args)
        context["model_ids"] = model_ids
    except Exception as exc:
        context["model_resolution_error"] = {
            "error_type": exc.__class__.__name__,
            "error_message": str(exc),
        }
        return context

    if args.dry_run:
        context["llm_endpoints"] = {
            role: {
                "provider": "dry-run",
                "model": model,
                "display_model": f"dry-run/{model}",
                "base_url": "dry-run://local",
            }
            for role, model in model_ids.items()
            if role != "tau2"
        }
        return context

    endpoints: dict[str, Any] = {}
    for role in ["skill_generator", "rubricator", "verifier", "fusion"]:
        try:
            resolved = resolve_llm_config(args.models_config, model_ids[role])
            endpoints[role] = {
                "provider": resolved.provider,
                "model": resolved.model,
                "display_model": resolved.display_name,
                "base_url": resolved.base_url,
                "timeout": resolved.timeout,
                "max_retries": resolved.max_retries,
                "api_key_source": resolved.api_key_source,
                "requested_model_matched": resolved.requested_model_matched,
            }
        except Exception as exc:
            endpoints[role] = {
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
            }
    context["llm_endpoints"] = endpoints
    try:
        context["tau2_llm_args_summary"] = _summarize_tau2_llm_args(
            _tau2_llm_args(args, model_ids["tau2"])
        )
    except Exception as exc:
        context["tau2_llm_args_error"] = {
            "error_type": exc.__class__.__name__,
            "error_message": str(exc),
        }
    return context


def _tau2_llm_args(args: argparse.Namespace, requested_model: str) -> dict[str, Any]:
    if args.dry_run:
        return {}
    resolved = resolve_llm_config(args.models_config, requested_model)
    return {
        "api_key": resolved.api_key,
        "api_base": resolved.base_url,
        "base_url": resolved.base_url,
        "custom_llm_provider": "openai",
        "timeout": resolved.timeout,
    }


def _select_bucket_task_ids(domain: str, split: str, issue_type: str, count: int) -> list[str]:
    if count <= 0:
        raise ValueError(f"{split} task count must be positive")
    if not issue_type:
        return []
    if domain != "telecom":
        raise ValueError("--issue-type bucket selection is currently limited to telecom")
    split_path = ROOT / "tau2-bench" / "data" / "tau2" / "domains" / domain / "split_tasks.json"
    split_payload = json.loads(split_path.read_text(encoding="utf-8"))
    if split not in split_payload:
        raise ValueError(f"split {split!r} not found in {split_path}")
    prefix = f"[{issue_type}]"
    candidates = sorted(str(task_id) for task_id in split_payload[split] if str(task_id).startswith(prefix))
    if len(candidates) < count:
        raise ValueError(
            f"{domain} {split} bucket {issue_type!r} has only {len(candidates)} tasks, need {count}"
        )
    return candidates[:count]


def _summarize_tau2_llm_args(llm_args: dict[str, Any]) -> dict[str, Any]:
    if not llm_args:
        return {}
    return {
        key: ("<redacted>" if "key" in key.lower() else value)
        for key, value in llm_args.items()
    }


def _write_single_cluster_manifest(
    path: Path,
    *,
    split: str,
    phase: str,
    domain: str,
    cluster_id: str,
    issue_type: str,
    task_ids: list[str],
    seed: int,
) -> None:
    write_json(
        path,
        {
            "version": "v2.4",
            "split": split,
            "phase": phase,
            "seed": seed,
            "domains": {
                domain: [
                    {
                        "cluster_id": cluster_id,
                        "issue_type": issue_type,
                        "semantic_key": issue_type,
                        "task_ids": task_ids,
                        "size": len(task_ids),
                    }
                ]
            },
        },
    )


def _artifact_privacy_grep(root: Path) -> dict[str, Any]:
    matches: list[dict[str, str]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in FORBIDDEN_ARTIFACT_PATTERNS:
            if pattern in text:
                matches.append({"path": str(path), "pattern": pattern})
    return {"ok": not matches, "matches": matches}


def _ensure_tau2_importable() -> None:
    if str(TAU2_SRC) not in sys.path:
        sys.path.insert(0, str(TAU2_SRC))


def _install_tau2_dry_run_generate() -> None:
    from tau2.agent import llm_agent as agent_mod
    from tau2.data_model.message import AssistantMessage
    from tau2.user import user_simulator as user_mod

    def fake_generate(model, tools=None, messages=None, call_name=None, **kwargs):
        del model, tools, messages, kwargs
        if call_name == "agent_response":
            return AssistantMessage.text("I can help. I will inspect the visible telecom state before taking action.")
        if call_name == "user_simulator_response":
            return AssistantMessage.text("I need help with my mobile data service.")
        return AssistantMessage.text("###STOP###")

    agent_mod.generate = fake_generate
    user_mod.generate = fake_generate


def _maybe_reexec_tau2_venv() -> None:
    if os.environ.get("SKILLLIFT_TAU2_REAL_LLM_SMOKE_REEXEC") == "1":
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
    env["SKILLLIFT_TAU2_REAL_LLM_SMOKE_REEXEC"] = "1"
    os.execvpe(str(TAU2_PYTHON), [str(TAU2_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]], env)


def _markdown_validation_payload(package: GeneratedSkillPackage) -> dict[str, Any]:
    return {
        "package_mode": package.metadata.get("package_mode", "full"),
        "files": [{"path": path, "content": content} for path, content in package.files.items()],
        "entrypoint": None,
        "metadata": package.metadata,
    }


def _dry_markdown_package(block: str, strategy: str, *, mode: str = "full", marker: str = "seed") -> str:
    metadata = []
    if mode == "patch":
        metadata = [
            "<targeted_rubrics>r1,r2</targeted_rubrics>",
            "<expected_effect>Adds visible-state verification and post-action checks.</expected_effect>",
            "<changed_files>SKILL.md</changed_files>",
        ]
    return "\n".join(
        [
            f'<package mode="{mode}" strategy="{strategy}">',
            "<summary>Dry-run markdown guide package.</summary>",
            '<file path="SKILL.md" content_block="' + block + '"/>',
            *metadata,
            f"<{block}>",
            f"# Tau2 {marker} guide",
            "",
            "- Verify visible customer and service state before taking action.",
            "- Use only public policy and runtime-visible tool results.",
            "- Check the outcome after every action.",
            f"</{block}>",
            "</package>",
        ]
    )


def _extract_prompt_content_block(prompt: str) -> str:
    match = re.search(r"content_block=\"([A-Za-z_][A-Za-z0-9_-]*)\"", prompt)
    return match.group(1) if match else "skill_block_dryrun"


def _extract_current_receipt_rubrics(prompt: str) -> dict[str, int]:
    payload = _extract_json_after_marker(prompt, "[Receipt]")
    rubrics = (payload or {}).get("rubrics") or []
    parsed = {
        str(item.get("rubric_id")): int(item.get("points") or 1)
        for item in rubrics
        if isinstance(item, dict)
    }
    return parsed or {"r1": 1, "r2": 1, "r3": 1, "r4": 1, "r5": -1}


def _extract_json_after_marker(prompt: str, marker: str) -> dict[str, Any] | None:
    if marker not in prompt:
        return None
    text = prompt.split(marker, 1)[1].strip()
    if not text.startswith("{"):
        start = text.find("{")
        if start == -1:
            return None
        text = text[start:]
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[: index + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "skill"


if __name__ == "__main__":
    raise SystemExit(main())
