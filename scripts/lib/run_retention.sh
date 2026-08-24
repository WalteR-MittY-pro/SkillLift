#!/usr/bin/env bash

validate_invalid_attempt_keep() {
  local keep="$1"
  if ! [[ "$keep" =~ ^[1-9][0-9]*$ ]]; then
    echo "INVALID_ATTEMPT_KEEP must be a positive integer: ${keep}" >&2
    return 2
  fi
}

prune_invalid_attempts_for_task() {
  local output_root="$1"
  local task_id="$2"
  local keep="$3"
  local task_archive="${output_root}/_invalid_attempts/${task_id}"
  local attempt
  local index=0

  [[ -d "$task_archive" ]] || return 0
  while IFS= read -r attempt; do
    [[ -n "$attempt" ]] || continue
    index=$((index + 1))
    if (( index > keep )); then
      echo "[prune-invalid] removing ${attempt}"
      rm -rf -- "$attempt"
    fi
  done < <(find "$task_archive" -mindepth 1 -maxdepth 1 -type d -print | LC_ALL=C sort -r)
}

prune_all_invalid_attempts() {
  local output_root="$1"
  local keep="$2"
  local task_archive

  validate_invalid_attempt_keep "$keep" || return
  [[ -d "${output_root}/_invalid_attempts" ]] || return 0
  while IFS= read -r task_archive; do
    [[ -n "$task_archive" ]] || continue
    prune_invalid_attempts_for_task "$output_root" "$(basename "$task_archive")" "$keep"
  done < <(find "${output_root}/_invalid_attempts" -mindepth 1 -maxdepth 1 -type d -print | LC_ALL=C sort)
}

archive_invalid_run() {
  local output_root="$1"
  local task_id="$2"
  local run_root="$3"
  local keep="$4"
  local attempt_id
  local archive_root

  attempt_id="$(date +%Y%m%d_%H%M%S)_${RANDOM}"
  archive_root="${output_root}/_invalid_attempts/${task_id}/${attempt_id}"
  mkdir -p "$(dirname "$archive_root")"
  mv "$run_root" "$archive_root"
  if [[ -f "${run_root}.log" ]]; then
    mv "${run_root}.log" "${archive_root}/run.log"
  fi
  echo "[archive-invalid] ${task_id} -> ${archive_root}"
  prune_invalid_attempts_for_task "$output_root" "$task_id" "$keep"
}
