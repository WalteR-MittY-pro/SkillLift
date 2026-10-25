from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
from urllib.parse import quote

from .common import HERE, ROOT, digest, endpoint_identity, read_json, require_frozen, tree_hash, utc_now, write_json
from .sources import PINS, Instance, instances, source_path


def code_identity():
    paths = sorted(set(HERE.glob("*.py")) | set((ROOT / "skilllift").rglob("*.py")) |
                   set((ROOT / "skilllift_eval").rglob("*.py")) | set((ROOT / "packages/bounded-edits/src").rglob("*.py")))
    return digest([(str(p.relative_to(ROOT)), p.read_text()) for p in paths])


def prepare_run(root, protocol):
    root = Path(root).resolve()
    for name in ("skilllearnbench", "skillopt"):
        if protocol[name]["revision"] != PINS[name][1]:
            raise ValueError(f"Protocol and pinned {name} implementation revisions disagree")
    protocol = {**protocol, "source_revisions": {k: v[1] for k, v in PINS.items()}}
    identity = {"protocol": protocol, "endpoints": {m: endpoint_identity(m) for m in protocol["models"]},
                "driver_hash": code_identity(), "seed_manifest_hash": digest(read_json(HERE / "seeds/LLM_SKILLS_FROZEN.json"))}
    if protocol.get("rebuild_scope"):
        identity["writer_endpoint"] = endpoint_identity("writer")
    resolved = ROOT / ".cache/revision2026/resolved-requirements.txt"
    identity["resolved_dependencies"] = digest(resolved.read_text()) if resolved.exists() else None
    manifest = root / "run_manifest.json"
    if manifest.exists():
        if read_json(manifest)["identity"] != identity:
            raise ValueError("Run settings, model endpoint, source, or code changed. Use a new --run-dir.")
    else:
        write_json(manifest, {"created_at": utc_now(), "identity": identity, "config_hash": digest(identity)})
    return protocol, digest(identity)


def cell_path(root, benchmark, model, method, run, family):
    return Path(root) / "training" / benchmark / model / method / f"run-{run}" / family


def generalization_jobs(root, protocol, config_hash, stage="all", pilot=False):
    items = instances()
    families = sorted({item.family for item in items})
    if pilot:
        families = protocol["skilllearnbench"]["pilot_families"]
    jobs = []
    methods = protocol["skilllearnbench"]["methods"]
    for model in protocol["models"]:
        for method in methods:
            for run in range(1 if pilot else protocol["evolution_runs"]):
                for family in families:
                    common = dict(benchmark="skilllearnbench", model=model, method=method, run=run, family=family,
                                  root=str(Path(root).resolve()), protocol=protocol, config_hash=config_hash)
                    if stage in {"all", "train"}:
                        jobs.append({**common, "kind": "train"})
                    if stage in {"all", "test"}:
                        selected = [x for x in items if x.family == family and (x.number == 1 if pilot else x.number > 1)]
                        for item in selected:
                            for repeat in range(1 if pilot else protocol["test_repeats"]):
                                jobs.append({**common, "kind": "evaluate", "phase": "pilot" if pilot else "heldout",
                                             "instance": item.number, "task_id": item.task_id, "task_path": str(item.path.resolve()), "repeat": repeat})
    return jobs


def ablation_jobs(root, protocol, config_hash, stage="all", benchmarks=None):
    from .oracles import native_task_ids
    jobs = []
    for benchmark in benchmarks or protocol["ablation"]["benchmarks"]:
        for task_id in native_task_ids(benchmark):
            for model in protocol["models"]:
                for method in protocol["ablation"]["methods"]:
                    for run in range(protocol["evolution_runs"]):
                        common = dict(benchmark=benchmark, model=model, method=method, run=run, family=task_id,
                                      root=str(Path(root).resolve()), protocol=protocol, config_hash=config_hash)
                        if stage in {"all", "train"}:
                            jobs.append({**common, "kind": "train"})
                        if stage in {"all", "test"}:
                            for repeat in range(protocol["ablation"]["final_repeats"]):
                                jobs.append({**common, "kind": "evaluate", "phase": "independent_final", "task_id": task_id, "repeat": repeat})
    return jobs


def freeze_cell(source, cell, spec, training_details):
    cell = Path(cell)
    target = cell / "skills"
    if target.exists():
        raise ValueError(f"Unmarked skill directory exists: {target}; do not overwrite it")
    staging = cell / (".freeze-" + uuid.uuid4().hex)
    shutil.copytree(source, staging)
    staging.replace(target)
    write_json(cell / "frozen.json", {"status": "complete", "frozen_at": utc_now(), "config_hash": spec["config_hash"],
        "benchmark": spec["benchmark"], "model": spec["model"], "method": spec["method"], "family": spec["family"],
        "evolution_run": spec["run"], "skill_hash": tree_hash(target), "training": training_details})


def train_cell(spec):
    from .oracles import TrainingOracle, native_adapter
    from .optimization import run_skilllift, run_direct_oracle, run_skillopt
    cell = cell_path(spec["root"], spec["benchmark"], spec["model"], spec["method"], spec["run"], spec["family"])
    marker = cell / "frozen.json"
    if marker.exists():
        frozen = require_frozen(marker)
        if frozen["config_hash"] != spec["config_hash"]:
            raise ValueError("Frozen cell configuration mismatch")
        return
    attempt = cell / "attempts" / uuid.uuid4().hex
    attempt.mkdir(parents=True)
    ledger = attempt / "usage.jsonl"
    native, instance = None, None
    if spec["benchmark"] == "skilllearnbench":
        instance = next(x for x in instances() if x.family == spec["family"] and x.number == 1)
        seed = HERE / "seeds/skills/llm_written" / spec["family"]
        if spec["method"] == "human_written":
            seed = source_path("skilllearnbench") / "skills/human_authored" / spec["family"]
        elif spec["method"] == "no_skill":
            seed = attempt / "empty"
            seed.mkdir()
    else:
        native = native_adapter(spec["benchmark"], spec["family"], spec["model"], attempt / "native", spec["protocol"])
        seed = native.seed_portfolio(spec["family"]).root
        if spec["method"] == "no_skill":
            seed = attempt / "empty"
            seed.mkdir()
        elif spec.get("fresh_table2") and spec["method"] in {"llm_written", "skillopt", "skilllift"}:
            seed = Path(spec["root"]) / "shared_llm_seeds" / spec["benchmark"] / spec["family"] / "skills"
    if not seed.is_dir():
        raise FileNotFoundError(f"Missing baseline skill source: {seed}")
    if spec["method"] in {"no_skill", "human_written", "llm_written"}:
        freeze_cell(seed, cell, spec, {"static": True, "source_hash": tree_hash(seed)})
        return
    oracle = TrainingOracle(benchmark=spec["benchmark"], family=spec["family"], model=spec["model"], seed=seed,
                            protocol=spec["protocol"], root=attempt, ledger=ledger, native=native, instance=instance)
    initial_hash = tree_hash(seed)
    kwargs = dict(oracle=oracle, protocol=spec["protocol"], root=attempt, model=spec["model"], ledger=ledger)
    if spec["method"] == "skilllift":
        winner, details = run_skilllift(**kwargs)
    elif spec["method"] == "direct_oracle":
        winner, details = run_direct_oracle(**kwargs)
    elif spec["method"] == "skillopt":
        winner, details = run_skillopt(**kwargs, run_index=spec["run"])
    elif spec["method"] == "coevoskills":
        from .legacy import run_coevo
        winner, details = run_coevo(**kwargs)
    else:
        raise ValueError("Unsupported optimization method: " + spec["method"])
    freeze_cell(winner, cell, spec, {**details, "initial_hash": initial_hash, "oracle_rollouts": oracle.calls, "attempt": str(attempt)})


def evaluation_path(spec):
    return Path(spec["root"]) / "evaluations" / spec["benchmark"] / spec["model"] / spec["method"] / f"run-{spec['run']}" / ("task-" + quote(spec["task_id"], safe="")) / spec["phase"] / f"repeat-{spec['repeat']}"


def evaluation_identity(spec):
    return {key: spec[key] for key in ("benchmark", "model", "method", "run", "family", "task_id", "repeat", "phase")}


def seal_evaluation_plan(root, jobs):
    evaluations = [job for job in jobs if job["kind"] == "evaluate"]
    if not evaluations:
        return
    keys = [digest(evaluation_identity(job)) for job in evaluations]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate logical evaluation cells in plan")
    path = Path(root) / "evaluation_manifest.json"
    planned = {"evaluations": evaluations, "plan_hash": digest(evaluations)}
    if path.exists() and read_json(path) != planned:
        raise ValueError("The sealed evaluation roster changed; use a new run directory")
    write_json(path, planned)


def evaluate_cell(spec):
    cell = Path(spec.get("frozen_cell") or cell_path(spec["root"], spec["benchmark"], spec["model"], spec["method"], spec["run"], spec["family"]))
    frozen = require_frozen(cell / "frozen.json")
    if frozen["config_hash"] != spec["config_hash"]:
        raise ValueError("Training and evaluation configurations differ")
    if spec["phase"] == "heldout" and int(spec.get("instance", 0)) <= 1:
        raise ValueError("Training instance cannot enter held-out results")
    location = evaluation_path(spec)
    selected = location / "selected.json"
    if selected.exists():
        record = read_json(selected)
        if record.get("cell_identity") != evaluation_identity(spec) or record["config_hash"] != spec["config_hash"] or record["skill_hash"] != frozen["skill_hash"]:
            raise ValueError("Evaluation resume identity mismatch")
        if record["status"] == "OK":
            return
    attempt = location / "attempts" / uuid.uuid4().hex
    ledger = attempt.parent / (attempt.name + ".usage.jsonl")
    if spec["benchmark"] == "skilllearnbench":
        from .docker_agent import evaluate
        instance = Instance(spec["family"], spec["instance"], Path(spec["task_path"]))
        result = evaluate(instance, None if spec["method"] == "no_skill" else cell / "skills", spec["model"], spec["protocol"]["solver"], attempt, ledger, Path(spec["root"]))
    else:
        from .oracles import native_adapter, checked_native_result
        from skilllift.coordinator import TrialSpec
        from skilllift.portfolio import PortfolioRef
        adapter = native_adapter(spec["benchmark"], spec["family"], spec["model"], attempt, spec["protocol"])
        portfolio = PortfolioRef.from_directory(task_id=spec["family"], root=cell / "skills", config_hash=spec["config_hash"])
        evaluation = checked_native_result(adapter.evaluate(spec["family"], portfolio, TrialSpec("independent-" + uuid.uuid4().hex, "independent_final", 0, final_index=spec["repeat"])))
        result = {"status": "OK" if evaluation.is_valid else "ERROR", "reward": evaluation.reward,
                  "error": evaluation.infrastructure_error, "native_result": evaluation.to_dict(), "finished_at": utc_now()}
        write_json(attempt / "result.json", result)
    record = {**result, "benchmark": spec["benchmark"], "model_label": spec["model"], "execution_model_id": endpoint_identity(spec["model"])["model"], "method": spec["method"],
              "family": spec["family"], "task_id": spec["task_id"], "evolution_run": spec["run"], "repeat": spec["repeat"],
              "phase": spec["phase"], "config_hash": spec["config_hash"], "skill_hash": frozen["skill_hash"],
              "frozen_at": frozen["frozen_at"], "attempt": str(attempt), "cell_identity": evaluation_identity(spec)}
    write_json(selected, record)
    if result["status"] != "OK":
        raise RuntimeError(f"Evaluation ERROR; not a zero score: {selected}")


def worker(spec_file):
    import fcntl
    spec_file = Path(spec_file)
    spec = read_json(spec_file)
    with spec_file.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        write_json(spec_file.with_suffix(".status.json"), {"status": "running", "started_at": utc_now()})
        try:
            if spec["kind"] == "write_seed":
                from .legacy import write_seed
                write_seed(spec)
            else:
                (train_cell if spec["kind"] == "train" else evaluate_cell)(spec)
        except Exception as exc:
            write_json(spec_file.with_suffix(".status.json"), {"status": "error", "error_type": type(exc).__name__, "finished_at": utc_now()})
            raise
        write_json(spec_file.with_suffix(".status.json"), {"status": "complete", "finished_at": utc_now()})


def run_jobs(jobs, root, workers, *, retry_errors=False):
    root = Path(root)
    seal_evaluation_plan(root, jobs)
    queue = []
    blocked = []
    for spec in jobs:
        key = digest(spec)
        path = root / "jobs" / (key + ".json")
        write_json(path, spec)
        if spec.get("blocked_reason"):
            blocked.append({"cell": evaluation_identity(spec), "reason": spec["blocked_reason"]})
            continue
        status = path.with_suffix(".status.json")
        if status.exists() and read_json(status).get("status") == "error" and not retry_errors:
            raise ValueError("Previous errors exist. Inspect them and use --retry-errors to make fresh attempts.")
        queue.append(path)
    write_json(root / "blocked_jobs.json", blocked)

    def launch(path):
        with path.with_suffix(".stdout.log").open("a") as stdout, path.with_suffix(".stderr.log").open("a") as stderr:
            stdout.write(f"\nLauncher attempt {utc_now()}\n")
            stdout.flush()
            result = subprocess.run([sys.executable, "-m", "experiments.revision2026", "worker", str(path)], cwd=ROOT, stdout=stdout, stderr=stderr)
        job = read_json(path)
        print(f"{job['kind']} {job['benchmark']}/{job['model']}/{job['method']}/{job['family']} run={job['run']}: {'OK' if result.returncode == 0 else 'ERROR'}", flush=True)
        return result.returncode

    # Train all cells before any final evaluation. No test result can feed optimization.
    for kind in ("write_seed", "train", "evaluate"):
        phase = [path for path in queue if read_json(path)["kind"] == kind]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(launch, phase))
        if any(outcomes):
            raise RuntimeError(f"{kind} phase has {sum(code != 0 for code in outcomes)} failed jobs; inspect {root / 'jobs'}. Subsequent phases were not started.")
