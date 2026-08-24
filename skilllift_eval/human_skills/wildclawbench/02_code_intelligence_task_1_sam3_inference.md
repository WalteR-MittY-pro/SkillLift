---
name: 02-code-intelligence-task-1-sam3-inference
description: Use when writing inference scripts for undocumented ML codebases. Focuses on API reverse-engineering from source code, format inference, and coordinate system handling.
---

# Undocumented Codebase API Inference

## Core Challenge

Writing working code against an undocumented library requires reverse-engineering the correct API surface from source code alone. Function names and signatures are misleading without reading their implementations. The hardest part is inferring implicit contracts: what input formats methods expect, how outputs are structured, and where coordinate or normalization conversions happen internally.

## Solution Strategy

1. **Read implementation before trusting names**: A method named `set_box` might expect `[x, y, w, h]` or `[x1, y1, x2, y2]` — only the implementation tells you which. Read the function body, not just its signature. Common mistake: agents call APIs based on function names and parameter names, then discover format mismatches at runtime.

2. **Trace the data flow end-to-end**: Follow your input from the entry point through every transformation to the output. Map each conversion explicitly: where does normalization happen? Where are boxes converted between formats? Write this down. Common mistake: agents read the entry function but skip intermediate processing layers, missing silent transformations.

3. **Build incrementally with minimal test cases**: Get ONE simple case working before attempting complex ones. Print intermediate results at each stage to verify the data shape matches your mental model. Common mistake: agents write the full script for all cases at once, then can't isolate which step failed.

4. **Prioritize `__init__.py` and utility modules**: These files reveal the public API surface and common helpers. Box operation utilities, coordinate conversion functions, and processor classes are the key integration points. Common mistake: agents dive into model architecture code when they need the processor/predictor layer.

5. **Verify output format against consumers**: If the output is consumed by a downstream checker or comparison tool, understand its expected format before generating. A detection result might need `xyxy` when the internal API returns `xywh`. Common mistake: agents trust the API's native output format without converting to what the consumer expects.

6. **Match the inference device and runtime configuration**: Check whether the model expects CPU or GPU, what runtime backend is available, and whether weights need loading from a checkpoint path. Device mismatches cause silent fallbacks or hard crashes. Common mistake: agents assume GPU availability or skip weight loading steps that the model requires.

## Decision Points

- **When to use processor vs. raw model APIs**: If a processor/preprocessor class exists, prefer it — it handles normalization, resizing, and format conversion internally. Use raw model APIs only when the processor lacks a needed capability.

- **How to handle multiple prompt types**: When combining different prompt modalities (text + box, multiple boxes with labels), check whether the API supports them in a single call or requires sequential calls. Read the method signatures carefully.

- **Whether to normalize boxes before or after passing to API**: Some APIs normalize internally, others expect pre-normalized inputs. Check if the processor class handles normalization, and whether bypassing it skips required preprocessing.

## Common Failure Patterns

- **Assuming coordinate format from variable names**: `box` could be corner+size or corner pairs. `coords` could be pixel or normalized. Agents trust names and skip reading the arithmetic. → Silent coordinate errors that produce plausible-looking but wrong results.

- **Skipping normalization steps**: Some APIs expect pixel coordinates, others expect [0,1] normalized values. The conversion often happens inside a processor class that agents bypass. → Detection results scaled incorrectly, boxes in wrong positions.

- **One-shot scripting without intermediate verification**: Agents write the entire inference pipeline without checking intermediate outputs. When results are wrong, there's no way to localize the failure. → Hours of debugging what could be caught with a single print statement.

- **Ignoring confidence threshold parameters**: APIs often have default thresholds that differ from what's needed. Agents use defaults without checking. → Too many or too few detections relative to expected results.

- **Mishandling multi-prompt interactions**: When combining text prompts with box prompts, agents may pass them in wrong order, wrong format, or miss required flags (e.g., positive/negative labels). → Detection results that don't match expected outputs for combined prompt cases.

## Self-Check Questions

- [ ] Did I read the implementation of every API method I'm calling, not just its signature?
- [ ] Did I trace the full data flow from input to output, noting every transformation?
- [ ] Did I verify the coordinate format (xyxy vs xywh, pixel vs normalized) at each stage?
- [ ] Did I test the simplest case first before attempting complex multi-modal prompts?
- [ ] Did I print intermediate results to confirm data shapes match expectations?
- [ ] Did I check `__init__.py` and utility files for the public API surface?
- [ ] Did I run the script to completion and verify it produces valid output without errors?
- [ ] Did I confirm the output coordinate format matches what downstream consumers expect?

## Technical Notes

- **Box format ambiguity**: Detection APIs commonly use two formats: `[x, y, w, h]` (corner + dimensions) and `[x1, y1, x2, y2]` (two corners). Always verify which format a method expects by reading its implementation, especially any arithmetic on box coordinates.
- **Normalization mismatch**: Models often preprocess inputs to normalized [0-1] ranges internally. If you pass raw pixel coordinates as prompts, check whether the API converts them or expects pre-normalized values.
