from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skilllift_eval.runners.skilllift_skillsbench_tasks import (  # noqa: E402
    SkillLiftSkillsBenchTasksRunner,
    SkillLiftSkillsBenchTasksSettings,
    _write_once,
    build_shard_manifest,
    load_task_universe,
    merge_shards,
    select_domain_task_ids,
    select_task_ids,
    task_universe_hash,
)

AGENTCLAW_ROOT = ROOT / "skilllift"
if str(AGENTCLAW_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTCLAW_ROOT))

from skilllift_eval.cli import load_env_files  # noqa: E402
from skilllift_eval.skillsbench_cli import load_and_validate_split  # noqa: E402
from skilllift_eval.runners.skilllift_thresholds import probability_param  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run per-task SkillPortfolio CoEvo on SkillsBench.")
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "skillsbench" / "tasks.yaml")
    parser.add_argument(
        "--baseline",
        choices=("skilllift", "coevoskills"),
        default="skilllift",
    )
    parser.add_argument("--phase", choices=("check", "evolve", "merge"), default="evolve")
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--model", help="Select a label under skillsbench.model_endpoints in the config.")
    parser.add_argument("--domain", nargs="+", help="One or more SkillsBench domains; defaults to all eight.")
    parser.add_argument("--task-range", help="One-based inclusive range within the selected domains.")
    parser.add_argument("--tasks", help="Comma-separated explicit task IDs.")
    parser.add_argument("--shards", help="Comma-separated shard roots for --phase merge.")
    parser.add_argument("--resume", action="store_true", help="Accepted for clarity; resume is always enabled.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config_path = args.config.resolve()
    config = _load_config(config_path)
    load_env_files(ROOT)
    settings = _settings(
        config_path,
        config,
        args.run_root,
        model_override=args.model,
        require_credentials=args.phase == "evolve",
    )
    if args.phase == "merge":
        if not args.shards:
            raise ValueError("--phase merge requires --shards")
        result = merge_shards(
            [Path(item).resolve() for item in args.shards.split(",") if item.strip()],
            settings.run_root,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    universe = load_task_universe(settings.split_path, settings.tasks_root)
    split = load_and_validate_split(settings.split_path, settings.tasks_root)
    domain_universe = select_domain_task_ids(split, args.domain)
    explicit = tuple(item.strip() for item in (args.tasks or "").split(",") if item.strip())
    selected = select_task_ids(domain_universe, task_range=args.task_range, tasks=explicit)
    if args.phase == "check":
        result = build_shard_manifest(
            universe,
            selected,
            universe_hash=task_universe_hash(settings.tasks_root, universe),
        )
        result["status"] = "ready"
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.baseline == "skilllift":
        result = SkillLiftSkillsBenchTasksRunner(settings).run(selected)
    else:
        result = _run_evolution_baseline(args.baseline, settings, config, selected)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if int(result.get("failed_task_count", 0)) else 0


def _run_evolution_baseline(
    baseline: str,
    settings: SkillLiftSkillsBenchTasksSettings,
    config: dict[str, Any],
    task_ids: tuple[str, ...],
) -> dict[str, Any]:
    from skilllift_eval.runners.skillsbench_evolution_adapter import (
        SkillsBenchEvolutionSettings,
    )

    algorithm_params = dict(config.get(baseline) or {})
    universe = load_task_universe(settings.split_path, settings.tasks_root)
    manifest = build_shard_manifest(
        universe,
        task_ids,
        universe_hash=task_universe_hash(settings.tasks_root, universe),
    )
    manifest.update(
        baseline=baseline,
        runner_version=f"skillsbench_{baseline}_runner_v1",
        algorithm_params_hash=hashlib.sha256(
            json.dumps(
                algorithm_params, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest(),
    )
    _write_evolution_manifest(settings.run_root, manifest)
    common = SkillsBenchEvolutionSettings(
        project_root=settings.project_root,
        run_root=settings.run_root,
        tasks_root=settings.tasks_root,
        split_path=settings.split_path,
        python_executable=settings.python_executable,
        model=settings.model,
        sandbox=settings.sandbox,
        base_url=settings.base_url,
        api_key=settings.api_key,
        patch_source=settings.patch_source,
        agent=settings.agent,
        framework_model=settings.framework_model
        or settings.model.split("/", 1)[-1],
        use_stream=settings.use_stream,
        prebuilt_image_template=settings.prebuilt_image_template,
        algorithm_params=algorithm_params,
    )
    if baseline == "coevoskills":
        from skilllift_eval.runners.coevoskills_skillsbench import (
            CoEvoSkillsSkillsBenchRunner,
        )

        return CoEvoSkillsSkillsBenchRunner(common).run(task_ids)
    raise ValueError(f"unknown SkillsBench evolution baseline: {baseline}")


def _write_evolution_manifest(run_root: Path, manifest: dict[str, Any]) -> None:
    path = run_root / "run_manifest.json"
    try:
        _write_once(path, manifest)
        return
    except ValueError:
        other_files = [
            item
            for item in run_root.rglob("*")
            if item.is_file() and item.resolve() != path.resolve()
        ]
        if other_files:
            raise
    path.unlink()
    _write_once(path, manifest)


def _settings(
    config_path: Path,
    config: dict[str, Any],
    run_root_override: Path | None,
    *,
    model_override: str | None = None,
    require_credentials: bool,
) -> SkillLiftSkillsBenchTasksSettings:
    project_root = _resolve(config_path.parent, str(config.get("project_root", "..")))
    benchmark = config["skillsbench"]
    skilllift = config.get("skilllift") or {}
    model_label = str(model_override or benchmark.get("default_model") or "")
    endpoints = benchmark.get("model_endpoints")
    if not isinstance(endpoints, dict) or not model_label:
        raise ValueError("SkillsBench config requires default_model and model_endpoints")
    endpoint = endpoints.get(model_label)
    if not isinstance(endpoint, dict):
        raise ValueError(f"unknown SkillsBench model endpoint: {model_label}")
    model_env = str(endpoint.get("model_env") or "")
    raw_model = str(endpoint.get("model") or os.environ.get(model_env, ""))
    if not raw_model:
        raise ValueError(f"SkillsBench model endpoint {model_label} requires model or populated model_env")
    provider = str(endpoint.get("provider") or "")
    model = raw_model if not provider or "/" in raw_model else f"{provider}/{raw_model}"
    framework_model_env = str(endpoint.get("framework_model_env") or "")
    framework_model = str(
        endpoint.get("framework_model")
        or os.environ.get(framework_model_env, "")
        or raw_model.split("/", 1)[-1]
    )
    base_url_env = str(endpoint.get("base_url_env") or "")
    base_url = str(endpoint.get("base_url") or os.environ.get(base_url_env, ""))
    api_key_env = str(endpoint.get("api_key_env") or "")
    if not api_key_env:
        raise ValueError(f"SkillsBench model endpoint {model_label} requires api_key_env")
    api_key = os.environ.get(api_key_env, "")
    if require_credentials and (not base_url or not api_key):
        sources = f"{base_url_env or 'base_url'} and {api_key_env}"
        raise ValueError(f"missing {sources} for SkillsBench model endpoint {model_label}")
    configured_run_root = _resolve(project_root, str(config.get("run_root", "runs/skilllift_skillsbench_tasks")))
    warning_rows: list[dict[str, Any]] = []
    mode_a_threshold = probability_param(
        skilllift, "mode_a_min_score_threshold", 0.85, warning_sink=warning_rows.append
    )
    rank_threshold = probability_param(
        skilllift, "rank_alignment_threshold", 0.9, warning_sink=warning_rows.append
    )
    terminal_threshold = probability_param(
        skilllift, "terminal_threshold", 1.0, warning_sink=warning_rows.append
    )
    if warning_rows:
        warning_path = (run_root_override.resolve() if run_root_override else configured_run_root) / "threshold_warnings.jsonl"
        warning_path.parent.mkdir(parents=True, exist_ok=True)
        with warning_path.open("a", encoding="utf-8") as handle:
            for row in warning_rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return SkillLiftSkillsBenchTasksSettings(
        project_root=project_root,
        run_root=(run_root_override.resolve() if run_root_override else configured_run_root),
        tasks_root=_resolve(project_root, str(benchmark["tasks_root"])),
        split_path=_resolve(project_root, str(benchmark["split_path"])),
        python_executable=_resolve_executable(project_root, str(benchmark["python_executable"])),
        model=model,
        sandbox=str(benchmark.get("sandbox", "docker")),
        base_url=base_url,
        api_key=api_key,
        patch_source=_resolve(project_root, str(benchmark.get("patch_source", "scripts/oh_skill_patch.py"))),
        agent=str(endpoint.get("agent") or benchmark.get("agent") or "openhands"),
        framework_model=framework_model,
        max_context_chars=int(skilllift["max_context_chars"]) if skilllift.get("max_context_chars") else None,
        candidate_concurrency=int(skilllift.get("candidate_concurrency", 2)),
        mode_a_iters=int(skilllift.get("mode_a_iters", 2)),
        mode_b_iters=int(skilllift.get("mode_b_iters", 2)),
        mode_a_min_score_threshold=mode_a_threshold,
        rank_alignment_threshold=rank_threshold,
        terminal_threshold=terminal_threshold,
        use_stream=bool(endpoint.get("stream", False)),
        prebuilt_image_template=(
            str(benchmark["prebuilt_image_template"])
            if benchmark.get("prebuilt_image_template")
            else None
        ),
    )


def _load_config(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("SkillsBench task CoEvo config must be a YAML object")
    return payload


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _resolve_executable(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


if __name__ == "__main__":
    raise SystemExit(main())
