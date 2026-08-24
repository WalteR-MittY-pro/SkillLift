#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run the full SkillLift baseline serially with task-declared skills.

Usage:
  scripts/run_skilllift_baseline.sh [--resume-skip-existing]

Environment overrides:
  MODELS_CONFIG Models config path. Default: models_config_openclaw.json
  THINKING       OpenClaw thinking level. Default: high
  OUTPUT_SUBDIR  Output directory under repo root. Default: skilllift_baseline

The script intentionally does not pass --skill-dir. eval/run_batch.py will load
each task's own "## Skills" declaration from the repository skills/ directory.
The model and retry settings are resolved from MODELS_CONFIG.

Options:
  --resume-skip-existing  Skip tasks that already have output under OUTPUT_SUBDIR.
EOF
}

main() {
  local resume_skip_existing=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        usage
        exit 0
        ;;
      --resume-skip-existing)
        resume_skip_existing=1
        shift
        ;;
      *)
        echo "Unknown argument: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
  done

  local script_dir root_dir wildclaw_root model models_config thinking output_subdir task_count
  local rate_limit_retries rate_limit_wait_seconds
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  root_dir="$(cd "${script_dir}/.." && pwd)"
  cd "${root_dir}"
  wildclaw_root="${WILDCLAWBENCH_ROOT:-${root_dir}/../WildClawBench}"

  models_config="${MODELS_CONFIG:-models_config_openclaw.json}"
  thinking="${THINKING:-high}"
  output_subdir="${OUTPUT_SUBDIR:-skilllift_baseline}"

  [[ -d "${wildclaw_root}/tasks" ]] || { echo "Missing external WildClawBench tasks/ directory" >&2; exit 1; }
  [[ -d "${wildclaw_root}/skills" ]] || { echo "Missing external WildClawBench skills/ directory" >&2; exit 1; }
  [[ -f eval/run_batch.py ]] || { echo "Missing eval/run_batch.py" >&2; exit 1; }
  [[ -f "${models_config}" ]] || { echo "Missing models config: ${models_config}" >&2; exit 1; }

  model="$(python3 - <<'PY' "${models_config}"
from pathlib import Path
import sys
from skilllift.model_config import load_model_config
config = load_model_config(Path(sys.argv[1]))
print(config.model_for("openclaw_agent") or config.model_for("oracle"))
PY
)"
  rate_limit_retries="$(python3 - <<'PY' "${models_config}"
from pathlib import Path
import sys
from skilllift.model_config import load_model_config
print(load_model_config(Path(sys.argv[1])).runtime_int("oracle_rate_limit_retries", 2))
PY
)"
  rate_limit_wait_seconds="$(python3 - <<'PY' "${models_config}"
from pathlib import Path
import sys
from skilllift.model_config import load_model_config
print(load_model_config(Path(sys.argv[1])).runtime_float("oracle_rate_limit_wait_seconds", 120.0))
PY
)"
  [[ -n "${model}" ]] || { echo "No openclaw_agent/oracle model configured in ${models_config}" >&2; exit 1; }

  task_count="$(find "${wildclaw_root}/tasks" -mindepth 2 -maxdepth 2 -type f -name '*task_*.md' | wc -l | tr -d ' ')"
  mkdir -p "${output_subdir}/logs"

  local -a cmd=(
    python3 eval/run_batch.py
    --category all
    --parallel 1
    --model "${model}"
    --models-config "${models_config}"
    --thinking "${thinking}"
    --rate-limit-retries "${rate_limit_retries}"
    --rate-limit-wait-seconds "${rate_limit_wait_seconds}"
  )
  if [[ "${resume_skip_existing}" -eq 1 ]]; then
    cmd+=(--resume-skip-existing)
  fi

  echo "SkillLift baseline"
  echo "  repo:          ${root_dir}"
  echo "  benchmark:     ${wildclaw_root}"
  echo "  tasks:         ${task_count}"
  echo "  output:        ${root_dir}/${output_subdir}"
  echo "  model:         ${model}"
  echo "  models_config: ${models_config}"
  echo "  skills:        task-declared skills from ${wildclaw_root}/skills"
  echo "  parallelism:   1"
  echo "  rate retries:  ${rate_limit_retries} (wait ${rate_limit_wait_seconds}s)"
  echo "  resume skip:   ${resume_skip_existing}"
  echo

  if ! docker info >/dev/null 2>&1; then
    echo "Docker is not running or not reachable." >&2
    exit 1
  fi

  local log_file
  log_file="${output_subdir}/logs/run_$(date +%Y%m%d_%H%M%S).log"
  echo "Log: ${root_dir}/${log_file}"
  echo

  OUTPUT_SUBDIR="${output_subdir}" "${cmd[@]}" 2>&1 | tee "${log_file}"
}

main "$@"
