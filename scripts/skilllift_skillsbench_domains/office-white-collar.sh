#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    exec "${ROOT}/scripts/skilllift_skillsbench_domain.sh" --help
fi
phase="${1:-train}"
[[ $# -eq 0 ]] || shift
SKILLLIFT_SKILLSBENCH_CONFIG="${ROOT}/configs/skillsbench/domains/office-white-collar.yaml" \
    exec "${ROOT}/scripts/skilllift_skillsbench_domain.sh" office-white-collar "${phase}" \
    "runs/skilllift_skillsbench_office-white-collar_gpt54mini_v1" "$@"
