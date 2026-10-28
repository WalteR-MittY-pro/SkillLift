#!/usr/bin/env bash
# Build missing SkillsBench task images while preserving existing prewarm tags.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLSBENCH_TASKS_DIR="${SKILLSBENCH_TASKS_DIR:-${ROOT}/skillsbench/tasks}"
PARALLEL="${PARALLEL:-3}"
MAX_RETRY="${MAX_RETRY:-3}"
FORCE="${FORCE:-0}"
FAILURES_FILE="${FAILURES_FILE:-${ROOT}/runs/prewarm_failed_tasks.txt}"
PREWARM_APT_MIRROR_HOST="${PREWARM_APT_MIRROR_HOST:-}"
PREWARM_PYPI_INDEX_URL="${PREWARM_PYPI_INDEX_URL:-}"

if [ ! -d "$SKILLSBENCH_TASKS_DIR" ]; then
  echo "SkillsBench tasks directory not found: $SKILLSBENCH_TASKS_DIR" >&2
  echo "For revision2026 the pinned tasks are downloaded by experiments/revision2026/setup.sh (prepare); run it first." >&2
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
  local temp_dockerfile=""
  local -a dockerfile_args=()
  local -a mirror_args=()
  local started retry duration

  if [ ! -f "$env_dir/Dockerfile" ]; then
    echo "$task|NO_DOCKERFILE"
    printf '%s\n' "$task" >> "$FAILURES_FILE"
    return 1
  fi

  if [ -n "$PREWARM_APT_MIRROR_HOST" ] || [ -n "$PREWARM_PYPI_INDEX_URL" ]; then
    temp_dockerfile="${ROOT}/.cache/revision2026/prewarm-dockerfiles/${task}.Dockerfile"
    mkdir -p "$(dirname "$temp_dockerfile")"
    python3 - "$env_dir/Dockerfile" "$temp_dockerfile" "$PREWARM_APT_MIRROR_HOST" "$PREWARM_PYPI_INDEX_URL" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
mirror = sys.argv[3]
pypi_index = sys.argv[4].rstrip("/")
text = source.read_text(encoding="utf-8")
if mirror:
    for old, new in (
        ("http://mirrors.aliyun.com/ubuntu-ports", f"http://{mirror}/ubuntu-ports"),
        ("http://mirrors.aliyun.com/ubuntu", f"http://{mirror}/ubuntu"),
        ("http://mirrors.aliyun.com/debian-security", f"http://{mirror}/debian-security"),
        ("http://mirrors.aliyun.com/debian", f"http://{mirror}/debian"),
    ):
        text = text.replace(old, new)
if pypi_index:
    for scheme in ("http", "https"):
        text = text.replace(f"{scheme}://mirrors.aliyun.com/pypi/simple", pypi_index)

# Task Dockerfiles add three fallback mirror copies. Omitting them from this
# disposable Dockerfile avoids downloading the same package indexes repeatedly.
lines = []
skipping_fallback = False
for line in text.splitlines():
    if mirror and line.strip() == "# Fallback mirrors (apt will try next mirror on failure)":
        lines.append("    : # prewarm uses the selected apt mirror")
        skipping_fallback = True
        continue
    if skipping_fallback:
        if line.strip() == "fi":
            skipping_fallback = False
        continue
    lines.append(line)
destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
    dockerfile_args=(-f "$(wslpath -w "$temp_dockerfile")")
    if [ -n "$PREWARM_APT_MIRROR_HOST" ]; then
      mirror_args+=(--label "org.skilllift.prewarm-apt-mirror=${PREWARM_APT_MIRROR_HOST}")
    fi
    if [ -n "$PREWARM_PYPI_INDEX_URL" ]; then
      mirror_args+=(--label "org.skilllift.prewarm-pypi-index=${PREWARM_PYPI_INDEX_URL}")
    fi
  fi

  started=$(date +%s)
  for retry in $(seq 1 "$MAX_RETRY"); do
    # shellcheck disable=SC2086  # PREWARM_BUILD_ARGS is intentionally word-split (e.g. proxy --build-arg pairs)
    if (cd "$env_dir" && docker build ${PREWARM_BUILD_ARGS:-} "${dockerfile_args[@]}" \
      "${mirror_args[@]}" -t "prewarm/${task}:prewarm" . > "$log_file" 2>&1); then
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

echo "PREWARM_START requested=$requested pending=${#pending[@]} skipped=$skipped parallel=$PARALLEL max_retry=$MAX_RETRY apt_mirror=${PREWARM_APT_MIRROR_HOST:-default} pypi_index=${PREWARM_PYPI_INDEX_URL:-default}"
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
