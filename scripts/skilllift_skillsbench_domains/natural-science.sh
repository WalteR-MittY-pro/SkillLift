#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    exec "${ROOT}/scripts/skilllift_skillsbench_domain.sh" --help
fi
phase="${1:-train}"
[[ $# -eq 0 ]] || shift
SKILLLIFT_SKILLSBENCH_CONFIG="${ROOT}/configs/skilllift_skillsbench_domains/natural-science.yaml" \
    exec "${ROOT}/scripts/skilllift_skillsbench_domain.sh" natural-science "${phase}" \
    "runs/skilllift_skillsbench_natural-science_gpt54mini_v1" "$@"
