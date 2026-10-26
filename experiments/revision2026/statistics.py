"""Paired, family-clustered inference from complete independent evaluation cells."""
from __future__ import annotations

import csv
import itertools
import json
from pathlib import Path

import numpy as np

from .common import read_json, write_json
from .runner import evaluation_path, evaluation_identity


def holm(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    answer, previous = [None] * len(values), 0.0
    for rank, index in enumerate(order):
        previous = max(previous, min(1.0, values[index] * (len(values) - rank)))
        answer[index] = previous
    return answer


def paired_inference(rows_a, rows_b, settings):
    def index(rows):
        result = {}
        for row in rows:
            key = (row["family"], row["task_id"], row["evolution_run"], row["repeat"])
            if key in result:
                raise ValueError("Duplicate logical evaluation cell")
            if row["status"] != "OK" or row["phase"] not in {"heldout", "independent_final"}:
                raise ValueError("Only valid independent evaluations may enter inference")
            result[key] = float(row["reward"])
            if not np.isfinite(result[key]) or not 0 <= result[key] <= 1:
                raise ValueError("Invalid native reward")
        return result

    a, b = index(rows_a), index(rows_b)
    if not a or a.keys() != b.keys():
        raise ValueError("Paired method coverage differs")
    families, runs = sorted({k[0] for k in a}), sorted({k[2] for k in a})
    if len(families) < 2:
        raise ValueError("At least two independent task families are needed")
    sums_a = np.zeros((len(families), len(runs)))
    sums_b, counts = sums_a.copy(), sums_a.copy()
    for f, family in enumerate(families):
        for r, run in enumerate(runs):
            keys = [key for key in a if key[0] == family and key[2] == run]
            if not keys:
                raise ValueError("Incomplete family/run crossing")
            sums_a[f, r], sums_b[f, r], counts[f, r] = sum(a[k] for k in keys), sum(b[k] for k in keys), len(keys)
    rng = np.random.default_rng(settings["random_seed"])
    n = settings["bootstrap_repeats"]
    fi = rng.integers(len(families), size=(n, len(families)))
    ri = rng.integers(len(runs), size=(n, len(runs)))
    denominator = counts[fi[:, :, None], ri[:, None, :]].sum(axis=(1, 2))
    boot_a = sums_a[fi[:, :, None], ri[:, None, :]].sum(axis=(1, 2)) / denominator
    boot_b = sums_b[fi[:, :, None], ri[:, None, :]].sum(axis=(1, 2)) / denominator
    alpha = (1 - settings["confidence"]) / 2
    ci = lambda values: (100 * np.quantile(values, [alpha, 1 - alpha])).tolist()
    difference_by_family = (sums_a - sums_b).sum(axis=1) / counts.sum()
    observed = abs(difference_by_family.sum())
    if len(families) <= 16:
        signs = np.asarray(list(itertools.product([-1, 1], repeat=len(families))))
        p = float(np.mean(np.abs(signs @ difference_by_family) >= observed - 1e-12))
    else:
        signs = rng.choice([-1, 1], size=(settings["permutation_repeats"], len(families)))
        p = float((1 + np.sum(np.abs(signs @ difference_by_family) >= observed - 1e-12)) / (len(signs) + 1))
    return {"a_mean_percent": 100 * sums_a.sum() / counts.sum(), "b_mean_percent": 100 * sums_b.sum() / counts.sum(),
            "a_ci95": ci(boot_a), "b_ci95": ci(boot_b), "difference_pp": 100 * difference_by_family.sum(),
            "difference_ci95": ci(boot_a - boot_b), "p_raw": p, "families": len(families), "run_indices": len(runs),
            "observations": len(a), "ci_scope": "crossed resampling of task-family blocks and complete run indices; methods remain paired",
            "test_assumption": "exchangeability of method labels within task-family blocks under the null"}


def csv_write(path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with Path(path).open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in row.items()})


def summarize(root, settings):
    root = Path(root)
    expected, records, seen = {}, {}, set()
    manifest = read_json(root / "evaluation_manifest.json")
    mixed = mixed_conditions(root)
    from .common import digest
    if digest(manifest["evaluations"]) != manifest["plan_hash"]:
        raise ValueError("Evaluation plan hash mismatch")
    for spec in manifest["evaluations"]:
        if spec.get("phase") not in {"heldout", "independent_final"}:
            continue
        selected = evaluation_path(spec) / "selected.json"
        identity = digest(evaluation_identity(spec))
        if identity in seen:
            raise ValueError("Duplicate logical evaluation cell in sealed plan")
        seen.add(identity)
        group = (spec["benchmark"], spec["model"], spec["method"])
        expected[group] = expected.get(group, 0) + 1
        if selected.exists():
            row = read_json(selected)
            if row["config_hash"] != spec["config_hash"] or row.get("cell_identity") != evaluation_identity(spec):
                raise ValueError("Mismatched configuration or evaluation identity in report")
            if row["status"] == "OK":
                records.setdefault(group, []).append(row)
    if not expected:
        raise ValueError("No planned independent evaluation cells; pilot scores cannot become final statistics")
    tables, comparisons = [], []
    for (benchmark, model, method), count in sorted(expected.items()):
        rows = records.get((benchmark, model, method), [])
        table = {"benchmark": benchmark, "model": model, "method": method, "expected": count, "observed_valid": len(rows),
                 "status": "mixed_conditions" if mixed else "complete" if len(rows) == count else "incomplete", "mean_percent": None, "ci95": None,
                 "p_vs_skilllift": None, "p_holm": None}
        reference = records.get((benchmark, model, "skilllift"), [])
        if len(rows) == count and not mixed:
            own = paired_inference(rows, rows, settings)
            table.update(mean_percent=own["a_mean_percent"], ci95=own["a_ci95"], families=own["families"], run_indices=own["run_indices"])
        if method != "skilllift":
            comparison = {"benchmark": benchmark, "model": model, "baseline": method, "status": "mixed_conditions" if mixed else "missing_complete_pair"}
            if not mixed and len(rows) == count and len(reference) == expected.get((benchmark, model, "skilllift"), -1):
                result = paired_inference(reference, rows, settings)
                comparison.update(status="complete", **result)
                table["p_vs_skilllift"] = result["p_raw"]
            comparisons.append(comparison)
        tables.append(table)
    adjusted = holm([row.get("p_raw", 1.0) for row in comparisons])
    for comparison, value in zip(comparisons, adjusted):
        if comparison["status"] == "complete":
            comparison["p_holm"] = value
            for table in tables:
                if (table["benchmark"], table["model"], table["method"]) == (comparison["benchmark"], comparison["model"], comparison["baseline"]):
                    table["p_holm"] = value
    report = root / "report"
    report.mkdir(exist_ok=True)
    csv_write(report / "overall_statistics.csv", tables)
    csv_write(report / "paired_comparisons.csv", comparisons)
    write_json(report / "statistics.json", {"rows": tables, "comparisons": comparisons, "settings": settings,
        "note": "New independent evaluation estimates, not variances attached to historical published means. Missing comparisons count in the planned Holm family. Static methods repeat execution, not evolution."})
    return tables


def mixed_conditions(root):
    for name, key in (("run_manifest.json", "mixed_scientific_conditions"), ("evaluation_manifest.json", "mixed_conditions")):
        path = Path(root) / name
        if path.exists() and read_json(path).get(key):
            return True
    return False


def cost_report(root):
    root = Path(root)
    rows, native_seen = [], set()
    for path in sorted(root.glob("**/*usage.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            kind = event.get("kind")
            if kind == "skillopt_framework_usage":
                usage = event.get("summary", {}).get("_total", {})
            elif kind in {"framework_usage", "solver_usage", "vision_usage"}:
                usage = event.get("usage") or {}
            elif "usage_record_id" in event and "total_tokens" in event:
                usage = event
                identity = (str(path), event["usage_record_id"])
                if identity in native_seen:
                    continue
                native_seen.add(identity)
            else:
                continue
            rows.append({"source": str(path.relative_to(root)), "kind": kind or event.get("role", "native"),
                         "execution_model": event.get("response_model") or event.get("model"),
                         "recorded_at": event.get("recorded_at"), "total_tokens": usage.get("total_tokens"),
                         "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                         "cost_usd": usage.get("cost_usd", usage.get("cost")),
                         "usage_available": usage.get("total_tokens") is not None})
    for path in sorted(root.glob("**/task_run_record.json")):
        record = read_json(path)
        identity = record.get("raw_output_path", str(path))
        if identity in native_seen:
            continue
        native_seen.add(identity)
        tokens = record.get("token_actual")
        rows.append({"source": str(path.relative_to(root)), "kind": "native_oracle", "recorded_at": record.get("finished_at"),
                     "total_tokens": tokens if isinstance(tokens, (int, float)) else None,
                     "cost_usd": record.get("cost_actual") if isinstance(record.get("cost_actual"), (int, float)) else None,
                     "usage_available": isinstance(tokens, (int, float))})
    for path in sorted(root.glob("**/artifact_rollout_record.json")):
        record = read_json(path)
        identity = record.get("raw_output_path", str(path))
        if identity in native_seen:
            continue
        native_seen.add(identity)
        usage = record.get("usage") or {}
        rows.append({"source": str(path.relative_to(root)), "kind": "native_artifact_rollout",
                     "total_tokens": usage.get("total_tokens"), "cost_usd": usage.get("cost_usd", usage.get("cost")),
                     "usage_available": usage.get("total_tokens") is not None})
    groups = {}
    for row in rows:
        parts = Path(row["source"]).parts
        if parts[0] in {"training", "evaluations"} and len(parts) >= 4:
            group = (parts[1], parts[2], parts[3])
            phase = "training" if parts[0] == "training" else "final_evaluation"
        else:
            group, phase = ("shared", "writer", "initialization"), "initialization"
        row.update(benchmark=group[0], model=group[1], method=group[2], phase=phase)
        aggregate = groups.setdefault(group, {"benchmark": group[0], "model": group[1], "method": group[2],
            "reported_tokens": 0, "training_tokens": 0, "final_evaluation_tokens": 0,
            "framework_tokens": 0, "oracle_tokens": 0, "vision_tokens": 0, "vision_calls": 0, "usage_records": 0, "records_without_usage": 0,
            "reported_cost_usd": 0.0, "records_without_cost": 0})
        aggregate["usage_records"] += 1
        if row["kind"] == "vision_usage":
            aggregate["vision_calls"] += 1
        value = row.get("total_tokens")
        if value is None:
            aggregate["records_without_usage"] += 1
        else:
            aggregate["reported_tokens"] += value
            if phase in {"training", "final_evaluation"}:
                aggregate[phase + "_tokens"] += value
            role = "framework" if row["kind"] in {"framework_usage", "skillopt_framework_usage"} else "oracle"
            aggregate[role + "_tokens"] += value
            if row["kind"] == "vision_usage":
                aggregate["vision_tokens"] += value
        if isinstance(row.get("cost_usd"), (int, float)):
            aggregate["reported_cost_usd"] += row["cost_usd"]
        else:
            aggregate["records_without_cost"] += 1
    for group in groups.values():
        group["token_total_complete"] = group["records_without_usage"] == 0
        group["cost_total_complete"] = group["records_without_cost"] == 0
        if group["records_without_cost"] == group["usage_records"]:
            group["reported_cost_usd"] = None
    report = root / "report"
    report.mkdir(exist_ok=True)
    csv_write(report / "cost_records.csv", rows)
    csv_write(report / "cost_by_method.csv", list(groups.values()))
    summary = {"record_count": len(rows), "reported_tokens": sum(row["total_tokens"] for row in rows if row.get("total_tokens") is not None),
               "records_without_token_usage": sum(not row["usage_available"] for row in rows),
               "reported_cost_usd": sum(row["cost_usd"] for row in rows if isinstance(row.get("cost_usd"), (int, float))) if any(isinstance(row.get("cost_usd"), (int, float)) for row in rows) else None,
               "records_without_cost": sum(not isinstance(row.get("cost_usd"), (int, float)) for row in rows),
               "shared_gpt6sol_seed_generation_usage": "not exposed by the Codex subagent; not included in claimed totals",
               "scope": "All recorded attempts, including failed/interrupted training. Missing usage/cost is unknown, never zero. Reported tokens are a lower bound when usage is unavailable. Native surrogate-artifact rollouts are included; SkillOpt _total is counted once."}
    write_json(report / "cost_summary.json", summary)
    return summary


def process_best_report(root):
    """Table 2-style metric for the ablation: per-task best oracle score seen during evolution.

    The denominator is the full planned training roster; a missing or unfinished cell keeps the
    group incomplete (coverage only, no mean). Published references are listed beside
    the new rows and are never mixed into them."""
    from .runner import ablation_jobs, cell_path
    root = Path(root)
    manifest = read_json(root / "run_manifest.json")
    from .common import latest_protocol
    protocol = latest_protocol(manifest)
    mixed = mixed_conditions(root)
    rows, groups = [], {}
    # Rebuild the full training roster so a later `--stage test` plan cannot shrink it.
    for job in ablation_jobs(root, protocol, manifest["config_hash"], stage="train"):
        if job["method"] in {"no_skill", "human_written", "llm_written"}:
            continue
        marker = cell_path(root, job["benchmark"], job["model"], job["method"], job["run"], job["family"]) / "frozen.json"
        best = read_json(marker)["training"].get("process_best") if marker.exists() else None
        row = {"benchmark": job["benchmark"], "model": job["model"], "method": job["method"], "evolution_run": job["run"],
               "task_id": job["family"], "frozen": marker.exists(),
               "best_excluding_internal_final": (best or {}).get("best_excluding_internal_final"),
               "best_including_internal_final": (best or {}).get("best_including_internal_final")}
        rows.append(row)
        groups.setdefault((job["benchmark"], job["model"], job["method"]), []).append(row)
    summary = []
    for (benchmark, model, method), items in sorted(groups.items()):
        complete = all(item["best_including_internal_final"] is not None for item in items)
        entry = {"benchmark": benchmark, "model": model, "method": method, "source": "this run", "expected": len(items),
                 "observed": sum(item["best_including_internal_final"] is not None for item in items),
                 "status": "mixed_conditions" if mixed else "complete" if complete else "incomplete",
                 "mean_best_excluding_internal_final_percent": None, "mean_best_including_internal_final_percent": None}
        if complete and not mixed:
            for key in ("best_excluding_internal_final", "best_including_internal_final"):
                entry[f"mean_{key}_percent"] = 100 * float(np.mean([item[key] for item in items]))
        summary.append(entry)
    for model, methods in protocol["ablation"].get("published_reference", {}).items():
        for method, reference in methods.items():
            summary.append({"benchmark": reference["benchmark"], "model": model, "method": method, "source": reference["source"],
                            "expected": reference.get("tasks"), "observed": None, "status": "published",
                            "mean_best_excluding_internal_final_percent": reference["mean_percent"],
                            "mean_best_including_internal_final_percent": None,
                            "note": reference["metric"]})
    report = root / "report"
    report.mkdir(exist_ok=True)
    csv_write(report / "process_best_by_task.csv", rows)
    csv_write(report / "process_best_summary.csv", summary)
    write_json(report / "process_best_summary.json", {"rows": summary, "note":
        "Process-best uses the same training executions that selected the skill (the Table 2 definition); it is optimistic "
        "and is not an independent held-out estimate. Published rows are copied from the paper, not recomputed."})
    return summary
