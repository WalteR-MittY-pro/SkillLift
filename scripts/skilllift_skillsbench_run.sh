#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${ROOT}/skillsbench/.venv/bin/python"

cd "${ROOT}"

if [[ -f "${ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${ROOT}/.env"
    set +a
fi

export SKILLSBENCH_LLM_BASE_URL="${SKILLSBENCH_LLM_BASE_URL:-${GPT_BASE_URL:-}}"
export SKILLSBENCH_LLM_API_KEY="${SKILLSBENCH_LLM_API_KEY:-${GPT_API_KEY:-}}"

if [[ -z "${SSL_CERT_FILE:-}" ]]; then
    export SSL_CERT_FILE="$("${PYTHON}" -m certifi)"
fi

exec "${PYTHON}" scripts/skilllift_skillsbench_run.py "$@"
