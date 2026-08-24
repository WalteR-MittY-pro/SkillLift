#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from skilllift_eval.token_accounting import backfill_skilllift_reference_usage


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Estimate historical CoEvo framework tokens and sum exact Docker usage."
    )
    parser.add_argument(
        "--reference-root",
        default="runs/taskoutput-gpt5.4",
        help="Directory containing NN-NN/skilllift and NN-NN/raw task outputs.",
    )
    parser.add_argument("--budget-multiplier", type=float, default=2.0)
    args = parser.parse_args()

    root = Path(args.reference_root).expanduser()
    if not root.is_absolute():
        root = (PROJECT_ROOT / root).resolve()
    paths = backfill_skilllift_reference_usage(
        root,
        budget_multiplier=args.budget_multiplier,
    )
    print(f"wrote {len(paths)} task token reports under {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
