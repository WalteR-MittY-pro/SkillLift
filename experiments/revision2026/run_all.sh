#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=.venv-revision2026/bin/python
MODE="${1:-rebuild}"
WORKERS="${WORKERS:-4}"
RUN_ROOT="${RUN_ROOT:-runs/revision2026}"
[[ -x "$PY" ]] || { echo 'Run bash experiments/revision2026/setup.sh inside WSL first.' >&2; exit 2; }
if [[ "$MODE" != rebuild && "$MODE" != repair ]]; then
  echo 'Usage: run_all.sh [rebuild|repair]. repair requires LEGACY_ARCHIVE and may leave unresolved Table 2 rows.' >&2
  exit 2
fi
if [[ "$MODE" == rebuild ]]; then
  "$PY" -m experiments.revision2026 preflight --historical
else
  : "${LEGACY_ARCHIVE:?Set LEGACY_ARCHIVE to the extracted original archive inside WSL}"
  "$PY" -m experiments.revision2026 preflight
fi
if [[ -n "${LEGACY_ARCHIVE:-}" ]]; then
  "$PY" -m experiments.revision2026 repair-plan --archive "$LEGACY_ARCHIVE" --output "$RUN_ROOT/legacy-audit"
fi
"$PY" -m experiments.revision2026 pilot --run-dir "$RUN_ROOT/pilot" --workers "$WORKERS"
"$PY" -m experiments.revision2026 generalization --run-dir "$RUN_ROOT/generalization" --workers "$WORKERS"
# Direct Oracle ablation now runs inside `generalization` (SkillLearnBench, method direct_oracle).
if [[ "$MODE" == rebuild ]]; then
  "$PY" -m experiments.revision2026 table2-rebuild --run-dir "$RUN_ROOT/table2-fresh" --workers "$WORKERS"
else
  "$PY" -m experiments.revision2026 repair-run --plan "$RUN_ROOT/legacy-audit/repair_plan.json" --run-dir "$RUN_ROOT/table2-repair" --workers "$WORKERS"
fi
echo "Completed planned execution. Inspect coverage, blocked_jobs.json and report/ in $RUN_ROOT; missing rows are not passes."
