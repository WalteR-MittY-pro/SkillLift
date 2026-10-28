#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
SETUP_SCOPE="${1:-all}"
case "$SETUP_SCOPE" in
  all|skilllearnbench|skillsbench) ;;
  *) echo 'Usage: setup.sh [all|skilllearnbench|skillsbench]' >&2; exit 2 ;;
esac
echo "Setup scope: $SETUP_SCOPE"
INDEX_ARGS=()
if [[ -n "${REVISION_INDEX_URL:-}" ]]; then
  INDEX_ARGS=(--index-url "$REVISION_INDEX_URL")
fi
case "$(uname -s)" in
  Linux) OS=linux ;;
  Darwin) OS=macos ;;
  MINGW*|MSYS*|CYGWIN*) OS=windows ;;
  *) echo 'Run this script in Linux, macOS or Windows Git Bash.' >&2; exit 2 ;;
esac
# Keep Windows and Linux interpreter metadata/packages separate in shared checkouts.
VENV_DIR=.venv-revision2026
[[ $OS != windows ]] || VENV_DIR=.venv-revision2026-windows
# Windows Python otherwise reads/writes files with the ANSI code page (GBK on Chinese systems).
export PYTHONUTF8=1
# These leak another Python's paths into the venv or stop `python -m` from seeing the repo root.
for var in PYTHONHOME PYTHONPATH PYTHONSAFEPATH PYTHONNOUSERSITE PYTHONSTARTUP; do
  if [[ -n "${!var:-}" ]]; then
    echo "NOTE: ignoring $var=${!var} for this setup. Remove it from your environment (Windows: System Properties > Environment Variables) so later commands behave the same." >&2
    unset "$var"
  fi
done
# benchflow 0.6.3 and the pinned SkillsBench both declare requires-python >=3.12.
is_py312() { "$@" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; }
PYTHON=()
if [[ -n "${REVISION_PYTHON:-}" ]]; then
  if [[ -f "$REVISION_PYTHON" ]]; then PYTHON=("$REVISION_PYTHON"); else read -r -a PYTHON <<< "$REVISION_PYTHON"; fi
  is_py312 "${PYTHON[@]}" || { echo "REVISION_PYTHON=$REVISION_PYTHON is not a working Python 3.12+." >&2; exit 2; }
else
  candidates=()
  if [[ $OS == windows ]] && command -v py >/dev/null 2>&1; then candidates+=("py -3.13" "py -3.12" "py -3"); fi
  candidates+=(python3.13 python3.12 python3 python)   # the Microsoft Store "python3" alias fails the probe
  for spec in "${candidates[@]}"; do
    read -r -a cand <<< "$spec"
    if command -v "${cand[0]}" >/dev/null 2>&1 && is_py312 "${cand[@]}"; then PYTHON=("${cand[@]}"); break; fi
  done
  if [[ ${#PYTHON[@]} -eq 0 ]] && command -v uv >/dev/null 2>&1; then
    found="$(uv python find '>=3.12' 2>/dev/null || true)"
    if [[ -n "$found" ]] && is_py312 "$found"; then PYTHON=("$found"); fi
  fi
fi
if [[ ${#PYTHON[@]} -eq 0 ]]; then
  {
    echo 'ERROR: no Python 3.12+ found (benchflow 0.6.3 and the pinned SkillsBench require >=3.12).'
    echo 'Pythons visible from this shell:'
    if command -v py >/dev/null 2>&1; then py -0p 2>&1 | sed 's/^/  /' || true; fi
    for c in python3 python; do
      if command -v "$c" >/dev/null 2>&1; then echo "  $c -> $("$c" --version 2>&1 || true)"; fi
    done
    if [[ $OS == windows ]]; then
      echo 'Install Python 3.12 for Windows (64-bit), then rerun this script; it will pick up "py -3.12" automatically:'
      echo '  winget install -e --id Python.Python.3.12'
      echo '  or the installer from https://www.python.org/downloads/windows/ (mirror: https://mirrors.huaweicloud.com/python/)'
      echo 'Or point to an existing interpreter: REVISION_PYTHON="/c/path/to/python.exe" bash experiments/revision2026/setup.sh'
    else
      echo 'Install python3.12 and python3.12-venv, or set REVISION_PYTHON=/path/to/python3.12.'
    fi
  } >&2
  exit 2
fi
echo "Using Python: ${PYTHON[*]} ($("${PYTHON[@]}" -c 'import sys; print(sys.version.split()[0], sys.executable)'))"
if ! docker info >/dev/null 2>&1; then
  if [[ $OS == windows ]]; then
    echo 'Docker is unreachable. Start Docker Desktop (Linux containers mode) and retry. This script does not install Docker.' >&2
  elif [[ $OS == macos ]]; then
    echo 'Docker is unreachable. Start Docker Desktop on macOS and retry.' >&2
  else
    echo 'Docker is unreachable from WSL. Start Windows Docker Desktop and enable Settings > Resources > WSL Integration for this distribution. This script does not install Docker.' >&2
  fi
  exit 2
fi
if [[ $OS == linux && "$PWD" == /mnt/* ]]; then
  echo 'Recommendation: the WSL home filesystem (~/SkillLift) is faster than /mnt/* for Linux package I/O. Continuing in the current directory.' >&2
fi
PYEXE="$("${PYTHON[@]}" -c 'import sys; print(sys.executable)')"
# Some Windows installs lack the stdlib venv/ensurepip (custom/partial install, PYTHONHOME/PYTHONPATH
# pointing at another Python). Diagnose instead of failing with a bare "No module named venv".
missing="$("${PYTHON[@]}" - <<'PY'
import importlib.util
print(" ".join(m for m in ("venv", "ensurepip", "ssl", "sqlite3", "ctypes", "tomllib") if importlib.util.find_spec(m) is None))
PY
)"
if [[ -n "$missing" ]]; then
  {
    echo "WARNING: $PYEXE is missing standard-library modules: $missing"
    "${PYTHON[@]}" -c 'import os, sys; lib = os.path.join(sys.prefix, "Lib" if os.name == "nt" else "lib"); print("  sys.prefix =", sys.prefix); print("  stdlib dir =", lib, "(exists)" if os.path.isdir(lib) else "(MISSING)"); print("  sys.path   =", sys.path[:4])' || true
    echo "  PYTHONHOME=${PYTHONHOME:-<unset>}  PYTHONPATH=${PYTHONPATH:-<unset>}"
    echo "  Fix: unset PYTHONHOME/PYTHONPATH if set; otherwise repair the install (Settings > Apps > Python 3.12 > Modify > Repair,"
    echo "       or rerun the python.org installer and choose Repair). A python.org/winget install includes venv by default."
  } >&2
fi
if [[ " $missing " == *" ssl "* || " $missing " == *" tomllib "* ]]; then
  echo 'ERROR: ssl/tomllib are required (pip downloads and task configs). Repair the Python install and rerun.' >&2
  exit 2
fi
if [[ " $missing " != *" venv "* && " $missing " != *" ensurepip "* ]]; then
  echo 'Creating/updating the Python virtual environment...'
  "${PYTHON[@]}" -m venv "$VENV_DIR"
elif command -v uv >/dev/null 2>&1; then
  echo 'Falling back to: uv venv --seed' >&2
  uv venv --seed --python "$PYEXE" "$VENV_DIR"
elif "${PYTHON[@]}" -m pip --version >/dev/null 2>&1; then
  echo 'Falling back to: virtualenv (installed with pip --user)' >&2
  "${PYTHON[@]}" -m pip install --user --upgrade virtualenv
  "${PYTHON[@]}" -m virtualenv "$VENV_DIR"
else
  echo 'ERROR: cannot create a virtual environment (no venv, uv or pip). Repair the Python install as described above and rerun.' >&2
  exit 2
fi
if [[ $OS == windows ]]; then
  PY="$VENV_DIR/Scripts/python.exe"
else
  PY="$VENV_DIR/bin/python"
fi
# Put the repository root on the venv's sys.path. `python -m experiments.revision2026` and the
# repo-local skilllift_eval package then import from any working directory, even when the
# interpreter does not add the current directory (._pth file next to python.exe, PYTHONSAFEPATH).
"$PY" - <<'PY'
import os, sys, sysconfig
from pathlib import Path
if sys.prefix == sys.base_prefix:
    sys.exit(f"ERROR: {sys.executable} is not running as a virtual environment (sys.prefix == base_prefix).")
site = Path(sysconfig.get_paths()["purelib"])
site.mkdir(parents=True, exist_ok=True)
(site / "skilllift-repo-root.pth").write_text(os.getcwd() + "\n", encoding="utf-8")
PY
if ! (cd / && "$OLDPWD/$PY" -c 'import experiments.revision2026.common, sys') ; then
  {
    echo 'ERROR: the new venv cannot import this repository. Interpreter diagnostics:'
    "$PY" -c 'import sys, os, glob, sysconfig; print("  executable  =", sys.executable); print("  prefix/base =", sys.prefix, "|", sys.base_prefix); print("  flags       = isolated=%d safe_path=%d no_site=%d" % (sys.flags.isolated, sys.flags.safe_path, sys.flags.no_site)); print("  purelib     =", sysconfig.get_paths()["purelib"]); print("  sys.path    ="); [print("     ", p) for p in sys.path]; print("  ._pth files =", glob.glob(os.path.join(os.path.dirname(sys._base_executable), "*._pth")))' || true
    echo 'A *._pth file next to python.exe (embeddable-style install) disables venv/site handling; reinstall Python 3.12 from python.org or winget.'
  } >&2
  exit 2
fi
echo 'Updating pip...'
"$PY" -m pip install --upgrade pip "${INDEX_ARGS[@]}"
EXTRA_REQUIREMENTS=()
if [[ "$SETUP_SCOPE" == skilllearnbench ]]; then
  echo 'Preparing only SkillLearnBench and SkillOpt sources...'
  "$PY" -m experiments.revision2026 prepare --benchmarks skilllearnbench
else
  if [[ "$SETUP_SCOPE" == skillsbench ]]; then
    "$PY" -m experiments.revision2026 prepare --benchmarks skillsbench
  else
    "$PY" -m experiments.revision2026 prepare
  fi
  "$PY" - <<'PY'
from pathlib import Path
import tomllib
# SkillsBench is a task repository, not an installable flat-layout Python package.
project = tomllib.loads(Path('.cache/revision2026/skillsbench/pyproject.toml').read_text(encoding='utf-8'))
Path('.cache/revision2026/skillsbench-requirements.txt').write_text('\n'.join(project['project']['dependencies']) + '\n', encoding='utf-8')
PY
  EXTRA_REQUIREMENTS=(-r .cache/revision2026/skillsbench-requirements.txt)
  if [[ "$SETUP_SCOPE" == all ]]; then
    EXTRA_REQUIREMENTS+=(-r .cache/revision2026/wildclawbench/requirements.txt)
  fi
fi
echo "Installing dependencies for $SETUP_SCOPE..."
INSTALLER=("$PY" -m pip install)
if [[ $OS == linux ]] && command -v uv >/dev/null 2>&1; then
  echo 'Using uv for concurrent dependency resolution and downloads.'
  INSTALLER=(uv pip install --python "$PY")
fi
"${INSTALLER[@]}" "${INDEX_ARGS[@]}" -r experiments/revision2026/requirements.txt \
  "${EXTRA_REQUIREMENTS[@]}" \
  -e ./skilllift -e ./packages/bounded-edits -e .cache/revision2026/skillopt \
  --config-settings editable_mode=compat
"$PY" -m pip freeze > .cache/revision2026/resolved-requirements.txt
"$PY" -m pip check
"$PY" -m pytest -q tests/revision2026
echo "Setup complete. Python: $PY  Copy/configure .env privately, then run the documented preflight command."
