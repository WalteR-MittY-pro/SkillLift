#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) PY=.venv-revision2026/Scripts/python.exe; export PYTHONUTF8=1 ;;
  *) PY=.venv-revision2026/bin/python ;;
esac
MODE="${1:-supplementary}"
MODEL="${2:-}"
MODEL_ARGS=()
if [[ -n "$MODEL" ]]; then
  [[ "$MODE" == supplementary && ( "$MODEL" == glm || "$MODEL" == deepseek ) ]] || { echo 'Model selection is supported as: run_all.sh supplementary [glm|deepseek]' >&2; exit 2; }
  MODEL_ARGS=(--models "$MODEL")
fi
WORKERS="${WORKERS:-2}"  # Legacy rebuild/repair only; supplementary has two model-specific pools.
RUN_ROOT="${RUN_ROOT:-runs/revision2026}"
[[ -x "$PY" ]] || { echo 'Run bash experiments/revision2026/setup.sh first (WSL or Git Bash).' >&2; exit 2; }
if [[ "$MODE" == supplementary ]]; then
  exec "$PY" -m experiments.revision2026 supplementary --run-dir "$RUN_ROOT" \
    --glm-workers "${GLM_WORKERS:-2}" --deepseek-workers "${DEEPSEEK_WORKERS:-2}" "${MODEL_ARGS[@]}"
fi
if [[ "$MODE" != supplementary && "$MODE" != rebuild && "$MODE" != repair ]]; then
  echo 'Usage: run_all.sh [supplementary [glm|deepseek]|rebuild|repair]. supplementary runs only the two new experiments.' >&2
  exit 2
fi
if [[ "$MODE" == rebuild ]]; then
  "$PY" -m experiments.revision2026 preflight --historical
elif [[ "$MODE" == repair ]]; then
  : "${LEGACY_ARCHIVE:?Set LEGACY_ARCHIVE to the extracted original archive}"
  "$PY" -m experiments.revision2026 preflight
else
  "$PY" -m experiments.revision2026 preflight "${MODEL_ARGS[@]}"
fi
if [[ -n "${LEGACY_ARCHIVE:-}" ]]; then
  "$PY" -m experiments.revision2026 repair-plan --archive "$LEGACY_ARCHIVE" --output "$RUN_ROOT/legacy-audit"
fi
"$PY" -m experiments.revision2026 pilot --run-dir "$RUN_ROOT/pilot" --workers "$WORKERS" "${MODEL_ARGS[@]}"
"$PY" -m experiments.revision2026 generalization --run-dir "$RUN_ROOT/generalization" --workers "$WORKERS" "${MODEL_ARGS[@]}"
"$PY" -m experiments.revision2026 ablation --run-dir "$RUN_ROOT/ablation" --workers "$WORKERS" "${MODEL_ARGS[@]}"
if [[ "$MODE" == rebuild ]]; then
  "$PY" -m experiments.revision2026 table2-rebuild --run-dir "$RUN_ROOT/table2-fresh" --workers "$WORKERS"
elif [[ "$MODE" == repair ]]; then
  "$PY" -m experiments.revision2026 repair-run --plan "$RUN_ROOT/legacy-audit/repair_plan.json" --run-dir "$RUN_ROOT/table2-repair" --workers "$WORKERS"
fi
echo "Completed planned execution. Inspect coverage, blocked_jobs.json and report/ in $RUN_ROOT; missing rows are not passes."
