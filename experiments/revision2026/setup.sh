#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
if [[ "$(uname -s)" != Linux ]]; then
  echo 'Run this script inside WSL (or Linux), not PowerShell, Windows Python, or macOS.' >&2
  exit 2
fi
PYTHON="${REVISION_PYTHON:-python3}"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12+ is required for the native SkillsBench dependency"'
if ! docker info >/dev/null 2>&1; then
  echo 'Docker is unreachable from WSL. Start Windows Docker Desktop and enable Settings > Resources > WSL Integration for this distribution. This script does not install Docker.' >&2
  exit 2
fi
if [[ "$PWD" == /mnt/* ]]; then
  echo 'Recommendation: clone into the WSL home filesystem (~/SkillLift), not /mnt/c, for Linux permissions and I/O performance.' >&2
fi
"$PYTHON" -m venv .venv-revision2026
PY=.venv-revision2026/bin/python
"$PY" -m pip install --upgrade pip
"$PY" -m experiments.revision2026 prepare
"$PY" - <<'PY'
from pathlib import Path
import tomllib
# SkillsBench is a task repository, not an installable flat-layout Python package.
project = tomllib.loads(Path('.cache/revision2026/skillsbench/pyproject.toml').read_text())
Path('.cache/revision2026/skillsbench-requirements.txt').write_text('\n'.join(project['project']['dependencies']) + '\n')
PY
"$PY" -m pip install -r experiments/revision2026/requirements.txt \
  -r .cache/revision2026/skillsbench-requirements.txt \
  -r .cache/revision2026/wildclawbench/requirements.txt \
  -e ./skilllift -e ./packages/bounded-edits -e .cache/revision2026/skillopt
"$PY" -m pip freeze > .cache/revision2026/resolved-requirements.txt
"$PY" -m pytest -q tests/revision2026
echo 'Setup complete. Copy/configure .env privately, then run the documented preflight command.'
