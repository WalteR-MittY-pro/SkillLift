"""Read-only audit of the separately supplied GLM WildClaw verification bundle."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
from pathlib import Path
from statistics import mean

from .common import read_json, write_json


def scoring_errors(scores):
    # trap_errors is a scored task criterion, not a judge infrastructure failure.
    fields = [key for key in ("error", "judge_error", "llm_judge_error", "llm_error") if scores.get(key)]
    if scores.get("judge_method") == "failed":
        fields.append("judge_method")
    return fields


def audit(archive, output):
    archive, output = Path(archive).resolve(), Path(output).resolve()
    if output == archive or archive in output.parents:
        raise ValueError("Write audit output outside the original log bundle")
    manifest = read_json(archive / "batch_summaries/main.batch_manifest.json")
    expected = set(manifest["task_ids"])
    aggregation = read_json(archive / "final_aggregation.json")
    if len(expected) != 60 or {row["task_id"] for row in aggregation["tasks"]} != expected:
        raise ValueError("The fixed 60-task manifest and aggregation do not agree")
    fingerprints = {name: hashlib.sha256((archive / name).read_bytes()).hexdigest()
                    for name in ("batch_summaries/main.batch_manifest.json", "final_aggregation.json")}

    def load(path):
        content = path.read_bytes()
        fingerprints[path.relative_to(archive).as_posix()] = hashlib.sha256(content).hexdigest()
        return read_json(path)

    cells, configs, raw = {}, [], defaultdict(list)
    for path in sorted((archive / "cells").rglob("run_config.json")):
        configs.append(load(path))
    for path in sorted((archive / "cells").rglob("cell_summary.json")):
        cells[(path.parent.parent.name, path.parent.name)] = load(path)
    score_rows = []
    for path in sorted((archive / "rollouts").rglob("score.json")):
        parts = path.relative_to(archive / "rollouts").parts
        line, task, trial = parts[0], parts[2], parts[3]
        payload = load(path)
        row = {"line": line, "task_id": task, "trial": trial, "phase": trial.split("-", 1)[0],
               "reward": payload.get("overall_score"), "scoring_errors": ";".join(scoring_errors(payload)),
               "source": path.relative_to(archive).as_posix()}
        score_rows.append(row)
        if row["phase"] == "final":
            raw[(line, task)].append(row)
    canonical, missing, bad, ambiguous = [], [], [], []
    for task in sorted(expected):
        # Diagnostic ownership rule uses categories, never scores. It is an audit
        # convention, not proof that this was prospectively chosen by the runner.
        line = "main" if int(task[:2]) <= 3 else "c456"
        cell = cells.get((line, task), {})
        if cell.get("status") != "SUCCEEDED":
            for replacement in ("retry", "retry2"):
                candidate = cells.get((replacement, task), {})
                if candidate.get("status") == "SUCCEEDED":
                    line, cell = replacement, candidate
                    break
        finals = cell.get("final_rewards", [])
        records = raw[(line, task)]
        reasons = []
        if not finals:
            missing.append(task)
            reasons.append("missing_independent_final")
        errors = sorted({key for record in records for key in record["scoring_errors"].split(";") if key})
        if errors:
            bad.append(task)
            reasons.append("scorer_failure_in_final")
        if finals and Counter(record["reward"] for record in records) != Counter(finals):
            ambiguous.append(task)
            reasons.append("raw_retry_to_logical_final_mapping_required")
        canonical.append({"task_id": task, "line": line, "status": cell.get("status", "MISSING"),
                          "stop_reason": cell.get("stop_reason"), "winner_portfolio_hash": cell.get("winner_portfolio_hash"),
                          "final_rewards": finals, "final_mean": mean(finals) if finals else None,
                          "reported_mean_score": cell.get("mean_score"), "raw_final_score_files": len(records),
                          "scoring_errors": errors, "issues": reasons})
    final_cells = [cell for cell in cells.values() if cell.get("final_rewards")]
    raw_final = [row for row in score_rows if row["phase"] == "final"]
    represented = {task for _, task in cells}
    facts = {
        "expected_tasks": len(expected), "represented_tasks": len(represented), "cell_summaries": len(cells),
        "configurations": len(configs), "models": sorted({c["provider_model_id"] for c in configs}),
        "methods": sorted({c["baseline"] for c in configs}),
        "algorithm_parameter_hashes": sorted({c["algorithm_param_hash"] for c in configs}),
        "raw_score_files": len(score_rows), "raw_score_phases": dict(Counter(row["phase"] for row in score_rows)),
        "raw_final_files": len(raw_final), "tasks_with_any_final": len({row["task_id"] for row in raw_final}),
        "logical_final_cells": len(final_cells), "logical_final_values": sum(len(c["final_rewards"]) for c in final_cells),
        "raw_final_scorer_errors": sum(bool(row["scoring_errors"]) for row in raw_final),
        "raw_scorer_errors_by_phase": dict(Counter(row["phase"] for row in score_rows if row["scoring_errors"])),
        "tasks_with_any_phase_scorer_errors": sorted({row["task_id"] for row in score_rows if row["scoring_errors"]}),
        "canonical_final_tasks": sum(bool(c["final_rewards"]) for c in canonical),
        "canonical_anchor_only": sum(c["stop_reason"] == "anchor_terminal" for c in canonical),
        "canonical_failed": sum(c["status"] != "SUCCEEDED" for c in canonical),
        "canonical_missing_final_tasks": missing, "canonical_final_scorer_issue_tasks": bad,
        "canonical_ambiguous_raw_final_tasks": ambiguous,
        "canonical_tasks_requiring_final_work": sorted(set(missing + bad)),
        "archive_reported_A": aggregation["A_mean"], "archive_reported_B": aggregation["B_mean"],
        "canonical_rule": "main categories 1-3; c456 categories 4-6; replace unsuccessful cells only with successful retry/retry2; diagnostic, retrospective",
        "formal_full_benchmark_ci": None, "formal_between_method_p": None,
        "inference_status": "blocked: incomplete independent finals, scoring failures, missing method controls and frozen skill assets",
    }
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "audit.json", facts)
    write_json(output / "task_readiness.json", canonical)
    write_json(output / "input_sha256.json", fingerprints)
    for name, rows in [("raw_score_index.csv", score_rows), ("final_cell_statistics.csv", [
        {"line": line, "task_id": task, "winner_portfolio_hash": cell.get("winner_portfolio_hash"),
         "final_1": cell["final_rewards"][0], "final_2": cell["final_rewards"][1],
         "arithmetic_mean": mean(cell["final_rewards"]), "reported_mean_score": cell.get("mean_score"),
         "eligible_for_full_benchmark_inference": False}
        for (line, task), cell in sorted(cells.items()) if len(cell.get("final_rewards", [])) == 2])]:
        if rows:
            with (output / name).open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    return facts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.archive, args.output)
    print({key: result[key] for key in ("expected_tasks", "tasks_with_any_final", "logical_final_values", "raw_final_scorer_errors", "inference_status")})
