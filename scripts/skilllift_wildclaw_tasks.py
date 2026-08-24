from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skilllift_eval.runners.skilllift_wildclaw_tasks import (  # noqa: E402
    ALL_CATEGORIES,
    WildClawBatchSettings,
    run_wildclaw_batch,
)
from skilllift_eval.cli import load_env_files  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run per-task Portfolio CoEvo on WildClawBench categories.")
    parser.add_argument("--config", type=Path, default=ROOT / "skilllift_eval" / "config.yaml")
    parser.add_argument("--run-root", type=Path, default=ROOT / "runs" / "skilllift_wildclaw_tasks")
    parser.add_argument("--model", required=True, help="Key under model_endpoints in the shared YAML config.")
    parser.add_argument("--category", type=int, choices=ALL_CATEGORIES, nargs="+", default=ALL_CATEGORIES)
    parser.add_argument("--resume", action="store_true", help="Accepted for clarity; resume is always enabled.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_env_files(ROOT)
    summary = run_wildclaw_batch(
        WildClawBatchSettings(
            project_root=ROOT,
            wildclaw_root=ROOT / "WildClawBench",
            run_root=args.run_root,
            config_path=args.config,
            model=args.model,
            categories=tuple(args.category),
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["failed_tasks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
