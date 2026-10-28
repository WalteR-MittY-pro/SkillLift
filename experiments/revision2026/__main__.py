from __future__ import annotations

import argparse
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

from .common import IS_WINDOWS, endpoint, ensure_utf8_mode, load_protocol, read_json, verify_seeds, write_json
from .sources import PINS, prepare_sources, verify_source


def preflight(models, benchmarks, writer=False, protocol=None, require_cached_images=False):
    protocol = protocol or load_protocol()
    if platform.system() not in {"Linux", "Windows"}:
        raise ValueError("Run on Linux/WSL or Windows (Git Bash) with Docker Desktop; macOS is not supported")
    if sys.version_info < (3, 12):
        raise ValueError("Python 3.12+ is required")
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=20)
        subprocess.run(["docker", "compose", "version"], capture_output=True, check=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Docker is unreachable. Start Docker Desktop" + ("." if IS_WINDOWS else " and enable this distro under Resources > WSL Integration.")) from exc
    for model in models + (["writer"] if writer else []):
        resolved = endpoint(model)
        historical = {"gpt_skillsbench": "gpt-5.4-mini", "gpt_wildclaw": "gpt-5.4", "deepseek_pro": "deepseek-v4-pro"}
        if model in historical and resolved["MODEL"].rsplit("/", 1)[-1].lower() != historical[model]:
            raise ValueError(f"Historical {model} requires {historical[model]}, not a replacement model")
    verify_seeds()
    if set(models) & set(protocol.get("vision", {}).get("enabled_for", [])):
        if endpoint("glm_vision")["MODEL"].lower() != protocol["vision"]["model"]:
            raise ValueError("GLM_VISION_MODEL must match protocol.vision.model")
    for name in set(benchmarks) | {"skillopt"}:
        verify_source(name)
    from .oracles import native_task_ids
    for benchmark, count in {"skillsbench": 87, "wildclawbench": 60}.items():
        if benchmark in benchmarks:
            names = native_task_ids(benchmark)
            if len(names) != count or len(set(names)) != count:
                raise ValueError(f"Expected exactly {count} unique {benchmark} tasks at the pinned revision")
    if "skilllearnbench" in benchmarks:
        import tomllib
        from .sources import instances
        required = set()
        for instance in instances():
            config_path = instance.path / "task.toml"
            if config_path.exists():
                config = tomllib.loads(config_path.read_text())
                required.update(config.get("environment", {}).get("required_env", []))
        missing = sorted(name for name in required if not os.environ.get(name))
        if missing:
            raise ValueError("Full SkillLearnBench task coverage requires: " + ", ".join(missing))
        if require_cached_images:
            from .docker_agent import task_image_name
            available = set(subprocess.check_output(
                ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"], text=True, timeout=30
            ).splitlines())
            expected = {task_image_name(instance) + ":latest" for instance in instances()}
            missing_images = sorted(expected - available)
            if missing_images:
                raise ValueError(f"Connected Docker daemon is missing {len(missing_images)}/{len(expected)} cached task images. "
                                 "Check DOCKER_HOST/DOCKER_CONTEXT (WSL native Docker and Docker Desktop have separate image stores). "
                                 f"No jobs started. Examples: {', '.join(missing_images[:3])}")
            print(f"Cached SkillLearnBench task images: {len(expected)}/{len(expected)}", flush=True)
    skillopt_spec = importlib.util.find_spec("skillopt")
    if skillopt_spec is None:
        raise ValueError("Install the pinned SkillOpt package using setup.sh")
    from .sources import source_path
    if not skillopt_spec.origin or not Path(skillopt_spec.origin).resolve().is_relative_to(source_path("skillopt").resolve()):
        raise ValueError("Install SkillOpt from .cache/revision2026/skillopt so the imported trainer matches the pinned source")
    try:
        from skillopt.engine.trainer import ReflACTTrainer
        from skilllift.coordinator import TrialSpec
        import bounded_edits
    except ImportError as exc:
        raise ValueError(f"Experiment runtime import failed: {exc}. Reinstall the local packages and their dependencies.") from exc
    if "skillsbench" in benchmarks:
        from importlib.metadata import version
        if version("benchflow") != "0.6.3":
            raise ValueError("The native SSE adapter is verified against benchflow==0.6.3")
        from skilllift_eval.runners.skillsbench_adapter import register_openhands_sse
        register_openhands_sse()
    if "wildclawbench" in benchmarks:
        image = os.getenv("DOCKER_IMAGE") or "wildclawbench-ubuntu:v1.3"
        if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode:
            raise ValueError("WildClaw image is missing. Run prepare-wildclaw on WSL first.")
        from .sources import source_path
        if not (source_path("wildclawbench") / "workspace").is_dir():
            raise ValueError("WildClaw workspace data missing; run prepare-wildclaw")
    print(json.dumps({"status": "configuration_ready", "platform": platform.system(), "wsl": "microsoft" in platform.release().lower() or bool(os.getenv("WSL_DISTRO_NAME")),
                      "models": {model: endpoint(model)["MODEL"] for model in models},
                      "environment_precedence": "exported variables override .env",
                      "inference_tested": False, "container_tasks_tested": False}, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description="SkillLift revision experiments for WSL + Docker Desktop")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare", help="Fetch fixed benchmark/SkillOpt source and task data on the execution machine")
    prepare_parser.add_argument("--benchmarks", choices=["skilllearnbench", "skillsbench", "wildclawbench"], nargs="+",
                                help="Prepare only these benchmarks plus SkillOpt; defaults to all sources")
    sub.add_parser("prepare-wildclaw", help="On WSL: download/load the official OpenClaw image and prepare benchmark media")
    doctor = sub.add_parser("preflight")
    doctor.add_argument("--historical", action="store_true", help="Also require original-model endpoints and GPT-6-SOL writer for Table2 rebuilding")
    doctor.add_argument("--models", choices=["glm", "deepseek"], nargs="+")
    doctor.add_argument("--require-cached-images", action="store_true", help="Fail before scheduling if task images are absent from this Docker daemon")
    doctor.add_argument("--benchmarks", choices=["skilllearnbench", "skillsbench", "wildclawbench"], nargs="+",
                        help="Check only the selected benchmarks; defaults to the supplementary experiment suite")
    budget_parser = sub.add_parser("budget", help="Print the audited SkillOpt oracle-execution budget without running models")
    budget_parser.add_argument("--protocol", type=Path)
    together = sub.add_parser("supplementary", help="Run both new experiments in shared GLM/DeepSeek queues")
    together.add_argument("--run-dir", type=Path, required=True)
    together.add_argument("--protocol", type=Path)
    together.add_argument("--models", choices=["glm", "deepseek"], nargs="+")
    together.add_argument("--glm-workers", type=int, default=2)
    together.add_argument("--deepseek-workers", type=int, default=2)
    together.add_argument("--plan-only", action="store_true")
    for name in ["pilot", "generalization", "ablation", "table2-rebuild"]:
        p = sub.add_parser(name)
        p.add_argument("--run-dir", type=Path, required=True)
        p.add_argument("--protocol", type=Path)
        p.add_argument("--workers", type=int)
        if name != "table2-rebuild":
            p.add_argument("--models", choices=["glm", "deepseek"], nargs="+")
        p.add_argument("--stage", choices=["all", "train", "test"], default="all")
        p.add_argument("--retry-errors", action="store_true", help="Kept for compatibility; failed jobs are retried by default")
        p.add_argument("--no-retry-errors", action="store_true", help="Stop if earlier failed jobs exist instead of retrying them")
        p.add_argument("--plan-only", action="store_true")
        p.add_argument("--require-cached-images", action="store_true", help="Require all cached SkillLearnBench task images before scheduling")
    p = sub.add_parser("worker", help="Internal process worker; normally launched by the batch runner")
    p.add_argument("spec", type=Path)
    p = sub.add_parser("report")
    p.add_argument("--run-dir", type=Path, required=True)
    p = sub.add_parser("status")
    p.add_argument("--run-dir", type=Path, required=True)
    for name in ["audit-legacy", "repair-plan"]:
        p = sub.add_parser(name)
        p.add_argument("--archive", type=Path, required=True)
        p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("repair-run")
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--protocol", type=Path)
    p.add_argument("--workers", type=int)
    p.add_argument("--retry-errors", action="store_true", help="Kept for compatibility; failed jobs are retried by default")
    p.add_argument("--no-retry-errors", action="store_true", help="Stop if earlier failed jobs exist instead of retrying them")
    p.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        if platform.system() not in {"Linux", "Windows"}:
            raise ValueError("Prepare source/data on the execution machine (Linux/WSL or Windows)")
        prepare_sources(list(dict.fromkeys([*args.benchmarks, "skillopt"])) if args.benchmarks else list(PINS))
        return
    if args.command == "prepare-wildclaw":
        from .prepare_wildclaw import prepare
        prepare()
        return
    if args.command == "worker":
        from .runner import worker
        worker(args.spec)
        return
    if args.command in {"audit-legacy", "repair-plan"}:
        from .legacy import audit, recovery_plan
        result = (audit if args.command == "audit-legacy" else recovery_plan)(args.archive, args.output)
        if result:
            print(json.dumps({"verified_assets": len(result["assets"]), "unresolved": len(result["unresolved"]), "plan": str(args.output / "repair_plan.json")}, indent=2))
        return
    if args.command == "report":
        from .statistics import cost_report, process_best_report, summarize
        from .common import latest_protocol
        settings = latest_protocol(read_json(args.run_dir / "run_manifest.json"))["statistics"]
        output = {}
        if (args.run_dir / "evaluation_manifest.json").exists():
            rows = summarize(args.run_dir, settings)
            output.update(overall_rows=len(rows), complete_rows=sum(r["status"] == "complete" for r in rows))
        if (args.run_dir / "ablation_plan.json").exists():
            output["process_best"] = process_best_report(args.run_dir)
        output["costs"] = cost_report(args.run_dir)
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return
    if args.command == "status":
        from collections import Counter
        from .runner import job_done
        counts = Counter()
        current_jobs = {}
        for path in sorted((args.run_dir / "jobs").glob("*.json"), key=lambda p: p.stat().st_mtime_ns):
            if path.name.endswith(".status.json"):
                continue
            spec = read_json(path)
            # A changed timeout/protocol produces a new spec filename on resume.
            # Old attempts remain audit evidence, not additional logical tasks.
            identity = tuple(spec.get(key) for key in (
                "kind", "benchmark", "model", "method", "run", "family", "task_id", "phase", "repeat"
            ))
            current_jobs[identity] = (path, spec)
        for path, spec in current_jobs.values():
            status = path.with_suffix(".status.json")
            state = ("blocked" if spec.get("blocked_reason") else "complete" if job_done(spec)
                     else read_json(status)["status"] if status.exists() else "pending")
            counts[(spec["kind"], state)] += 1
        print(json.dumps([{"kind": k[0], "recorded_status": k[1], "count": n} for k, n in sorted(counts.items())], indent=2))
        return
    protocol = load_protocol(getattr(args, "protocol", None))
    if args.command == "budget":
        from .budget import skillopt_budget
        value = skillopt_budget(protocol)
        print(json.dumps({"skillopt_per_family": value, "two_models_twenty_families_training_max": value["maximum"] * 40,
                          "skillopt_heldout_executions": 160, "excluding": "infrastructure retries and LLM-only calls"}, indent=2))
        return
    if getattr(args, "models", None):
        protocol["models"] = list(dict.fromkeys(args.models))
    if args.command == "supplementary":
        if not 1 <= args.glm_workers <= 2 or not 1 <= args.deepseek_workers <= 2:
            raise ValueError("GLM workers must be 1–2; DeepSeek workers must be 1–2")
        if not args.plan_only:
            preflight(protocol["models"], ["skilllearnbench", "skillsbench"], protocol=protocol)
        from .batch import run_supplementary
        print(json.dumps(run_supplementary(args.run_dir, protocol, glm_workers=args.glm_workers,
                                          deepseek_workers=args.deepseek_workers, plan_only=args.plan_only), indent=2))
        return
    if args.command == "preflight":
        models = protocol["models"]
        if args.historical:
            from .legacy import HISTORICAL_MODELS
            models = sorted({m for group in HISTORICAL_MODELS.values() for m in group})
        benchmarks = args.benchmarks or (["skilllearnbench", "skillsbench"] + (["wildclawbench"] if args.historical else []))
        preflight(models, benchmarks, writer=args.historical, protocol=protocol, require_cached_images=args.require_cached_images)
        return
    from .runner import ablation_jobs, generalization_jobs, prepare_run, run_jobs, seal_evaluation_plan
    if args.command == "pilot":
        protocol = deepcopy(protocol)
        protocol["evolution_runs"] = 1
        protocol["skilllift"].update(max_rounds=1, max_candidates_per_round=1, mode_a_iters=1, mode_b_iters=1)
        protocol["skillopt"]["num_epochs"] = 1
    if args.command == "table2-rebuild":
        from .legacy import HISTORICAL_MODELS
        protocol["models"] = sorted({m for group in HISTORICAL_MODELS.values() for m in group})
        protocol["rebuild_scope"] = "fresh 36-cell experiment; not reproduction of historical means"
        protocol["rebuild_initialization"] = "SkillOpt and SkillLift start from common GPT-6-SOL task skill; CoEvoSkills retains its native initialization. Report this difference."
    if args.command == "repair-run":
        plan = read_json(args.plan)
        protocol["models"] = sorted({asset["model"] for asset in plan["assets"] + plan["unresolved"]})
        if not plan["assets"]:
            raise ValueError("No verified recoverable assets. Review the audit or use explicit table2-rebuild.")
        protocol["repair_plan_hash"] = __import__("hashlib").sha256(args.plan.read_bytes()).hexdigest()
    if args.command == "ablation":
        if not protocol["ablation"]["benchmarks"]:
            raise ValueError("protocol.json has no ablation benchmarks")
        benchmarks = list(protocol["ablation"]["benchmarks"])
    elif args.command in {"pilot", "generalization"}:
        benchmarks = ["skilllearnbench"]
    else:
        benchmarks = ["skillsbench", "wildclawbench"]
    if not args.plan_only:
        preflight(protocol["models"], benchmarks, writer=args.command == "table2-rebuild", protocol=protocol,
                  require_cached_images=getattr(args, "require_cached_images", False))
    protocol, config_hash = prepare_run(args.run_dir, protocol)
    if args.command in {"pilot", "generalization"}:
        jobs = generalization_jobs(args.run_dir, protocol, config_hash, stage=args.stage, pilot=args.command == "pilot")
    elif args.command == "ablation":
        jobs = ablation_jobs(args.run_dir, protocol, config_hash, stage=args.stage)
    elif args.command == "table2-rebuild":
        from .legacy import rebuild_jobs
        jobs = rebuild_jobs(args.run_dir, protocol, config_hash)
        if args.stage != "all":
            allowed = {"train", "write_seed"} if args.stage == "train" else {"evaluate"}
            jobs = [j for j in jobs if j["kind"] in allowed]
    else:
        from .legacy import repair_jobs
        jobs = repair_jobs(args.plan, args.run_dir, protocol, config_hash)
    write_json(args.run_dir / f"{args.command}_plan.json", {"command": args.command, "config_hash": config_hash, "jobs": jobs})
    seal_evaluation_plan(args.run_dir, jobs)
    print(json.dumps({"command": args.command, "planned_jobs": len(jobs), "run_dir": str(args.run_dir), "plan_only": args.plan_only}, indent=2), flush=True)
    if not args.plan_only:
        if args.workers is not None and args.workers < 1:
            raise ValueError("--workers must be positive")
        run_jobs(jobs, args.run_dir, args.workers or protocol["workers"], retry_errors=not args.no_retry_errors)
        if args.command != "pilot":
            from .statistics import summarize, cost_report
            if any(job["kind"] == "evaluate" for job in jobs):
                summarize(args.run_dir, protocol["statistics"])
            if args.command == "ablation":
                from .statistics import process_best_report
                process_best_report(args.run_dir)
            cost_report(args.run_dir)


if __name__ == "__main__":
    ensure_utf8_mode(["-m", "experiments.revision2026", *sys.argv[1:]])
    try:
        main()
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
