# SkillLift

SkillLift is a rubric-guided skill self-evolution framework, evaluated on the
WildClawBench benchmark. This glossary fixes the shared language for the
framework, the benchmark, and the verification runs that compare new results
against the README's claims.

## Language

### Benchmark

**WildClawBench**:
A bilingual, multimodal agent benchmark: 60 tasks in 6 categories, each executed
by a real OpenClaw CLI agent inside a Docker container.
_Avoid_: wild claw bench, WildClaw bench

**Task**:
One benchmark unit: a markdown file with a prompt, a workspace, pre-provisioned
skills, and an embedded `grade()` function that scores the agent's transcript.
_Avoid_: experiment (an experiment is a full run over tasks, not one task)

**Category**:
One of the six WildClawBench groups — Productivity Flow (10), Code
Intelligence (12), Social Interaction (6), Search & Retrieval (11), Creative
Synthesis (11), Safety Alignment (10).

**Oracle**:
The authoritative per-rollout score: the task's scripted `grade()` function run
inside the task container. Never fabricated; a failed run reports null.

**Judge**:
The LLM role available inside the task container for grading steps that need a
model (e.g. VLM checks in Creative Synthesis tasks). Driven by the same
endpoint as the evaluated model, not by an external judge service.

### Skills

**Native skill**:
A skill pre-provisioned by WildClawBench, declared in a task's `## Skills`
section and loaded from the benchmark's own skills directory.

**Seed portfolio**:
The set of native skills a task declares; the starting point for every
evolving method on that task.

**Evolved skill**:
A skill produced by an evolving method (SkillLift or a baseline) during a run.

**Human-written skill**:
The per-task reference skill shipped in this repo (`human_skill` baseline
column). Not part of the SkillLift seed portfolio.

### Runs

**Rollout**:
One full agent execution of a task in its container, producing a transcript,
usage, and an oracle score.

**Cell**:
One (model × benchmark × baseline × task) experiment unit, summarized by its
`cell_summary.json`.

**Run root**:
The directory holding all cells, records, usage ledgers, and manifests of one
invocation; resumable and fingerprint-guarded.

**Model endpoint profile**:
The env-driven configuration (model id, base URL, key) that every framework
role and the agent share for one `--model` label.

**Pass rate**:
The README's headline metric: mean over tasks of the per-task best evolved
skill's oracle score, as a percentage.
