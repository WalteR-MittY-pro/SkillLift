#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    exec "${ROOT}/scripts/skilllift_skillsbench_domain.sh" --help
fi
phase="${1:-train}"
[[ $# -eq 0 ]] || shift
SKILLLIFT_SKILLSBENCH_CONFIG="${ROOT}/configs/skillsbench/domains/mathematics-or-formal-reasoning.yaml" \
    exec "${ROOT}/scripts/skilllift_skillsbench_domain.sh" mathematics-or-formal-reasoning "${phase}" \
    "runs/skilllift_skillsbench_mathematics-or-formal-reasoning_gpt54mini_v1" "$@"
