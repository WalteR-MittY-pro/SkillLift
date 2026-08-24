---
name: 02-code-intelligence-task-2-sam3-debug
description: Use when debugging injected bugs in a codebase via error pattern analysis. Focuses on systematic fault localization from observable output anomalies and root-cause reasoning.
---

# Fault Localization from Output Anomaly Analysis

## Core Challenge

Debugging injected bugs requires reasoning backwards from observable output anomalies to the specific code lines causing them. Unlike real-world bugs, injected bugs are intentional and produce characteristic error patterns (coordinates negated, dimensions swapped, wrong activation functions). The challenge is mapping each anomaly class to its root cause without guessing.

## Solution Strategy

1. **Run first, diagnose from symptoms**: Execute the failing script and observe the actual output before reading any code. Categorize the anomalies: Are coordinates negative? Are width/height swapped? Are scores in wrong ranges? Each anomaly pattern points to specific code locations. Common mistake: agents start reading code randomly without first gathering diagnostic evidence.

2. **Map anomaly classes to code functions**: Coordinate errors implicate coordinate conversion functions. Score range errors implicate activation functions or normalization code. Dimension swaps implicate parameter ordering. Build this mapping before diving into any single function. Common mistake: agents read code linearly from top to bottom instead of targeting the functions implicated by the anomaly pattern.

3. **Fix one bug at a time, re-run after each**: After identifying and fixing a bug, re-run the script to confirm the fix works and to surface the next anomaly (if any). Multiple bugs can mask each other. Common mistake: agents fix all suspected bugs at once and can't tell which fix worked or which introduced new issues.

4. **Read the math, not the comments**: Injected bugs are in numerical logic — numerator/denominator swaps, wrong exponents, incorrect activation functions. Read the actual arithmetic in each function. Don't trust function names or docstrings. Common mistake: agents read function signatures and assume correctness because the name matches the purpose.

5. **Preserve unrelated code**: Only modify the lines with actual bugs. Don't refactor, "improve," or restructure working code — this introduces new failures. Common mistake: agents clean up surrounding code while fixing bugs, creating cascading issues.

6. **Confirm each fix with a re-run, not by reasoning**: After making a change, execute the script and check whether the specific anomaly you targeted is resolved. Don't assume the fix works based on code analysis alone — runtime behavior is the only proof. Common mistake: agents reason "this should fix it" without running, then discover the anomaly persists.

## Decision Points

- **Which functions to inspect first**: Prioritize by anomaly type. Coordinate anomalies → box conversion functions. Score anomalies → activation/sigmoid functions. Detection count anomalies → threshold or NMS logic. Missing detections → prompt processing functions.

- **When a fix doesn't resolve the anomaly**: If the anomaly persists after a fix, either the fix was wrong or there's a second bug in the same pipeline. Re-examine the anomaly pattern for additional error signatures before trying a different fix.

- **Whether to inspect test script or library code**: The constraint "don't modify the test script" also means "don't waste time reading it for bugs." Focus all debugging effort on library code. The test script is correct by definition.

## Common Failure Patterns

- **Shotgun debugging**: Agents change multiple things at once hoping something works, without understanding root causes. → Fixes that work by coincidence, new bugs introduced, impossible to reason about.

- **Trusting function names over implementation**: A function named `sigmoid` might contain a different activation. A function named `normalize_box` might have the normalization formula inverted. Agents read the name and move on. → Bugs remain unfixed because the real defect is inside a "correctly named" function.

- **Ignoring constraint to not modify the test script**: When told not to modify the driver script, some agents modify it anyway to work around bugs. → The evaluation validates the library code, so test script changes don't fix the actual defects.

- **Stopping at first fix**: Agents fix one obvious bug and assume the task is complete, missing additional injected defects that produce subtler anomalies. → Partial scores when multiple bugs each independently affect different test cases.

- **Fixing symptoms instead of root causes**: Agents add clamping or post-processing to mask wrong outputs (e.g., clipping negative coordinates) instead of finding the function that produces them. → Outputs look correct in some cases but fail edge cases.

## Self-Check Questions

- [ ] Did I run the script first and catalog ALL observable output anomalies before reading code?
- [ ] Did I map each anomaly type to the specific code functions it implicates?
- [ ] Did I read the actual arithmetic in suspected functions, not just their names?
- [ ] Did I fix bugs one at a time and re-run after each fix?
- [ ] Did I verify that outputs are fully correct, not just "less wrong"?
- [ ] Did I avoid modifying any files I was told not to touch?
- [ ] Did I re-run the script after each fix rather than assuming it works?
- [ ] Did I verify that ALL test cases pass, not just the ones affected by the first bug?
- [ ] Did I confirm the fix addresses root cause, not just symptom masking?

## Technical Notes

- **Common injected bug patterns**: Numerator/denominator swaps in IoU-like functions, width/height parameter swaps in coordinate converters, sigmoid replaced with tanh or raw output, clamp/truncate operations with inverted bounds. When you see coordinate values outside expected pixel ranges, check for these patterns first.
