#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${SKILLLIFT_SKILLSBENCH_CONFIG:-${ROOT}/configs/skilllift_skillsbench_all.yaml}"

usage() {
    cat <<'EOF'
Usage:
  scripts/skilllift_skillsbench_domain.sh <category|all> <check|train|test> <run-root> [extra CLI arguments]

Categories:
  cybersecurity
  finance-economics
  industrial-physical-systems
  mathematics-or-formal-reasoning
  media-content-production
  natural-science
  office-white-collar
  software-engineering

The model is read from the selected YAML config. Set SKILLLIFT_SKILLSBENCH_CONFIG
to use another config. The wrapper loads .env and maps GPT_BASE_URL/GPT_API_KEY
to SKILLSBENCH_LLM_BASE_URL/SKILLSBENCH_LLM_API_KEY.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi

if [[ $# -lt 3 ]]; then
    usage >&2
    exit 2
fi

category="$1"
phase="$2"
run_root="$3"
shift 3

case "${phase}" in
    check|train|test) ;;
    *)
        echo "invalid phase: ${phase}" >&2
        usage >&2
        exit 2
        ;;
esac

case "${category}" in
    all|cybersecurity|finance-economics|industrial-physical-systems|mathematics-or-formal-reasoning|media-content-production|natural-science|office-white-collar|software-engineering) ;;
    *)
        echo "invalid SkillsBench category: ${category}" >&2
        usage >&2
        exit 2
        ;;
esac

exec "${ROOT}/scripts/skilllift_skillsbench_run.sh" \
    --config "${CONFIG}" \
    --categories "${category}" \
    --phase "${phase}" \
    --run-root "${run_root}" \
    --resume \
    "$@"
