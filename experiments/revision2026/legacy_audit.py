"""Read selected metadata in the supplied archive; never execute experiments.

Only allowlisted scalar fields are exported. Large trajectories and credential
configuration contents are not read. The original archive is never modified.
"""
from pathlib import Path
import collections
import csv
import hashlib
import json
import os
import re
import statistics

import argparse
parser = argparse.ArgumentParser(description="Audit legacy archives without executing experiments or inventing statistics")
parser.add_argument("--archive", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
SRC = args.archive.resolve()
ROOT = SRC.parent
OUT = args.output.resolve()
OUT.mkdir(parents=True, exist_ok=True)
if not SRC.is_dir():
    raise SystemExit(f"Archive directory does not exist: {SRC}")
LIMIT = 8 * 1024 * 1024
read_files = {}
issues = []


def read(path):
    if not path.exists():
        return None
    if path.stat().st_size > LIMIT:
        issues.append({"path": str(path.relative_to(ROOT)), "issue": "metadata_size_limit"})
        return None
    raw = path.read_bytes()
    read_files[str(path.relative_to(ROOT))] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        issues.append({"path": str(path.relative_to(ROOT)), "issue": "invalid_json"})
        return None


def save(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def csvsave(name, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with (OUT / name).open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()})


def relocate(value):
    if not value:
        return None
    for part in ["/runs/skillsbench/", "/agentclawbench_baseline/"]:
        if part in value:
            return SRC / part.strip("/") / value.split(part, 1)[1]
    return None


inventory = collections.defaultdict(collections.Counter)
for base, dirs, files in os.walk(SRC):
    for name in files:
        path = Path(base) / name
        branch = path.relative_to(SRC).parts[0]
        inventory[branch]["files"] += 1
        inventory[branch]["bytes"] += path.stat().st_size
        if name in {"summary.json", "state.json", "result.json", "trial_result.json", "score.json", "run_config.json", "checkpoint.json"}:
            inventory[branch][name] += 1
save("inventory.json", dict(inventory))

observations, task_rows, group_rows, missing = [], [], [], []
for group in sorted((SRC / "runs/skillsbench").iterdir()):
    if not group.is_dir():
        continue
    group_tasks, group_models = [], set()
    for summary_path in sorted(group.glob("*/summary.json")):
        domain_root = summary_path.parent
        manifest = read(domain_root / "run_manifest.json")
        summary = read(summary_path)
        rows = summary.get("tasks", [])
        assigned = set(manifest["task_ids"])
        observed = {r["task_id"] for r in rows}
        missing.extend({"group": group.name, "domain": domain_root.name, "task_id": t, "reason": "not_in_summary"} for t in sorted(assigned - observed))
        for row in rows:
            task_id = row["task_id"]
            task_root = domain_root / "tasks" / task_id
            state = read(task_root / "state.json") or {}
            final = row.get("final_rewards", [])
            record = {"group": group.name, "domain": domain_root.name, "task_id": task_id,
                      "cohort": row.get("cohort"), "stop_reason": row.get("stop_reason"),
                      "summary_status": summary.get("status"), "state_status": state.get("status"),
                      "winner_hash": row.get("winner_hash"), "adaptation_reward": row.get("adaptation_reward"),
                      "stored_final_score": row.get("final_score"), "final_rewards": final,
                      "final_n": len(final), "final_mean": statistics.mean(final) if final else None,
                      "source": str(summary_path.relative_to(ROOT)), "source_pointer": f"tasks[task_id={task_id}]"}
            record["state_file_present"] = (task_root / "state.json").exists()
            record["seed_manifest_present"] = (task_root / "seed.json").exists()
            record["final_portfolio_directory_present"] = (task_root / "final_portfolio").is_dir()
            record["matching_final_deployment_directories"] = 0
            verified = 0
            refs_by_marker = {}
            for refpath in sorted(task_root.glob("final/trial-*/result_ref.json")):
                ref = read(refpath) or {}
                ev = ref.get("evaluation", {})
                markerpath = relocate(ev.get("artifact_ref"))
                if markerpath:
                    refs_by_marker[markerpath] = (refpath, ev)
            for markerpath in sorted((domain_root / "adapter_artifacts" / task_id).glob("final*/trial_result.json")):
                refpath, ev = refs_by_marker.get(markerpath, (None, {}))
                marker = read(markerpath)
                marker = marker or {}
                rawpath = relocate(marker.get("result_path"))
                raw = read(rawpath) if rawpath else None
                raw = raw or {}
                reward = (raw.get("rewards") or {}).get("reward")
                model = raw.get("model")
                if model:
                    group_models.add(model)
                checks = {"raw_task_matches": raw.get("task_name") == task_id,
                          "reward_matches": reward is not None and reward == marker.get("reward") and (not ev or reward == ev.get("reward")),
                          "winner_matches": marker.get("portfolio_hash") == row.get("winner_hash"),
                          "phase_final": str(marker.get("trial_id", "")).startswith("final-")}
                if not all(checks.values()):
                    issues.append({"path": str(markerpath.relative_to(ROOT)), "issue": "raw_chain_mismatch", "checks": checks})
                verified += int(all(checks.values()))
                record["matching_final_deployment_directories"] += int(checks["winner_matches"] and (markerpath.parent / "deployment").is_dir())
                observations.append({"benchmark": "skillsbench", "method": "SkillLift_task_portfolio",
                                     "group": group.name, "model": model, "domain": domain_root.name,
                                     "task_id": task_id, "phase": "final", "trial_id": marker.get("trial_id"),
                                     "logical_slot": refpath.parent.name if refpath else None, "reward": reward,
                                     "winner_hash": row.get("winner_hash"), "started_at": raw.get("started_at"),
                                     "finished_at": raw.get("finished_at"), "rollout_seed": None,
                                     "evolution_seed": None, "evolution_run_id": None,
                                     "chain_checks": checks, "raw_error_present": bool(raw.get("error")),
                                     "logical_fingerprint_prefix": re.sub(r"-a\d+$", "", str(marker.get("trial_id", ""))),
                                     "physical_attempt": int(str(marker.get("trial_id")).rsplit("-a", 1)[1]) if re.search(r"-a\d+$", str(marker.get("trial_id"))) else None,
                                     "raw_source": str(rawpath.relative_to(ROOT)) if rawpath and rawpath.exists() else None,
                                     "raw_sha256": read_files.get(str(rawpath.relative_to(ROOT)), {}).get("sha256") if rawpath and rawpath.exists() else None,
                                     "reference_source": str(refpath.relative_to(ROOT)) if refpath else None,
                                     "marker_source": str(markerpath.relative_to(ROOT))})
            record["raw_chain_matched_records"] = verified
            if not final:
                missing.append({"group": group.name, "domain": domain_root.name, "task_id": task_id, "reason": "no_independent_final", "cohort": row.get("cohort")})
            elif len(final) < 2:
                missing.append({"group": group.name, "domain": domain_root.name, "task_id": task_id, "reason": "one_final_only"})
            if verified != len(final):
                issues.append({"path": str(task_root.relative_to(ROOT)), "issue": "summary_final_count_vs_verified_chain", "summary_n": len(final), "verified_n": verified})
            task_rows.append(record)
            group_tasks.append(record)
    final_tasks = [r for r in group_tasks if r["final_n"]]
    mixed = [r["stored_final_score"] if r["stored_final_score"] is not None else r["adaptation_reward"] for r in group_tasks]
    mixed = [value for value in mixed if value is not None]
    group_rows.append({"group": group.name, "actual_models_in_final_raw_results": sorted(group_models),
                       "summary_task_count": len(group_tasks), "expected_task_count": 87,
                       "cohorts": dict(collections.Counter(r["cohort"] for r in group_tasks)),
                       "final_count_distribution": dict(collections.Counter(r["final_n"] for r in group_tasks)),
                       "final_task_count": len(final_tasks), "final_trial_count": sum(r["final_n"] for r in group_tasks),
                       "raw_chain_matched_records_not_independent_n": sum(r["raw_chain_matched_records"] for r in group_tasks),
                       "conditional_final_subset_mean_percent": 100 * statistics.mean(r["final_mean"] for r in final_tasks) if final_tasks else None,
                       "diagnostic_mixed_best_or_anchor_count": len(mixed),
                       "diagnostic_mixed_best_or_anchor_mean_percent": 100 * statistics.mean(mixed) if mixed else None,
                       "tasks_where_final_max_differs_from_mean": sum(r["final_mean"] is not None and r["final_mean"] != r["stored_final_score"] for r in group_tasks),
                       "table2_validated": False, "inferential_statistics": "withheld: outcome-dependent missing final tasks and no matched baseline cohort"})

attempt_groups = collections.defaultdict(list)
for row in observations:
    attempt_groups[(row["group"], row["task_id"], row["logical_fingerprint_prefix"])].append(row)
attempt_map = []
for (group, task_id, fingerprint), rows in sorted(attempt_groups.items()):
    selected = [r for r in rows if r["reference_source"]]
    disposition = "one_result_ref_selected" if len(selected) == 1 else "multiple_result_refs_need_review" if len(selected) > 1 else "no_result_ref_selection_unproven"
    attempt_map.append({"group": group, "task_id": task_id, "logical_fingerprint_prefix": fingerprint,
                        "physical_records": len(rows), "trial_ids": [r["trial_id"] for r in rows],
                        "rewards": [r["reward"] for r in rows], "result_ref_selected_trial_ids": [r["trial_id"] for r in selected],
                        "disposition": disposition, "independent_final_evaluation_proven": False})

recovery = []
for group in sorted((SRC / "runs/skillsbench").iterdir()):
    if not group.is_dir():
        continue
    for manifest_path in sorted(group.glob("*/run_manifest.json")):
        domain_root = manifest_path.parent
        manifest = read(manifest_path)
        for task_id in manifest["task_ids"]:
            task_root = domain_root / "tasks" / task_id
            state = read(task_root / "state.json") or {}
            matching = [r for r in task_rows if r["group"] == group.name and r["task_id"] == task_id]
            row = matching[0] if matching else {}
            markers = list((domain_root / "adapter_artifacts" / task_id).glob("final*/trial_result.json"))
            recovery.append({"group": group.name, "domain": domain_root.name, "task_id": task_id,
                             "in_summary": bool(matching), "task_directory_present": task_root.is_dir(),
                             "state_file_present": bool(state), "state_status": state.get("status"),
                             "seed_manifest_present": (task_root / "seed.json").exists(),
                             "final_portfolio_directory_present": (task_root / "final_portfolio").is_dir(),
                             "summary_final_n": row.get("final_n", 0), "raw_final_marker_count_not_independent_n": len(markers),
                             "slots_missing_from_summary_for_archived_two_trial_protocol": max(0, 2-row.get("final_n", 0)),
                             "raw_reconciliation_needed_before_rerun": len(markers) != row.get("final_n", 0),
                             "portfolio_content_hash_recomputed": False})

legacy = []
for branch in ["taskoutput-gpt5.4", "taskoutput-opus4.7"]:
    rows = []
    for path in sorted((SRC / branch).glob("[0-9][0-9]-[0-9][0-9]/coevo/summary.json")):
        data, config = read(path), read(path.parent / "config.json")
        rows.append({"branch": branch, "task_code": path.parent.parent.name,
                     "model_in_config": (config or {}).get("model"), "best_oracle_score": data.get("best_oracle_score"),
                     "stop_reason": data.get("stop_reason"), "phase": "selection_best",
                     "source": str(path.relative_to(ROOT)), "final_evaluation_proven": False})
    legacy.extend(rows)

baseline_rows = []
for path in sorted((SRC / "agentclawbench_baseline").glob("*/summary*.json")):
    data = read(path)
    for row in data if isinstance(data, list) else data.get("results", []):
        output = relocate(row.get("output_dir"))
        scores = row.get("scores") or {}
        baseline_rows.append({"branch": "agentclawbench_baseline", "task_id": output.parent.name if output else row.get("task_id"),
                              "trial_id": row.get("task_id"), "overall_score": scores.get("overall_score"),
                              "error_present": bool(row.get("error")), "raw_score_exists": bool(output and (output / "score.json").exists()),
                              "method_identity": "unverified static skill condition", "source": str(path.relative_to(ROOT))})

baseline_raw = []
for path in sorted((SRC / "agentclawbench_baseline").glob("*/*/*/score.json")):
    data = read(path) or {}
    scores = data.get("scores", data)
    baseline_raw.append({"task_id": path.parent.parent.name, "trial_folder": path.parent.name,
                         "overall_score": scores.get("overall_score"), "source": str(path.relative_to(ROOT)),
                         "method_identity": "not established from score file", "model_identity": "not established from score file"})

budget = []
for branch in ["skillopt-glm5.1-budget", "coevoskills-glm5.1-budget"]:
    for path in sorted((SRC / "runs" / branch).glob("*/run_config.json")):
        data = read(path)
        parent = path.parent
        budget.append({"branch": branch, "task_code": parent.name, "method": data.get("baseline"),
                       "provider_model_id": data.get("provider_model_id"), "benchmark": data.get("benchmark"),
                       "checkpoint_count": len(list(parent.glob("*/checkpoint.json"))),
                       "summary_count": len(list(parent.glob("**/summary.json"))),
                       "final_named_directory_count": len([p for p in parent.glob("**/*") if p.is_dir() and p.name.startswith("final")]),
                       "source": str(path.relative_to(ROOT))})

csvsave("observations_final.csv", observations)
csvsave("logical_attempt_map.csv", attempt_map)
csvsave("tasks_skillsbench.csv", task_rows)
csvsave("task_recovery.csv", recovery)
csvsave("missing_final_or_tasks.csv", missing)
csvsave("legacy_selection_scores.csv", legacy)
csvsave("wildclaw_static_summary_rows.csv", baseline_rows)
csvsave("wildclaw_static_raw_scores.csv", baseline_raw)
csvsave("budget_run_coverage.csv", budget)
save("groups.json", group_rows)
save("issues.json", issues)
save("read_metadata_manifest.json", read_files)
save("audit_summary.json", {"source": str(SRC), "zip_read": False, "metadata_files_read": len(read_files),
    "skillsbench_groups": group_rows, "legacy_selection_rows": len(legacy),
    "static_summary_rows": len(baseline_rows), "budget_configs": len(budget),
    "paired_p_values": None, "confidence_intervals": None,
    "statistics_reason": "Do not attach inference to a selectively observed final subset or relabel selection-best as held-out performance.",
    "complete_evolution_replicates_proven": 0})

# Read the historical presentation constants without importing/executing plotting.
published = json.loads((Path(__file__).parent / "published_table2.json").read_text())
readiness = []
model_groups = {"GPT-5.4-mini": "gpt", "GLM-5.1": "glm", "DeepSeek-V4-Pro": "deepseek"}
for benchmark, models in published["main_results"].items():
    for model, values in models.items():
        for method, mean in zip(published["methods"], values):
            cell = {"benchmark": benchmark, "published_model": model, "method": method,
                    "published_mean_percent": mean,
                    "published_source": "SkillLift.pdf p5 Table2; outputs/concurrent-preprints-2026-10-25/sources/skilllift.txt:274-303",
                    "constant_source": "scripts/plot_readme_figures.py:22-46",
                    "ci95_lower": None, "ci95_upper": None, "paired_difference": None,
                    "p_raw": None, "p_holm": None, "status": "not_reconstructable_yet",
                    "matched_historical_final_n_proven": None, "candidate_raw_final_records": None,
                    "candidate_summary_final_count": None, "candidate_final_portfolio_directories": None,
                    "portfolio_hash_recomputed": False,
                    "missing_reason": "No attributable complete per-task final records and historical skill/run identity for this cell; cannot infer variance from published mean."}
            if benchmark == "SkillsBench" and "SkillLift" in method:
                group = model_groups[model]
                evidence = next(r for r in group_rows if r["group"] == group)
                rec = [r for r in recovery if r["group"] == group]
                cell.update(candidate_raw_final_records=evidence["raw_chain_matched_records_not_independent_n"],
                            candidate_summary_final_count=evidence["final_trial_count"],
                            candidate_final_portfolio_directories=sum(r["final_portfolio_directory_present"] for r in rec),
                            missing_reason="Candidate archive has outcome-dependent missing finals, two-trial/max protocol, unresolved historical aggregation/independence; same-model baseline finals absent. Raw count includes physical attempts, not independent n.")
            elif benchmark == "WildClawBench" and model == "GLM-5.1" and method in ["+ SkillOpt", "+ CoEvoSkills"]:
                cell["missing_reason"] = "GLM budget configs exist (SkillOpt 54 / CoEvoSkills 53); only 1 / 3 checkpoints and no located final summaries. Configs do not establish finished evaluation."
            elif benchmark == "WildClawBench" and model == "GPT-5.4" and "SkillLift" in method:
                cell["missing_reason"] = "60 taskoutput-gpt5.4 selection-best scores exist; mean 70.0938 differs from published 68.4; exact method/version mapping and independent final evaluation unproven."
            elif benchmark == "WildClawBench" and model == "GPT-5.4" and method in ["No skill", "+ Human-written"]:
                cell["missing_reason"] = "58 static raw score files exist, but no-skill versus human-skill identity, full 60-task denominator and historical run mapping are unproven; cannot assign these files to this row."
            readiness.append(cell)
csvsave("table2_statistics_readiness.csv", readiness)
print(json.dumps({"metadata_files_read": len(read_files), "groups": group_rows,
                  "legacy_rows": len(legacy), "static_rows": len(baseline_rows), "budget_configs": len(budget), "issues": len(issues)}, ensure_ascii=False, indent=2))
