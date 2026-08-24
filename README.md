# SkillLift

**An evaluation framework for self-evolving agent skills.**

SkillLift runs skill-evolution methods against three agent benchmarks — [WildClawBench](https://github.com/InternLM/WildClawBench) (60 containerized end-to-end tasks), [tau2-bench](https://github.com/WalteR-MittY-pro/tau2-bench) (airline / retail / telecom), and SkillsBench (87 tasks across 8 domains) — under matched context and token budgets, with fully auditable run records.

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)]
[![Benchmarks](https://img.shields.io/badge/benchmarks-WildClawBench%20%C2%B7%20%CF%842--bench%20%C2%B7%20SkillsBench-blueviolet)]

## How it works

The flagship algorithm (`skilllift`) is the **SkillLift portfolio coordinator**. Each task starts from the seed skill portfolio the benchmark task already declares. The coordinator then runs a locked protocol: an anchor evaluation of the seed, up to three rounds of controlled candidate generation — each candidate is a validated unified-diff patch proposed by a Rubricator planner and applied through the bounded-edits engine — and two final trials per accepted portfolio.

- **Mode A** refines candidate branches with a rubric verifier using zero oracle calls, accepting a refinement only when the verifier score is non-decreasing.
- **Mode B** runs the real-environment oracle once per round, compares the verifier's ranking against the oracle's ground-truth ranking, and has the Rubricator revise the signed rubric receipt until rank alignment passes threshold.

The same CLI runs the baselines (`no_skill`, `human_skill`, `autoskill`, `textgrad`, `skilllift`, `coevoskills`) so every method is compared under identical budgets.

## Requirements

- Python **3.11+** (tested on 3.13)
- Docker (WildClawBench tasks run one isolated container per task)
- Git submodules for the benchmarks

## Setup

```bash
git clone --recurse-submodules <this-repo-url>
cd SkillLift

python -m venv .venv && source .venv/bin/activate
pip install -r skilllift/requirements.txt
pip install -e skilllift/
pip install -e packages/bounded-edits

cp .env.example .env   # then fill in your endpoints and keys
```

> tau2-bench runs additionally read `tau2-bench/.env` — create it with the same
> provider/judge variables you plan to use for tau2 evaluation.

All model endpoints are environment-driven. Each provider needs a triplet, e.g. `GLM_MODEL` / `GLM_BASE_URL` / `GLM_API_KEY` (see `.env.example` for the full list: `GPT_*`, `CLAUDE_*`, `GLM_*`, `QWEN_*`, `DEEPSEEK_*`, `MINIMAX_*`). Endpoint profiles live in [`skilllift_eval/config.yaml`](skilllift_eval/config.yaml).

## Quickstart

Run the SkillLift (`skilllift`) pipeline on WildClawBench:

```bash
# single task
python -m skilllift_eval.cli run skilllift \
    --benchmark wildclawbench --model glm-5.1 \
    --config skilllift_eval/config.yaml --tasks.mode single --tasks.filter <task-id>

# or the batch driver (all six categories, resumable)
python scripts/skilllift_wildclaw_tasks.py --model glm-5.1 --run-root runs/skilllift_wildclaw_tasks --resume
```

Run a baseline on tau2-bench:

```bash
python -m skilllift_eval.cli run no_skill \
    --benchmark tau2 --model glm-5.1 \
    --config skilllift_eval/config.yaml --tau2.domains airline,retail,telecom
```

SkillsBench (requires `skillsbench/.venv` and Docker):

```bash
scripts/run_skillsbench.sh --model glm --domain software-engineering --baseline skilllift
```

Useful flags: `--dry-run`, `--evaluation-mode native_end_to_end|budget_matched`, `--param-profile paper_default`, `--algo-param k=v`. Audit a finished run with `python -m skilllift_eval.cli audit <run_root>`.

## Project structure

| Path | What it is |
|---|---|
| `skilllift_eval/` | Evaluation framework: CLI, runners for every baseline × benchmark pair, resume, token accounting |
| `skilllift/skilllift/` | Algorithm core: portfolio coordinator, SkillLift Modes A/B, rubricator, verifier, oracle adapters |
| `packages/bounded-edits/` | Constrained unified-diff edit engine used for all portfolio patches |
| `configs/` | Domain configs for SkillsBench runs |
| `scripts/` | Batch drivers and per-benchmark launch scripts |
| `WildClawBench/`, `tau2-bench/`, `skillsbench/` | Benchmark submodules (tasks, skills, graders) |
| `skilllift/README.md` | Deep dive into the co-evolution loop, knobs, and artifacts |

## Tests

```bash
python -m pytest tests/skilllift_eval        # framework + runner contracts
cd skilllift && python -m pytest tests   # algorithm core
python -m pytest packages/bounded-edits/tests  # edit engine
```

## License

[MIT](LICENSE) — WildClawBench benchmark assets keep their upstream licenses in the respective submodules.
