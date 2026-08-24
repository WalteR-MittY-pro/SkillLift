---
name: 02-code-intelligence-task-9-link-a-pix-color-easy-zh
description: Use when solving grid puzzles with structured data input available. Focuses on leveraging provided data files over visual extraction, constraint-satisfaction pathfinding, and cross-modal verification.
---

# Structured-Data Puzzle Solving with Visual Verification

## Core Challenge

When a puzzle provides structured data alongside the visual image, the challenge shifts from extraction accuracy to solver correctness and cross-modal verification. The structured data eliminates OCR errors but introduces a different risk: trusting the data without understanding its semantics. The solver must correctly interpret the data schema, implement the puzzle's pathfinding constraints faithfully, and use the original image to verify and describe the result.

## Solution Strategy

1. **Prefer structured data over visual extraction**: When structured puzzle data is available, use it as the primary input. It eliminates number-misreading, color-confusion, and position-precision errors. Read the data schema carefully — understand what each field represents before using it. Common mistake: agents ignore the provided data file and attempt visual OCR, introducing unnecessary errors.

2. **Validate data against the image**: Cross-check a few clues from the structured data against the visual image. Do the positions, values, and colors match? This catches data format misunderstandings before they propagate through the entire solver. Common mistake: agents trust the data schema interpretation without visual spot-checks.

3. **Pair clues systematically before pathfinding**: Group clues by (color, value). Each group with value > 1 must have exactly two entries. Verify this invariant — if any group has 1 or 3+ entries, the data is either incomplete or misparsed. Handle value-1 clues as direct fills. Common mistake: agents start pathfinding without verifying pair structure, then debug "missing path" errors that are actually pairing failures.

4. **Implement exact-length BFS with obstacle avoidance**: For each pair, find a path of exactly `value` cells connecting them, moving only horizontally/vertically, without using cells already claimed by other paths. Process pairs in difficulty order (most constrained first). Backtrack globally when no valid path exists for any pair. Common mistake: agents use shortest-path algorithms that don't enforce the exact-length constraint.

5. **Use the image for result description, not for solving**: After generating the pixel art from the solver, use a VLM to analyze the filled image and describe what it depicts. The image is your verification and description tool, not your solving input. Common mistake: agents try to solve from the image even when structured data is available.

6. **Verify solver output against visual expectations**: After filling the grid, compare the colored output against the original puzzle image. Do the colored regions form coherent shapes? Are there unexpected gaps? This cross-modal check catches solver bugs that structured data alone won't reveal. Common mistake: agents trust the solver output without visual verification.

## Decision Points

- **When structured data conflicts with visual inspection**: Trust the structured data for solving (it's authoritative), but investigate the discrepancy — it may indicate a data format misunderstanding. If the data is clearly wrong (e.g., impossible positions), fall back to visual extraction.

- **Solver completeness vs partial results**: If the solver can't find a complete solution (some pairs unresolved), output the partial result. A partially filled pixel art may still be recognizable and scoreable. Don't discard partial work.

## Common Failure Patterns

- **Ignoring structured data**: Agents skip the provided data file and perform visual OCR, introducing errors that structured data was meant to prevent. → Lower accuracy than necessary, avoidable mistakes in positions or colors.

- **Misinterpreting data schema**: Agents assume field meanings without verification — e.g., treating row/col as x/y, or misreading color format (RGB list vs hex string). → Solver operates on wrong data, producing completely incorrect pixel art.

- **Shortest-path instead of exact-length**: Agents connect pairs with the shortest path rather than a path of exactly the specified length. → Paths too short, cells unfilled, pixel art incomplete and wrong.

- **Discarding partial solutions**: When the solver fails on some pairs, agents produce no output instead of saving the partial result. → Zero score when partial credit was possible.

- **Rendering at incorrect grid resolution**: Agents produce output at the original image resolution rather than grid-cell resolution. Each cell should be a uniform square in the pixel art. → Distorted pixel art that doesn't match the expected visual proportions.

## Self-Check Questions

- [ ] Did I use the provided structured data as primary input instead of visual OCR?
- [ ] Did I validate a few data entries against the visual image?
- [ ] Did I verify every non-1 clue has exactly one pair partner?
- [ ] Does my pathfinding enforce exact cell-count paths, not shortest paths?
- [ ] Did I check that no paths cross or reuse cells?
- [ ] Did I produce output even if the solution is only partial?
- [ ] Did I use the visual image to describe the result, not to solve the puzzle?
- [ ] Did I verify solver output visually against the original puzzle image?
- [ ] Did I describe the resulting pixel art based on what the colored cells depict?
- [ ] Did I verify the data schema interpretation (row vs col, color format) with visual spot-checks?

## Technical Notes

- **Coordinate convention in structured data**: Verify whether the data uses (row, col) or (x, y) ordering. In image coordinates, x is horizontal (column) and y is vertical (row). If the data provides (row, col), map carefully: row corresponds to y-axis, col to x-axis.
- **Color format handling**: Structured data may provide colors as RGB lists, hex strings, or named colors. Normalize to a single format (e.g., RGB tuples) before rendering. Verify the color values match the visual image by spot-checking.
