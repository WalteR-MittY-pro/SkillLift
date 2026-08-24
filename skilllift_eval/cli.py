from __future__ import annotations

from argparse import ArgumentParser
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import os
import sys

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - fallback for bare Python envs
    yaml = None

from .model_endpoints import ModelEndpointProfile, endpoint_config_hash, resolve_endpoints
from .runners.skilllift_wildclaw import (
    SOURCE as SKILLLIFT_WILDCLAW_SOURCE,
    TASK_SAMPLE_POLICY_ID as SKILLLIFT_WILDCLAW_TASK_SAMPLE_POLICY_ID,
    SkillLiftWildClawRunSettings,
    SkillLiftWildClawRunner,
)
from .runners.coevoskills_wildclaw import (
    SOURCE as SKILLLIFTSKILLS_WILDCLAW_SOURCE,
    TASK_SAMPLE_POLICY_ID as SKILLLIFTSKILLS_WILDCLAW_TASK_SAMPLE_POLICY_ID,
    CoEvoSkillsWildClawRunSettings,
    CoEvoSkillsWildClawRunner,
)
from .runners.human_skill_wildclaw import (
    FULL_TASK_SAMPLE_POLICY_ID as HUMAN_SKILL_FULL_TASK_SAMPLE_POLICY_ID,
    SINGLE_TASK_SAMPLE_POLICY_ID as HUMAN_SKILL_SINGLE_TASK_SAMPLE_POLICY_ID,
    SOURCE as HUMAN_SKILL_SOURCE,
    HumanSkillWildClawRunner,
    human_skill_source_hash,
    validate_human_skill_inventory,
)
from .runners.tau2 import (
    EMPTY_TAU2_PROMPT_HASH,
    RUNNER_VERSION as TAU2_RUNNER_VERSION,
    Tau2RunSettings,
    Tau2Runner,
)
from .runners.wildclaw import WildClawRunSettings
from .schemas import AlgorithmParamProfile, MatrixCellKey, SkillBundle, stable_hash


BASELINES = ["no_skill", "human_skill", "autoskill", "textgrad", "skilllift", "coevoskills"]
SKILL_BASELINES = ["human_skill", "autoskill", "textgrad", "skilllift", "coevoskills"]
MATRIX_BASELINES = ["no_skill", "autoskill", "textgrad", "skilllift"]
BASELINE_ALIASES = {"autoskills": "autoskill"}
BENCHMARKS = ["wildclawbench", "tau2"]
EVALUATION_MODES = ["native_end_to_end", "budget_matched"]
TAU2_ALLOWED_DOMAINS = ["airline", "retail", "telecom"]
TAU2_DEFAULT_SPLIT = "base"
A0_CONTRACT_REFS = {
    "repo_state": "runs/task_0_2_to_0_5/repo_state.json",
    "external_contracts": "runs/task_0_2_to_0_5/external_contracts.json",
    "baseline_cli_contract": "runs/task_0_2_to_0_5/baseline_cli_contract.json",
    "context_budget_contract": "runs/task_0_2_to_0_5/context_budget_contract.json",
}


@dataclass(frozen=True)
class MatrixCell:
    matrix_cell_id: str
    baseline: str
    benchmark: str
    model_label: str
    model_endpoint_id: str
    provider_model_id: str
    evaluation_mode: str
    context_budget_profile: str
    visible_feedback_policy_id: str
    oracle_call_budget: str
    task_sample_policy_id: str
    round_budget: str
    token_budget: str
    algorithm_param_profile: str = "paper_default"
    task_set_id: str | None = None
    task_count: int | None = None
    tau2_domains: list[str] | None = None
    tau2_split: str | None = None
    models_config_path: str | None = None
    run_root: str | None = None
    algorithm_override_path: str | None = None
    algorithm_override_diff_path: str | None = None


def load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if yaml is None:
        return _minimal_yaml(text)
    data = yaml.safe_load(text)
    return data or {}


def _minimal_yaml(text: str) -> dict[str, Any]:
    # Intentionally tiny fallback for already-validated flat-ish config files.
    result: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, result)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or raw.lstrip().startswith("-"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if ":" not in raw:
            continue
        key, value = raw.strip().split(":", 1)
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        value = value.strip()
        if not value:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(value)
    return result


def _parse_scalar(value: str) -> Any:
    if value in {"null", "None"}:
        return None
    if value == "{}":
        return {}
    if value in {"true", "false"}:
        return value == "true"
    try:
        return int(value)
    except ValueError:
        return value.strip("'\"")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_config(path: Path) -> dict[str, Any]:
    return load_yaml(path)


def context_budget(config: dict[str, Any], profile_id: str) -> dict[str, Any]:
    budgets = config.get("context_budgets") or {}
    if profile_id in budgets:
        return budgets[profile_id]
    if profile_id == "matched_v1":
        matched = Path("skilllift_eval/context_budgets/matched_v1.yaml")
        if matched.exists():
            return load_yaml(matched)
    raise ValueError(f"unknown context_budget_profile {profile_id}")


def matrix_models(config: dict[str, Any]) -> list[str]:
    configured = (config.get("matrix") or {}).get("models")
    models = configured if configured is not None else list((config.get("model_endpoints") or {}).keys())
    if not isinstance(models, list) or not models:
        raise ValueError("matrix requires at least one configured model")
    return [str(model) for model in models]


def build_matrix_cells(config: dict[str, Any], config_path: Path, run_root: Path, evaluation_mode: str, context_budget_profile: str) -> list[MatrixCell]:
    if evaluation_mode != "native_end_to_end":
        return []
    budget = context_budget(config, context_budget_profile)
    models = matrix_models(config)
    endpoints = resolve_endpoints(config, models)
    cells: list[MatrixCell] = []
    for baseline in MATRIX_BASELINES:
        for benchmark in BENCHMARKS:
            for model in models:
                endpoint = endpoints[model]
                key = MatrixCellKey(evaluation_mode, baseline, benchmark, model)
                is_tau2 = benchmark == "tau2"
                cells.append(
                    MatrixCell(
                        matrix_cell_id=key.matrix_cell_id,
                        baseline=baseline,
                        benchmark=benchmark,
                        model_label=model,
                        model_endpoint_id=endpoint.model_endpoint_id,
                        provider_model_id=endpoint.provider_model_id,
                        evaluation_mode=evaluation_mode,
                        context_budget_profile=context_budget_profile,
                        visible_feedback_policy_id=budget["visible_feedback_policy_id"],
                        oracle_call_budget=budget["oracle_call_budget"],
                        task_sample_policy_id=budget["task_sample_policy_id"],
                        round_budget=budget["round_budget"],
                        token_budget=budget["token_budget"],
                        task_set_id="tau2_official_base_airline_retail_telecom" if is_tau2 else "wildclawbench_full_60",
                        task_count=None if is_tau2 else 60,
                        tau2_domains=TAU2_ALLOWED_DOMAINS if is_tau2 else None,
                        tau2_split=TAU2_DEFAULT_SPLIT if is_tau2 else None,
                        models_config_path=str(config_path),
                        run_root=str(run_root),
                    )
                )
    return cells


def handle_matrix(args: Any) -> int:
    if not args.dry_run:
        raise SystemExit("only --dry-run is implemented in Phase 1")
    load_env_files(Path.cwd())
    config = load_config(Path(args.config))
    run_root = Path(args.run_root)
    cells = build_matrix_cells(config, Path(args.config), run_root, args.evaluation_mode, args.context_budget_profile)
    run_root.mkdir(parents=True, exist_ok=True)
    if args.evaluation_mode == "native_end_to_end":
        write_json(run_root / "matrix_cells.json", [asdict(cell) for cell in cells])
    else:
        (run_root / "matrix_cells.json").unlink(missing_ok=True)
    endpoints = resolve_endpoints(config, matrix_models(config))
    write_json(run_root / "model_endpoints.redacted.json", {k: v.redacted() for k, v in endpoints.items()})
    if args.evaluation_mode == "budget_matched":
        print(f"budget_matched interface accepted; wrote {len(cells)} budget_matched matrix cells to {run_root}")
    else:
        print(f"wrote {len(cells)} {args.evaluation_mode} matrix cells to {run_root}")
    return 0


def handle_audit(args: Any) -> int:
    run_root = Path(args.run_root)
    summary_path = run_root / "matrix_summary.native_end_to_end.json"
    smoke_summary_path = run_root / "cell_summary.json"
    findings: list[str] = []
    warnings: list[str] = []
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        cells = summary.get("cells") or []
        if len(cells) != 4:
            findings.append(f"expected 4 Phase 3 cells, found {len(cells)}")
    elif smoke_summary_path.exists():
        cells = [json.loads(smoke_summary_path.read_text(encoding="utf-8"))]
        warnings.append("single-task smoke run; not a full native matrix audit")
    else:
        findings.append("missing matrix_summary.native_end_to_end.json")
        cells = []
    for cell in cells:
        _audit_cell(cell, findings, warnings)
    secret_hits = scan_artifacts_for_secrets(run_root)
    if secret_hits:
        findings.extend([f"secret material found in {path}" for path in secret_hits])
    report = {
        "run_root": str(run_root.resolve()),
        "status": "passed" if not findings else "failed",
        "findings": findings,
        "warnings": warnings,
        "cell_count": len(cells),
        "secret_scan": {"status": "passed" if not secret_hits else "failed", "hits": secret_hits},
    }
    write_json(run_root / "audit_report.json", report)
    (run_root / "audit_report.md").write_text(_audit_markdown(report), encoding="utf-8")
    print(f"audit {report['status']}: {len(findings)} finding(s), {len(warnings)} warning(s)")
    return 0 if not findings else 1


def _audit_cell(cell: dict[str, Any], findings: list[str], warnings: list[str]) -> None:
    artifact = str(cell.get("artifact") or "")
    artifact_path = artifact.split(":", 1)[0] if artifact.startswith("/") else artifact
    if artifact_path and not Path(artifact_path).exists():
        findings.append(f"{cell.get('matrix_cell_id')} artifact missing: {artifact}")
    if cell.get("source") == "external_cited":
        if cell.get("mean_score") is None:
            findings.append(f"{cell.get('matrix_cell_id')} cited cell missing mean_score")
        return
    if cell.get("source") not in {
        "self_run",
        "single_task_smoke",
        SKILLLIFT_WILDCLAW_SOURCE,
        SKILLLIFTSKILLS_WILDCLAW_SOURCE,
        HUMAN_SKILL_SOURCE,
    }:
        findings.append(f"{cell.get('matrix_cell_id')} has unsupported source {cell.get('source')}")
    records_path = Path(artifact_path) / "task_run_records.json"
    if not records_path.exists():
        findings.append(f"{cell.get('matrix_cell_id')} missing task_run_records.json")
        return
    records = json.loads(records_path.read_text(encoding="utf-8"))
    if cell.get("source") == "single_task_smoke":
        if cell.get("expected_count") != 1 or len(records) != 1:
            findings.append(f"{cell.get('matrix_cell_id')} smoke run must contain exactly one task record")
        for record in records:
            if record.get("task_sample_policy_id") != "single_task_06_01_smoke":
                findings.append(f"{cell.get('matrix_cell_id')} smoke record has wrong task_sample_policy_id")
            for path_key in ["loader_manifest_path", "raw_output_path", "score_path"]:
                path = Path(str(record.get(path_key) or ""))
                if not path.exists():
                    findings.append(f"{cell.get('matrix_cell_id')} smoke record missing {path_key}: {path}")
    if cell.get("source") == SKILLLIFT_WILDCLAW_SOURCE:
        _audit_skilllift_wildclaw_smoke(cell, records, findings)
    if cell.get("source") == SKILLLIFTSKILLS_WILDCLAW_SOURCE:
        _audit_coevoskills_wildclaw(cell, records, Path(artifact_path), findings)
    if cell.get("status") == "FAILED":
        if not records or not all(record.get("status") == "failed" for record in records):
            findings.append(f"{cell.get('matrix_cell_id')} FAILED cell lacks failed TaskRunRecord evidence")
        warnings.append(f"{cell.get('matrix_cell_id')} is FAILED: {cell.get('artifact')}")


def _audit_skilllift_wildclaw_smoke(cell: dict[str, Any], records: list[dict[str, Any]], findings: list[str]) -> None:
    if cell.get("expected_count") != 1:
        findings.append(f"{cell.get('matrix_cell_id')} CoEvo ModeB smoke must target exactly one benchmark task")
    if len(records) < 2:
        findings.append(f"{cell.get('matrix_cell_id')} CoEvo ModeB smoke must contain at least two oracle task records")
    for record in records:
        if record.get("task_sample_policy_id") != SKILLLIFT_WILDCLAW_TASK_SAMPLE_POLICY_ID:
            findings.append(f"{cell.get('matrix_cell_id')} CoEvo ModeB record has wrong task_sample_policy_id")
        for path_key in ["loader_manifest_path", "raw_output_path", "score_path"]:
            path = Path(str(record.get(path_key) or ""))
            if not path.exists():
                findings.append(f"{cell.get('matrix_cell_id')} CoEvo ModeB record missing {path_key}: {path}")
    exp_dir = Path(str(cell.get("skilllift_exp_dir") or ""))
    if not exp_dir.exists():
        findings.append(f"{cell.get('matrix_cell_id')} missing skilllift_exp_dir: {exp_dir}")
        return
    oracle_scores = exp_dir / "oracle_scores" / "outer_000_mode_b_batch.json"
    if not oracle_scores.exists():
        findings.append(f"{cell.get('matrix_cell_id')} missing CoEvo oracle_scores batch: {oracle_scores}")
    mode_b_states = list((exp_dir / "round_states").glob("*_mode_b.json"))
    if not mode_b_states:
        findings.append(f"{cell.get('matrix_cell_id')} missing CoEvo mode_b round_states")
    elif not any(_round_state_has_rank_alignment(path) for path in mode_b_states):
        findings.append(f"{cell.get('matrix_cell_id')} CoEvo mode_b round_states missing rank_alignment")
    revision_prompts = list((exp_dir / "receipt_revision_attempts").glob("*/prompt.txt"))
    if not revision_prompts:
        findings.append(f"{cell.get('matrix_cell_id')} missing rubricator revision prompt evidence")
    elif not any(_prompt_contains_rank_alignment(path) for path in revision_prompts):
        findings.append(f"{cell.get('matrix_cell_id')} rubricator prompt missing rank alignment fields")


def _audit_coevoskills_wildclaw(
    cell: dict[str, Any],
    records: list[dict[str, Any]],
    artifact_root: Path,
    findings: list[str],
) -> None:
    if cell.get("expected_count") != 1:
        findings.append(f"{cell.get('matrix_cell_id')} CoEvoSkills run must target one task")
    if len(records) < 2:
        findings.append(
            f"{cell.get('matrix_cell_id')} CoEvoSkills run needs optimization Oracle and final evaluation records"
        )
    run_ids = [str(record.get("run_id") or "") for record in records]
    if len(run_ids) != len(set(run_ids)):
        findings.append(f"{cell.get('matrix_cell_id')} CoEvoSkills TaskRunRecord ids are not unique")
    if not (artifact_root / "artifact_rollout_records.json").exists():
        findings.append(f"{cell.get('matrix_cell_id')} missing artifact_rollout_records.json")
    if not (artifact_root / "coevoskills_experiment" / "final_skill" / "SKILL.md").exists():
        findings.append(f"{cell.get('matrix_cell_id')} missing final CoEvoSkills package")
    for record in records:
        policy = str(record.get("task_sample_policy_id") or "")
        if not policy.startswith(SKILLLIFTSKILLS_WILDCLAW_TASK_SAMPLE_POLICY_ID):
            findings.append(
                f"{cell.get('matrix_cell_id')} CoEvoSkills record has wrong task_sample_policy_id"
            )
        for path_key in ["loader_manifest_path", "raw_output_path", "score_path"]:
            path = Path(str(record.get(path_key) or ""))
            if not path.exists():
                findings.append(
                    f"{cell.get('matrix_cell_id')} CoEvoSkills record missing {path_key}: {path}"
                )


def _round_state_has_rank_alignment(path: Path) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return (
        payload.get("mode") == "mode_b"
        and payload.get("rank_alignment") is not None
        and bool(payload.get("verifier_rank"))
        and bool(payload.get("oracle_rank"))
    )


def _prompt_contains_rank_alignment(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return all(marker in text for marker in ["rank_alignment", "verifier_rank", "oracle_rank", "rank_mismatches"])


def scan_artifacts_for_secrets(run_root: Path) -> list[str]:
    secrets = []
    for env_path in [Path(".env"), Path("tau2-bench/.env")]:
        if not env_path.exists():
            continue
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            if not raw.strip() or raw.lstrip().startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            secret_key = key.strip().upper()
            if not any(marker in secret_key for marker in ["KEY", "TOKEN", "SECRET", "PASSWORD"]):
                continue
            value = value.strip().strip("'\"")
            if len(value) >= 12:
                secrets.append(value)
    hits: list[str] = []
    for path in run_root.rglob("*"):
        if not path.is_file() or path.suffix not in {".json", ".jsonl", ".md", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(secret and secret in text for secret in secrets):
            hits.append(str(path))
    return hits


def _audit_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Audit Report", "", f"Status: {report['status']}", ""]
    if report["findings"]:
        lines.append("## Findings")
        lines.extend(f"- {finding}" for finding in report["findings"])
        lines.append("")
    if report["warnings"]:
        lines.append("## Warnings")
        lines.extend(f"- {warning}" for warning in report["warnings"])
        lines.append("")
    lines.append(f"Secret scan: {report['secret_scan']['status']}")
    return "\n".join(lines) + "\n"


def canonical_baseline(value: str, *, include_no_skill: bool = False) -> str:
    baseline = BASELINE_ALIASES.get(value, value)
    allowed = BASELINES if include_no_skill else SKILL_BASELINES
    if baseline not in allowed:
        raise ValueError(f"unknown baseline {value}; expected one of {', '.join(allowed)}")
    return baseline


def parse_domains(value: str | None) -> list[str]:
    domains = TAU2_ALLOWED_DOMAINS if value is None else [d.strip() for d in value.split(",") if d.strip()]
    bad = [d for d in domains if d not in TAU2_ALLOWED_DOMAINS]
    if bad:
        raise ValueError(f"unsupported tau2 domains: {', '.join(bad)}")
    return domains


def parse_algo_param(value: str) -> tuple[str, Any]:
    if "=" not in value:
        raise ValueError(f"--algo-param must be key=value, got {value}")
    key, raw = value.split("=", 1)
    return key, _parse_scalar(raw)


def load_algorithm_profile(root: Path, baseline: str, profile_id: str) -> AlgorithmParamProfile:
    path = root / "skilllift_eval" / "algorithm_params" / f"{baseline}.{profile_id}.yaml"
    data = load_yaml(path)
    if data.get("needs_confirmation"):
        raise ValueError(f"algorithm profile {path} still needs confirmation")
    return AlgorithmParamProfile.from_dict(data)


def apply_algorithm_override(profile: AlgorithmParamProfile, override_path: Path | None, inline: list[str]) -> AlgorithmParamProfile:
    overrides: dict[str, Any] = {}
    if override_path is not None:
        data = load_yaml(override_path)
        overrides.update(data.get("resolved_params") or data.get("paper_default_params") or data)
    for item in inline:
        key, value = parse_algo_param(item)
        overrides[key] = value
    if not overrides:
        return profile
    return profile.with_overrides(overrides)


def load_a0_contracts(root: Path) -> dict[str, str]:
    for rel in A0_CONTRACT_REFS.values():
        path = root / rel
        if not path.exists():
            raise ValueError(f"missing A0 contract {rel}")
        json.loads(path.read_text(encoding="utf-8"))
    return A0_CONTRACT_REFS.copy()


def handle_run(args: Any) -> int:
    baseline = canonical_baseline(args.baseline, include_no_skill=True)
    if args.tasks_mode == "single" and not args.tasks_filter:
        raise ValueError("--tasks.mode single requires --tasks.filter")
    if args.tasks_mode == "resume" and not args.tasks_checkpoint:
        raise ValueError("--tasks.mode resume requires --tasks.checkpoint")
    if args.tasks_filter and args.tasks_mode != "single":
        raise ValueError("--tasks.filter is only valid with --tasks.mode single")
    if args.tasks_checkpoint and args.tasks_mode != "resume":
        raise ValueError("--tasks.checkpoint is only valid with --tasks.mode resume")
    if args.benchmark != "tau2" and (args.tau2_domains or args.tau2_split):
        raise ValueError("--tau2.domains/--tau2.split are only valid with --benchmark tau2")
    tau2_domains = parse_domains(args.tau2_domains) if args.benchmark == "tau2" else None
    tau2_split = args.tau2_split or TAU2_DEFAULT_SPLIT if args.benchmark == "tau2" else None
    if args.benchmark == "tau2" and tau2_split != TAU2_DEFAULT_SPLIT:
        raise ValueError("tau2 split must be base for Phase 3")

    root = Path.cwd()
    load_env_files(root)
    config = load_config(Path(args.config))
    budget = context_budget(config, args.context_budget_profile)
    endpoints = resolve_endpoints(config, [args.model])
    endpoint = endpoints[args.model]
    profile = resolve_run_profile(root, baseline, args)
    a0_refs = load_a0_contracts(root)
    key = MatrixCellKey(args.evaluation_mode, baseline, args.benchmark, args.model)
    run_config = {
        "baseline_input": args.baseline,
        "baseline": baseline,
        "canonical_baseline": baseline,
        "benchmark": args.benchmark,
        "model_label": args.model,
        "model_endpoint": endpoint.redacted(),
        "model_endpoint_id": endpoint.model_endpoint_id,
        "provider_model_id": endpoint.provider_model_id,
        "endpoint_config_hash": endpoint_config_hash(endpoint),
        "evaluation_mode": args.evaluation_mode,
        "context_budget_profile": args.context_budget_profile,
        "visible_feedback_policy_id": budget["visible_feedback_policy_id"],
        "oracle_call_budget": budget["oracle_call_budget"],
        "task_sample_policy_id": budget["task_sample_policy_id"],
        "round_budget": budget["round_budget"],
        "token_budget": budget["token_budget"],
        "matrix_cell_id": key.matrix_cell_id,
        "algorithm_param_profile": profile.profile_id,
        "algorithm_param_hash": profile.param_hash,
        "paper_default_params": profile.paper_default_params,
        "resolved_params": profile.resolved_params,
        "override_diff": profile.override_diff,
        "algorithm_override_path": args.algorithm_override,
        "algorithm_override_diff_path": None,
        "tasks": {"mode": args.tasks_mode, "filter": args.tasks_filter, "checkpoint": args.tasks_checkpoint},
        "tau2": {"domains": tau2_domains, "split": tau2_split} if args.benchmark == "tau2" else None,
        "a0_contract_refs": a0_refs,
    }
    if baseline == "skilllift" and args.benchmark == "wildclawbench" and args.tasks_mode == "single":
        run_config["task_sample_policy_id"] = SKILLLIFT_WILDCLAW_TASK_SAMPLE_POLICY_ID
    if baseline == "coevoskills" and args.benchmark == "wildclawbench" and args.tasks_mode == "single":
        run_config["task_sample_policy_id"] = SKILLLIFTSKILLS_WILDCLAW_TASK_SAMPLE_POLICY_ID
    if baseline == "human_skill" and args.benchmark == "wildclawbench":
        human_skills_root = root / "skilllift_eval" / "human_skills" / "wildclawbench"
        human_source_hash = human_skill_source_hash(human_skills_root)
        run_config["task_sample_policy_id"] = (
            HUMAN_SKILL_SINGLE_TASK_SAMPLE_POLICY_ID
            if args.tasks_mode == "single"
            else HUMAN_SKILL_FULL_TASK_SAMPLE_POLICY_ID
        )
        run_config["human_skill_source"] = {
            "root": str(human_skills_root.resolve()),
            "source_hash": human_source_hash,
            "inventory_count": len(
                validate_human_skill_inventory(root / "WildClawBench", human_skills_root)
            ),
        }
    run_config["run_config_hash"] = stable_hash(run_config)
    run_root = Path(args.run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    write_json(run_root / "run_config.json", run_config)
    if args.dry_run:
        print(f"wrote standalone dry-run config for {baseline} to {run_root}")
        return 0
    if baseline == "human_skill" and args.benchmark == "wildclawbench":
        if args.tasks_mode == "resume":
            raise ValueError("human_skill WildClawBench runner supports single or full mode")
        human_skills_root = root / "skilllift_eval" / "human_skills" / "wildclawbench"
        source_hash = run_config["human_skill_source"]["source_hash"]
        result = HumanSkillWildClawRunner().run(
            WildClawRunSettings(
                run_root=run_root,
                wildclaw_root=root / "WildClawBench",
                model_endpoint=endpoint,
                matrix_cell_id=key.matrix_cell_id,
                model_label=args.model,
                endpoint_config_hash=endpoint_config_hash(endpoint),
                algorithm_param_hash=profile.param_hash or "",
                benchmark_source_hash=stable_hash(
                    {
                        "wildclaw": wildclaw_source_hash(root),
                        "human_skill_source": source_hash,
                    }
                ),
                task_set_hash=stable_hash(
                    {
                        "benchmark": "wildclawbench",
                        "tasks_mode": args.tasks_mode,
                        "task_filter": args.tasks_filter,
                        "human_skill_source": source_hash,
                    }
                ),
                run_config_hash=run_config["run_config_hash"],
                evaluation_mode=args.evaluation_mode,
                context_budget_profile=args.context_budget_profile,
                visible_feedback_policy_id=budget["visible_feedback_policy_id"],
                oracle_call_budget=budget["oracle_call_budget"],
                task_sample_policy_id=run_config["task_sample_policy_id"],
                round_budget=budget["round_budget"],
                token_budget=budget["token_budget"],
                tasks_mode=args.tasks_mode,
                task_filter=args.tasks_filter,
                baseline="human_skill",
                rate_limit_retries=max(0, endpoint.max_retries),
            ),
            human_skills_root,
        )
        write_json(
            run_root / "task_run_records.json",
            [record.to_dict() for record in result.records],
        )
        write_json(run_root / "cell_summary.json", result.summary | {"resume": result.resume})
        print(
            f"ran human_skill wildclawbench {args.model}: "
            f"{result.summary['status']} "
            f"{result.summary['succeeded_count']}/{result.summary['expected_count']} tasks"
        )
        return 0
    if baseline == "skilllift" and args.benchmark == "wildclawbench":
        if args.tasks_mode != "single":
            raise ValueError("Task 4 CoEvo WildClawBench smoke must use --tasks.mode single")
        result = SkillLiftWildClawRunner().run(
            SkillLiftWildClawRunSettings(
                run_root=run_root,
                project_root=root,
                skilllift_root=root / "skilllift",
                wildclaw_root=root / "WildClawBench",
                model_endpoint=endpoint,
                matrix_cell_id=key.matrix_cell_id,
                model_label=args.model,
                endpoint_config_hash=endpoint_config_hash(endpoint),
                algorithm_param_hash=profile.param_hash or "",
                benchmark_source_hash=wildclaw_source_hash(root),
                task_set_hash=stable_hash({"benchmark": "wildclawbench", "tasks_mode": args.tasks_mode, "task_filter": args.tasks_filter}),
                run_config_hash=run_config["run_config_hash"],
                evaluation_mode=args.evaluation_mode,
                context_budget_profile=args.context_budget_profile,
                visible_feedback_policy_id=budget["visible_feedback_policy_id"],
                oracle_call_budget=budget["oracle_call_budget"],
                round_budget=budget["round_budget"],
                token_budget=budget["token_budget"],
                tasks_mode=args.tasks_mode,
                task_filter=args.tasks_filter or "",
                algorithm_params=profile.resolved_params,
            )
        )
        write_json(run_root / "task_run_records.json", [record.to_dict() for record in result.records])
        write_json(run_root / "cell_summary.json", result.summary | {"resume": result.resume})
        print(
            f"ran skilllift wildclawbench {args.model}: "
            f"{result.summary['status']} {result.summary['succeeded_count']}/{result.summary['expected_count']} tasks"
        )
        return 0
    if baseline == "coevoskills" and args.benchmark == "wildclawbench":
        if args.tasks_mode != "single":
            raise ValueError("CoEvoSkills WildClawBench runner requires --tasks.mode single")
        load_env_files(root)
        result = CoEvoSkillsWildClawRunner().run(
            CoEvoSkillsWildClawRunSettings(
                run_root=run_root,
                project_root=root,
                skilllift_root=root / "skilllift",
                wildclaw_root=root / "WildClawBench",
                model_endpoint=endpoint,
                matrix_cell_id=key.matrix_cell_id,
                model_label=args.model,
                endpoint_config_hash=endpoint_config_hash(endpoint),
                algorithm_param_hash=profile.param_hash or "",
                benchmark_source_hash=wildclaw_source_hash(root),
                task_set_hash=stable_hash(
                    {
                        "benchmark": "wildclawbench",
                        "tasks_mode": args.tasks_mode,
                        "task_filter": args.tasks_filter,
                    }
                ),
                run_config_hash=run_config["run_config_hash"],
                evaluation_mode=args.evaluation_mode,
                context_budget_profile=args.context_budget_profile,
                visible_feedback_policy_id=budget["visible_feedback_policy_id"],
                oracle_call_budget=budget["oracle_call_budget"],
                round_budget=budget["round_budget"],
                token_budget=budget["token_budget"],
                tasks_mode=args.tasks_mode,
                task_filter=args.tasks_filter or "",
                algorithm_params=profile.resolved_params,
            )
        )
        write_json(
            run_root / "task_run_records.json",
            [record.to_dict() for record in result.records],
        )
        write_json(
            run_root / "artifact_rollout_records.json",
            [record.to_dict() for record in result.artifact_records],
        )
        write_json(run_root / "cell_summary.json", result.summary | {"resume": result.resume})
        print(
            f"ran coevoskills wildclawbench {args.model}: "
            f"{result.summary['status']} "
            f"best_score={result.summary['best_score']:.4f} "
            f"evaluation_mean={result.summary['evaluation_mean_score']:.4f} "
            f"pass_rate={result.summary['pass_rate']:.4f}"
        )
        return 0
    if baseline != "no_skill" or args.benchmark != "tau2":
        raise SystemExit("Phase 3 real runs are implemented only for no_skill tau2")
    result = Tau2Runner().run(
        Tau2RunSettings(
            run_root=run_root,
            model_endpoint=endpoint,
            matrix_cell_id=key.matrix_cell_id,
            model_label=args.model,
            endpoint_config_hash=endpoint_config_hash(endpoint),
            algorithm_param_hash=profile.param_hash or "",
            benchmark_source_hash=tau2_source_hash(root),
            task_set_hash=stable_hash({"benchmark": "tau2", "domains": tau2_domains}),
            run_config_hash=run_config["run_config_hash"],
            evaluation_mode=args.evaluation_mode,
            context_budget_profile=args.context_budget_profile,
            visible_feedback_policy_id=budget["visible_feedback_policy_id"],
            oracle_call_budget=budget["oracle_call_budget"],
            task_sample_policy_id=budget["task_sample_policy_id"],
            round_budget=budget["round_budget"],
            token_budget=budget["token_budget"],
            domains=tuple(tau2_domains or TAU2_ALLOWED_DOMAINS),
            split=tau2_split or TAU2_DEFAULT_SPLIT,
            tasks_mode=args.tasks_mode,
            task_filter=args.tasks_filter,
            prompt_hash=EMPTY_TAU2_PROMPT_HASH,
        )
    )
    write_json(run_root / "task_run_records.json", [record.to_dict() for record in result.records])
    write_json(run_root / "cell_summary.json", result.summary | {"resume": result.resume})
    print(f"ran no_skill tau2 {args.model}: {result.summary['status']} {result.summary['succeeded_count']}/{result.summary['expected_count']} tasks")
    return 0


def resolve_run_profile(root: Path, baseline: str, args: Any) -> AlgorithmParamProfile:
    if baseline != "no_skill":
        profile = load_algorithm_profile(root, baseline, args.param_profile)
        return apply_algorithm_override(
            profile,
            Path(args.algorithm_override) if args.algorithm_override else None,
            args.algo_param or [],
        )
    if args.algorithm_override or args.algo_param:
        raise ValueError("no_skill native cells must not use algorithm overrides")
    return AlgorithmParamProfile(
        baseline="no_skill",
        profile_id=args.param_profile,
        paper_reference="not_applicable",
        paper_default_params={},
        resolved_params={},
    )


def load_env_files(root: Path) -> None:
    for path in [root / ".env", root / "tau2-bench" / ".env"]:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def tau2_source_hash(root: Path) -> str:
    rels = [
        "tau2-bench/src/tau2/run.py",
        "tau2-bench/src/tau2/runner/helpers.py",
        "tau2-bench/src/tau2/data_model/simulation.py",
        "tau2-bench/src/tau2/domains/airline/environment.py",
        "tau2-bench/src/tau2/domains/retail/environment.py",
        "tau2-bench/src/tau2/domains/telecom/environment.py",
    ]
    files = {}
    for rel in rels:
        path = root / rel
        files[rel] = stable_hash(path.read_text(encoding="utf-8")) if path.exists() else "missing"
    return stable_hash({"runner": TAU2_RUNNER_VERSION, "files": files})


def wildclaw_source_hash(root: Path) -> str:
    rels = [
        "skilllift_eval/runners/wildclaw.py",
        "skilllift_eval/runners/wildclaw_engine/run_batch.py",
        "skilllift_eval/runners/wildclaw_engine/task_parser.py",
        "skilllift_eval/skills/wildclaw_loader.py",
        "WildClawBench/src/utils/task_parser.py",
        "WildClawBench/src/agents/openclaw/runner.py",
    ]
    files = {}
    for rel in rels:
        path = root / rel
        files[rel] = stable_hash(path.read_text(encoding="utf-8")) if path.exists() else "missing"
    return stable_hash({"runner": "wildclaw_runner_v1", "files": files})


def build_skilllift_task4_smoke_bundle(root: Path, task_filter: str) -> SkillBundle:
    skilllift_root = root / "skilllift"
    skilllift_path = str(skilllift_root)
    if skilllift_path not in sys.path:
        sys.path.insert(0, skilllift_path)
    from skilllift.adapters.wildclawbench import evo_skill_to_skill_bundle_dict
    from skilllift.schemas import EvoSkill, SkillKey

    skill = EvoSkill(
        key=SkillKey(0, 1),
        skill_name="skilllift-task4-06-01-smoke",
        files={
            "SKILL.md": (
                "---\n"
                "name: skilllift-task4-06-01-smoke\n"
                "description: Task4 smoke skill for WildClawBench 06-01.\n"
                "---\n\n"
                "# CoEvo Task4 06-01 Smoke Skill\n\n"
                "Before solving the task, preserve any existing `summary.md` file. "
                "Write the MAE paper summary to a different `*summary*.md` filename, "
                "and include the exact marker `SKILLLIFT_SKILL_LOADED` in that new summary file.\n"
            ),
            "executor.py": "def noop():\n    return None\n",
        },
        entrypoint="executor.py",
        metadata={"source": "task4_smoke_static_evo_skill", "task_filter": task_filter},
    )
    data = evo_skill_to_skill_bundle_dict(
        skill,
        task_id=task_filter.replace("—", "-").replace("–", "-") or "06-01",
        created_at=datetime.now(timezone.utc).isoformat(),
        source_artifact_path=str(skilllift_root / "skilllift"),
    )
    data["skill_bundle_id"] = "skilllift-task4-06-01-smoke"
    return SkillBundle.from_dict(data)


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="skilllift_eval")
    subparsers = parser.add_subparsers(dest="command", required=True)

    matrix = subparsers.add_parser("matrix")
    matrix.add_argument("--dry-run", action="store_true")
    matrix.add_argument("--evaluation-mode", choices=EVALUATION_MODES, required=True)
    matrix.add_argument("--context-budget-profile", default=None)
    matrix.add_argument("--config", required=True)
    matrix.add_argument("--run-root", default="runs/task_0_1_dry_run")

    run = subparsers.add_parser("run")
    run.add_argument("baseline")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--benchmark", choices=BENCHMARKS, required=True)
    run.add_argument("--model", required=True, help="Key under model_endpoints in --config.")
    run.add_argument("--config", required=True)
    run.add_argument("--run-root", default="runs/task_1_x/run_dry_run")
    run.add_argument("--param-profile", default="paper_default")
    run.add_argument("--algo-param", action="append", default=[])
    run.add_argument("--algorithm-override", default=None)
    run.add_argument("--evaluation-mode", choices=EVALUATION_MODES, default="native_end_to_end")
    run.add_argument("--context-budget-profile", default=None)
    run.add_argument("--tasks.mode", dest="tasks_mode", choices=["single", "full", "resume"], required=True)
    run.add_argument("--tasks.filter", dest="tasks_filter", default=None)
    run.add_argument("--tasks.checkpoint", dest="tasks_checkpoint", default=None)
    run.add_argument("--tau2.domains", dest="tau2_domains", default=None)
    run.add_argument("--tau2.split", dest="tau2_split", default=None)

    audit = subparsers.add_parser("audit")
    audit.add_argument("run_root")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "matrix":
            if args.context_budget_profile is None:
                args.context_budget_profile = "matched_v1" if args.evaluation_mode == "budget_matched" else "native_default"
            return handle_matrix(args)
        if args.command == "run":
            if args.context_budget_profile is None:
                args.context_budget_profile = "matched_v1" if args.evaluation_mode == "budget_matched" else "native_default"
            return handle_run(args)
        if args.command == "audit":
            return handle_audit(args)
    except ValueError as exc:
        parser.exit(2, f"error: {exc}\n")
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
