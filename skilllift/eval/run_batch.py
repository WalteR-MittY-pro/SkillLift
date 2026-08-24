from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


AGENTCLAW_ROOT = Path(__file__).resolve().parent.parent


def resolve_wildclaw_root() -> Path:
    configured = os.environ.get("WILDCLAWBENCH_ROOT", "")
    root = Path(configured).expanduser() if configured else AGENTCLAW_ROOT.parent / "WildClawBench"
    root = root.resolve()
    if not (root / "eval" / "run_batch.py").is_file():
        raise RuntimeError(f"External WildClawBench runner not found: {root / 'eval' / 'run_batch.py'}")
    return root


def resolve_forwarded_args(args: list[str], wildclaw_root: Path) -> list[str]:
    resolved = list(args)
    roots = {
        "--task": wildclaw_root,
        "--models-config": AGENTCLAW_ROOT,
        "--skill-dir": AGENTCLAW_ROOT,
        "--lobster-workspace": AGENTCLAW_ROOT,
    }
    for index, arg in enumerate(resolved[:-1]):
        root = roots.get(arg)
        if root is None:
            continue
        value = Path(resolved[index + 1]).expanduser()
        if not value.is_absolute():
            resolved[index + 1] = str((root / value).resolve())
    return resolved


def resolve_forwarded_env() -> dict[str, str]:
    env = os.environ.copy()
    output_subdir = env.get("OUTPUT_SUBDIR", "output")
    if not Path(output_subdir).expanduser().is_absolute():
        env["OUTPUT_SUBDIR"] = str((AGENTCLAW_ROOT / output_subdir).resolve())
    return env


def main() -> int:
    wildclaw_root = resolve_wildclaw_root()
    command = [
        sys.executable,
        str(wildclaw_root / "eval" / "run_batch.py"),
        *resolve_forwarded_args(sys.argv[1:], wildclaw_root),
    ]
    return subprocess.run(command, cwd=wildclaw_root, env=resolve_forwarded_env(), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
