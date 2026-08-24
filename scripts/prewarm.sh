#!/usr/bin/env bash
# Build missing SkillsBench task images while preserving existing prewarm tags.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLSBENCH_TASKS_DIR="${SKILLSBENCH_TASKS_DIR:-${ROOT}/skillsbench/tasks}"
PARALLEL="${PARALLEL:-3}"
MAX_RETRY="${MAX_RETRY:-3}"
FORCE="${FORCE:-0}"
FAILURES_FILE="${FAILURES_FILE:-${ROOT}/runs/prewarm_failed_tasks.txt}"

if [ ! -d "$SKILLSBENCH_TASKS_DIR" ]; then
  echo "SkillsBench tasks directory not found: $SKILLSBENCH_TASKS_DIR" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required" >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "docker daemon is not running" >&2
  exit 1
fi
case "$PARALLEL:$MAX_RETRY" in
  *[!0-9:]*|0:*|*:0) echo "PARALLEL and MAX_RETRY must be positive integers" >&2; exit 2 ;;
esac
case "$FORCE" in
  0|1) ;;
  *) echo "FORCE must be 0 or 1" >&2; exit 2 ;;
esac

image_exists() {
  docker image inspect "prewarm/${1}:prewarm" >/dev/null 2>&1
}

build_one() {
  local task="$1"
  local env_dir="$SKILLSBENCH_TASKS_DIR/$task/environment"
  local log_file="/tmp/prewarm_${task}.log"
  local started retry duration

  if [ ! -f "$env_dir/Dockerfile" ]; then
    echo "$task|NO_DOCKERFILE"
    printf '%s\n' "$task" >> "$FAILURES_FILE"
    return 1
  fi

  started=$(date +%s)
  for retry in $(seq 1 "$MAX_RETRY"); do
    if (cd "$env_dir" && docker build -t "prewarm/${task}:prewarm" . > "$log_file" 2>&1); then
      duration=$(( $(date +%s) - started ))
      echo "$task|SUCCESS|try=$retry|dur=${duration}s"
      return 0
    fi
    sleep 5
  done
  duration=$(( $(date +%s) - started ))
  echo "$task|FAILED_${MAX_RETRY}_RETRIES|dur=${duration}s|err=$(tail -2 "$log_file" | tr '\n' ' ' | cut -c1-120)"
  printf '%s\n' "$task" >> "$FAILURES_FILE"
  return 1
}

if [ "$#" -gt 0 ]; then
  tasks=("$@")
else
  tasks=()
  while IFS= read -r environment; do
    tasks+=("$(basename "$(dirname "$environment")")")
  done < <(find "$SKILLSBENCH_TASKS_DIR" -mindepth 2 -maxdepth 2 -type d -name environment | sort)
fi

requested=${#tasks[@]}
if [ "$requested" -eq 0 ]; then
  echo "No tasks to prewarm."
  exit 0
fi

pending=()
skipped=0
for task in "${tasks[@]}"; do
  if [ "$FORCE" -eq 0 ] && image_exists "$task"; then
    echo "$task|SKIPPED_EXISTING"
    skipped=$((skipped + 1))
  else
    pending+=("$task")
  fi
done

if [ "${#pending[@]}" -eq 0 ]; then
  echo "PREWARM_DONE requested=$requested built=0 skipped=$skipped failed=0"
  exit 0
fi

mkdir -p "$(dirname "$FAILURES_FILE")"
: > "$FAILURES_FILE"

active_jobs() {
  jobs -pr | wc -l | tr -d ' '
}

echo "PREWARM_START requested=$requested pending=${#pending[@]} skipped=$skipped parallel=$PARALLEL max_retry=$MAX_RETRY"
pids=()
for task in "${pending[@]}"; do
  while [ "$(active_jobs)" -ge "$PARALLEL" ]; do
    sleep 1
  done
  build_one "$task" &
  pids+=("$!")
done

for pid in "${pids[@]}"; do
  wait "$pid" 2>/dev/null || true
done

failed=0
if [ -s "$FAILURES_FILE" ]; then
  sort -u -o "$FAILURES_FILE" "$FAILURES_FILE"
  failed=$(wc -l < "$FAILURES_FILE" | tr -d ' ')
fi
built=$((${#pending[@]} - failed))
echo "PREWARM_DONE requested=$requested built=$built skipped=$skipped failed=$failed"

if [ "$failed" -gt 0 ]; then
  sed 's/^/  /' "$FAILURES_FILE"
  exit 1
fi
