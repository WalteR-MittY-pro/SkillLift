#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${ROOT}/skillsbench/.venv/bin/python"
MODELS=(gpt glm deepseek)
DOMAINS=(
    cybersecurity
    finance-economics
    industrial-physical-systems
    mathematics-or-formal-reasoning
    media-content-production
    natural-science
    office-white-collar
    software-engineering
)

model=""
domain=""
phase="evolve"
baseline="skilllift"
run_base="runs/skillsbench"
run_all=false
dry_run=false

usage() {
    cat <<'EOF'
Usage:
  scripts/run_skillsbench.sh --model <gpt|glm|deepseek> --domain <domain> [options]
  scripts/run_skillsbench.sh --all [options]

Options:
  --baseline <name>       skilllift or coevoskills (default: skilllift)
  --phase <check|evolve>  Default: evolve
  --run-base <path>       Default: runs/skillsbench
  --dry-run               Print cells and commands without executing
  --help
EOF
}

contains() {
    local expected="$1"
    shift
    local item
    for item in "$@"; do
        [[ "${item}" == "${expected}" ]] && return 0
    done
    return 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --model) model="${2:-}"; shift 2 ;;
        --domain) domain="${2:-}"; shift 2 ;;
        --baseline) baseline="${2:-}"; shift 2 ;;
        --phase) phase="${2:-}"; shift 2 ;;
        --run-base) run_base="${2:-}"; shift 2 ;;
        --all) run_all=true; shift ;;
        --dry-run) dry_run=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ "${phase}" != "check" && "${phase}" != "evolve" ]]; then
    echo "invalid phase: ${phase}" >&2
    exit 2
fi
if [[ "${baseline}" != "skilllift" && "${baseline}" != "coevoskills" ]]; then
    echo "invalid baseline: ${baseline}" >&2
    exit 2
fi

if [[ "${run_all}" == true ]]; then
    if [[ -n "${model}" || -n "${domain}" ]]; then
        echo "--all cannot be combined with --model or --domain" >&2
        exit 2
    fi
    selected_models=("${MODELS[@]}")
    selected_domains=("${DOMAINS[@]}")
else
    if [[ -z "${model}" || -z "${domain}" ]]; then
        echo "single-cell mode requires --model and --domain" >&2
        usage >&2
        exit 2
    fi
    contains "${model}" "${MODELS[@]}" || { echo "invalid model: ${model}" >&2; exit 2; }
    contains "${domain}" "${DOMAINS[@]}" || { echo "invalid domain: ${domain}" >&2; exit 2; }
    selected_models=("${model}")
    selected_domains=("${domain}")
fi

if [[ "${dry_run}" == false ]]; then
    if [[ ! -x "${PYTHON}" ]]; then
        echo "missing ${PYTHON}; create a skillsbench virtualenv first (see repo README)" >&2
        exit 1
    fi
    if [[ -f "${ROOT}/.env" ]]; then
        # Snapshot env vars already set so .env doesn't clobber caller overrides.
        # Lets e.g. run_skillsbench_gpt2.sh swap in GPT_API_KEY2 before calling us.
        _env_overrides=""
        while IFS= read -r _line; do
            case "$_line" in ''|\#*) continue ;; esac
            _k="${_line%%=*}"
            [[ "$_k" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
            [[ -n "${!_k:-}" ]] && _env_overrides+="${_k}=${!_k}"$'\n'
        done < "${ROOT}/.env"
        set -a
        # shellcheck disable=SC1091
        source "${ROOT}/.env"
        set +a
        # Re-apply env-level overrides (env takes precedence over .env file)
        if [[ -n "$_env_overrides" ]]; then
            while IFS='=' read -r _k _v; do
                [[ -n "$_k" ]] && export "$_k=$_v"
            done <<< "$_env_overrides"
        fi
        unset _env_overrides _k _v _line
    fi
    if [[ "${phase}" == "evolve" ]]; then
        command -v docker >/dev/null 2>&1 || { echo "docker is required for evolve" >&2; exit 1; }
        docker info >/dev/null 2>&1 || { echo "docker daemon is not running" >&2; exit 1; }
        for selected_model in "${selected_models[@]}"; do
            prefix=$(printf '%s' "${selected_model}" | tr '[:lower:]' '[:upper:]')
            for suffix in MODEL BASE_URL API_KEY; do
                variable="${prefix}_${suffix}"
                if [[ -z "${!variable:-}" ]]; then
                    echo "missing ${variable}; copy .env.example to .env and configure it" >&2
                    exit 1
                fi
            done
        done
    fi
fi

cd "${ROOT}"
for selected_model in "${selected_models[@]}"; do
    for selected_domain in "${selected_domains[@]}"; do
        if [[ "${baseline}" == "skilllift" ]]; then
            run_root="${run_base}/${selected_model}/${selected_domain}"
        else
            run_root="${run_base}/${baseline}/${selected_model}/${selected_domain}"
        fi
        command=(
            "${PYTHON}" scripts/skilllift_skillsbench_tasks.py
            --baseline "${baseline}"
            --model "${selected_model}"
            --domain "${selected_domain}"
            --run-root "${run_root}"
            --phase "${phase}"
            --resume
        )
        echo "CELL model=${selected_model} domain=${selected_domain} baseline=${baseline} run_root=${run_root}"
        if [[ "${dry_run}" == true ]]; then
            printf 'COMMAND'
            printf ' %q' "${command[@]}"
            printf '\n'
        else
            "${command[@]}"
        fi
    done
done
