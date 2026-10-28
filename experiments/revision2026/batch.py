"""Two experiments in shared per-model queues, with resumable task dependencies."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from itertools import zip_longest
from pathlib import Path
from threading import Event
import traceback

from .common import utc_now, write_json
from .concurrency import MODEL_LIMITS
from .runner import ablation_jobs, generalization_jobs, prepare_run, run_jobs, seal_evaluation_plan


def interleave_training(experiments):
    jobs = []
    for kind in ("write_seed", "train", "evaluate"):
        groups = [[job for job in group if job["kind"] == kind] for group in experiments]
        if kind == "train":
            # Freeze inexpensive static baselines immediately; otherwise alternating
            # them with slow ablations would delay generalization optimization.
            jobs.extend(job for group in groups for job in group if job["method"] in {"no_skill", "llm_written", "human_written"})
            groups = [[job for job in group if job["method"] not in {"no_skill", "llm_written", "human_written"}] for group in groups]
        jobs.extend(job for row in zip_longest(*groups) for job in row if job is not None)
    return jobs


def run_supplementary(root, protocol, *, glm_workers=2, deepseek_workers=2, plan_only=False):
    root = Path(root).resolve()
    workers = {"glm": glm_workers, "deepseek": deepseek_workers}
    for model in protocol["models"]:
        if model not in MODEL_LIMITS or not 1 <= workers[model] <= MODEL_LIMITS[model]:
            raise ValueError("GLM workers must be 1–2; DeepSeek workers must be 1–2")

    def plan(model, name, settings):
        folder = root / model / name
        effective, config_hash = prepare_run(folder, settings)
        jobs = (ablation_jobs(folder, effective, config_hash) if name == "ablation" else
                generalization_jobs(folder, effective, config_hash, pilot=name == "pilot"))
        write_json(folder / f"{name}_plan.json", {"command": name, "config_hash": config_hash, "jobs": jobs})
        seal_evaluation_plan(folder, jobs)
        return folder, jobs

    planned = {}
    for model in protocol["models"]:
        settings = deepcopy(protocol)
        settings["models"] = [model]
        pilot = deepcopy(settings)
        pilot["evolution_runs"] = 1
        pilot["skilllift"].update(max_rounds=1, max_candidates_per_round=1, mode_a_iters=1, mode_b_iters=1)
        pilot["skillopt"]["num_epochs"] = 1
        planned[model] = {"pilot": plan(model, "pilot", pilot),
                          "generalization": plan(model, "generalization", settings),
                          "ablation": plan(model, "ablation", settings)}
    write_json(root / "supplementary_plan.json", {"created_at": utc_now(), "workers": workers,
               "experiments": {model: {name: {"root": str(folder), "jobs": len(jobs)} for name, (folder, jobs) in plans.items()}
                               for model, plans in planned.items()}})
    if plan_only:
        return {"status": "planned", "root": str(root)}
    cancel_event = Event()

    def pipeline(model):
        from .statistics import summarize, cost_report, process_best_report
        plans = planned[model]
        pilot_root, pilot_jobs = plans["pilot"]
        pilot_result = run_jobs(pilot_jobs, pilot_root, workers[model], cancel_event=cancel_event)
        combined = interleave_training([plans["generalization"][1], plans["ablation"][1]])
        result = run_jobs(combined, root / model / "queue", workers[model], cancel_event=cancel_event)
        report_errors = []
        for name, (folder, jobs) in plans.items():
            try:
                if name != "pilot" and any(job["kind"] == "evaluate" for job in jobs):
                    summarize(folder, protocol["statistics"])
                if name == "ablation":
                    process_best_report(folder)
                cost_report(folder)
            except Exception as exc:
                report_errors.append({"experiment": name, "error_type": type(exc).__name__})
                with (folder / "report_errors.log").open("a") as stream:
                    stream.write(utc_now() + "\n" + traceback.format_exc() + "\n")
        return {"model": model, "status": "complete" if pilot_result["status"] == result["status"] == "complete" and not report_errors else "completed_with_errors",
                "pilot": pilot_result["status"], "formal": result["status"], "report_errors": report_errors}

    outcomes = []
    with ThreadPoolExecutor(max_workers=len(planned)) as executor:
        futures = {executor.submit(pipeline, model): model for model in planned}
        try:
            for future in as_completed(futures):
                try:
                    outcomes.append(future.result())
                except Exception as exc:
                    outcomes.append({"model": futures[future], "status": "driver_error", "error_type": type(exc).__name__})
                    with (root / futures[future] / "driver_errors.log").open("a") as stream:
                        stream.write(utc_now() + "\n" + traceback.format_exc() + "\n")
        except KeyboardInterrupt:
            cancel_event.set()
            raise
    summary = {"finished_at": utc_now(), "status": "complete" if all(row["status"] == "complete" for row in outcomes) else "completed_with_errors",
               "models": outcomes, "resume": "Run the same command; completed jobs are reused, failed and waiting jobs are revisited."}
    write_json(root / "supplementary_summary.json", summary)
    return summary
