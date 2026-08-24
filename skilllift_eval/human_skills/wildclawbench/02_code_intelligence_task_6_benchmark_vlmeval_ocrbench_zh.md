---
name: 02-code-intelligence-task-6-benchmark-vlmeval-ocrbench-zh
description: Use when reproducing ML benchmark evaluation results using known frameworks. Focuses on environment setup, framework configuration, reproducibility, and efficient evaluation execution.
---

# Benchmark Evaluation Reproduction

## Core Challenge

Reproducing a benchmark evaluation requires correctly configuring a complex framework with many moving parts: API endpoints, model identifiers, dataset versions, and reproducibility parameters. The challenge is navigating undocumented framework conventions, resolving dependency conflicts, and choosing settings that produce reproducible results — all while optimizing for execution speed since benchmark evaluation can be extremely time-consuming.

## Solution Strategy

1. **Understand the framework before configuring it**: Read the framework's documentation, README, and key scripts to understand how it structures evaluations. What are the entry points? How are models configured? How are datasets specified? This prevents misconfiguration that wastes hours of runtime. Common mistake: agents run commands blindly based on README snippets without understanding the configuration model.

2. **Verify dataset version explicitly**: Benchmarks often have multiple versions (v1, v2, updated variants). Verify which version the framework defaults to and whether it matches the target version. A wrong version produces plausible-looking but incorrect scores. Common mistake: agents assume the framework uses the "obvious" default and don't verify version compatibility.

3. **Configure for reproducibility first, speed second**: Set temperature to 0 (or framework minimum), disable sampling randomness, fix random seeds where applicable. Only after reproducibility is ensured should you optimize for speed. Common mistake: agents prioritize speed settings that introduce non-determinism, making results irreproducible.

4. **Resolve dependencies incrementally**: Install the framework, run it, and fix missing dependencies as they appear. Don't try to pre-install every possible dependency — frameworks often have optional dependencies that aren't needed for specific benchmarks. Common mistake: agents spend excessive time installing a complete dependency tree when only a subset is needed.

5. **Parse results carefully and format exactly**: Frameworks output results in their own format (nested JSON, per-sample logs, summary files). Understand the output structure, locate the specific metrics needed, and extract them into the required output format precisely. Integer vs float mismatches or wrong key names cause silent failures. Common mistake: agents copy the framework's output format directly without reformatting to match requirements.

6. **Use framework acceleration features**: Many evaluation frameworks support parallel inference, caching, or batch processing that can dramatically reduce wall-clock time. Explore available flags for concurrent API calls or sample-level caching. Even small speedups per sample compound across hundreds of evaluation items. Common mistake: agents run evaluation with default settings and wait hours when a simple flag could halve the time.

## Decision Points

- **API proxy vs direct API**: If the framework supports custom API endpoints, configure them to route through the provided proxy. Verify the proxy supports the required model identifier format. Direct API calls bypass framework evaluation logic.

- **Full evaluation vs subset for speed**: If time is critical, check whether the framework supports subset evaluation or sampling. However, be aware that subset results may not match full-benchmark scores. Prioritize full evaluation if feasible.

- **When to trust framework defaults vs override settings**: Framework defaults are usually safe for standard benchmarks, but always verify reproducibility settings (temperature, seed) and model configuration explicitly. Don't trust defaults for API endpoint and model naming.

## Common Failure Patterns

- **Wrong benchmark version**: Agents run evaluation on a newer or older version of the benchmark than intended. Scores differ significantly but look plausible. → Results outside expected range, no obvious error message.

- **Ignoring reproducibility settings**: Agents use default framework settings which may include non-zero temperature or stochastic sampling. → Results that vary between runs and don't match expected values.

- **Framework misconfiguration**: Agents set the wrong model name, API key, or data path. The framework runs "successfully" but evaluates the wrong model or dataset. → Results that are internally consistent but wrong.

- **Output format mismatch**: Agents copy framework output without reformatting. Key names, value types, or nesting structure don't match what the checker expects. → Valid evaluation results that score zero due to format incompatibility.

- **Running evaluation on the wrong dataset split**: Frameworks may offer train/test/validation splits or different difficulty levels. Agents use the default without verifying it matches the intended benchmark configuration. → Results that are valid but for the wrong evaluation subset.

## Self-Check Questions

- [ ] Did I verify I'm evaluating on the correct benchmark version (not a variant)?
- [ ] Did I configure temperature and sampling for reproducibility?
- [ ] Did I read the framework's evaluation script to understand how scores are computed?
- [ ] Did I verify the model identifier matches what the framework expects?
- [ ] Did I parse the framework's output format correctly and extract the right fields?
- [ ] Did I format the output to match the exact specification (key names, value types)?
- [ ] Did I check for framework acceleration options to reduce evaluation time?
- [ ] Did I verify the evaluation completed successfully (no silent crashes or truncated runs)?

## Technical Notes

- **Framework data paths**: Many evaluation frameworks expect datasets in specific locations or can use pre-downloaded data files. Providing local data files can avoid network downloads and significantly speed up evaluation setup.
- **API model naming**: Different frameworks use different model identifier conventions. The same model may be referenced by its API name, a framework-specific alias, or a path. Verify the framework resolves the identifier correctly by checking logs before the full evaluation runs.
