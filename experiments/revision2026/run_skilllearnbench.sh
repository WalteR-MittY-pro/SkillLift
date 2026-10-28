#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
MODEL="${1:?Usage: run_skilllearnbench.sh deepseek|glm [generalization|pilot] [all|train|test]}"
MODE="${2:-generalization}"
STAGE="${3:-all}"
case "$MODEL" in
  deepseek) WORKERS=2 ;;
  glm) WORKERS=2 ;;
  *) echo 'Model must be deepseek or glm' >&2; exit 2 ;;
esac
[[ "$MODE" == generalization || "$MODE" == pilot ]] || { echo 'Invalid mode' >&2; exit 2; }
[[ "$STAGE" == all || "$STAGE" == train || "$STAGE" == test ]] || { echo 'Invalid stage' >&2; exit 2; }
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    PY=.venv-revision2026-windows/Scripts/python.exe
    [[ -x "$PY" ]] || PY=.venv-revision2026/Scripts/python.exe
    ;;
  *) PY=.venv-revision2026/bin/python ;;
esac
[[ -x "$PY" ]] || { echo 'Missing .venv-revision2026; run bash experiments/revision2026/setup.sh skilllearnbench first.' >&2; exit 2; }
if [[ "${PLAN_ONLY:-0}" != 1 ]]; then
  # Docker Desktop WSL Integration provides the Linux CLI and default socket.
  # Use that supported connection instead of a private shared-socket path.
  docker info --format 'Experiment Docker daemon: {{.Name}} ({{.ID}})'
fi
RUN_DIR="${RUN_ROOT:-runs/skilllearnbench-thinking-32768-v1}/$MODEL/$MODE"
EXTRA=()
[[ "${PLAN_ONLY:-0}" != 1 ]] || EXTRA=(--plan-only)
echo "SkillLearnBench: model=$MODEL workers=$WORKERS mode=$MODE output_tokens=32768 thinking=enabled stream=false"
echo "Run directory: $RUN_DIR (reuse this directory to resume)"
"$PY" -X utf8 -m experiments.revision2026 "$MODE" --models "$MODEL" --workers "$WORKERS" \
  --protocol experiments/revision2026/protocol.skilllearnbench-thinking-32768.json \
  --run-dir "$RUN_DIR" --stage "$STAGE" --require-cached-images "${EXTRA[@]}"
if [[ "${PLAN_ONLY:-0}" != 1 ]]; then
  "$PY" -X utf8 -c 'import json,sys; from pathlib import Path; s=json.loads((Path(sys.argv[1])/"execution_summary.json").read_text()); sys.exit(0 if s["status"]=="complete" else "Jobs remain incomplete; inspect logs and rerun the same command.")' "$RUN_DIR"
fi
