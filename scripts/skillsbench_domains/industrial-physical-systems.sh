#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    exec "${ROOT}/scripts/skillsbench_domain.sh" --help
fi
phase="${1:-train}"
[[ $# -eq 0 ]] || shift
SKILLLIFT_SKILLSBENCH_CONFIG="${ROOT}/configs/skillsbench/domains/industrial-physical-systems.yaml" \
    exec "${ROOT}/scripts/skillsbench_domain.sh" industrial-physical-systems "${phase}" \
    "runs/skilllift_skillsbench_industrial-physical-systems_gpt54mini_v1" "$@"
