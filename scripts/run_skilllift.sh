#!/usr/bin/env bash

set -u
set -o pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$ROOT/scripts/lib/run_retention.sh"

OUTPUT_ROOT="runs/coevoskills-gpt5.4-full-priority-v2"
MAX_PASSES="${MAX_PASSES:-10}"
INVALID_ATTEMPT_KEEP="${INVALID_ATTEMPT_KEEP:-1}"
mkdir -p "$OUTPUT_ROOT/_invalid_attempts"

if ! [[ "$MAX_PASSES" =~ ^[1-9][0-9]*$ ]]; then
  echo "MAX_PASSES must be a positive integer: ${MAX_PASSES}" >&2
  exit 2
fi
validate_invalid_attempt_keep "$INVALID_ATTEMPT_KEEP" || exit $?
prune_all_invalid_attempts "$OUTPUT_ROOT" "$INVALID_ATTEMPT_KEEP"

cell_is_valid() {
  .venv/bin/python - "$1" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
valid = (
    payload.get("status") == "SUCCEEDED"
    and int(payload.get("succeeded_count") or 0) == 1
    and int(payload.get("evaluation_repeats") or 0) > 0
)
raise SystemExit(0 if valid else 1)
PY
}

all_tasks_valid() {
  local budget_file task_id summary
  for budget_file in runs/taskoutput-gpt5.4/*-*/token_usage.json; do
    task_id="$(basename "$(dirname "${budget_file}")")"
    summary="${OUTPUT_ROOT}/${task_id}/cell_summary.json"
    [[ -f "$summary" ]] && cell_is_valid "$summary" || return 1
  done
  return 0
}

for ((pass = 1; pass <= MAX_PASSES; pass++)); do
  echo "[pass] ${pass}/${MAX_PASSES}"
  for budget_file in runs/taskoutput-gpt5.4/*-*/token_usage.json; do
  task_id="$(basename "$(dirname "${budget_file}")")"
  run_root="${OUTPUT_ROOT}/${task_id}"

  if [[ -f "${run_root}/cell_summary.json" ]] && cell_is_valid "${run_root}/cell_summary.json"; then
    echo "[skip] ${task_id}"
    continue
  fi

  resume=0
  if [[ -f "${run_root}/coevoskills_experiment/checkpoint.json" ]]; then
    resume=1
    echo "[resume] ${task_id}"
  elif [[ -d "${run_root}" ]]; then
    archive_invalid_run "$OUTPUT_ROOT" "$task_id" "$run_root" "$INVALID_ATTEMPT_KEEP"
  else
    echo "[run] ${task_id}"
  fi

  tee_append=""
  if (( resume )); then
    tee_append=1
  fi

  if ! .venv/bin/python -m skilllift_eval.cli run coevoskills \
    --benchmark wildclawbench \
    --model gpt-5.4 \
    --config skilllift_eval/config.yaml \
    --run-root "${run_root}" \
    --evaluation-mode native_end_to_end \
    --context-budget-profile native_default \
    --param-profile paper_default \
    --tasks.mode single \
    --tasks.filter "${task_id}" \
    2>&1 | tee ${tee_append:+-a} "${run_root}.log"; then
    echo "[failed] ${task_id}"
    fi
  done

  if all_tasks_valid; then
    echo "[complete] all tasks valid after pass ${pass}/${MAX_PASSES}"
    exit 0
  fi
  echo "[pass-complete] ${pass}/${MAX_PASSES}; incomplete tasks remain"
done

echo "[incomplete] exhausted ${MAX_PASSES} passes" >&2
exit 1
