---
name: 02-code-intelligence-task-8-link-a-pix-color-zh
description: Use when solving Link-a-Pix constraint-satisfaction puzzles from images. Focuses on visual grid extraction, paired-number matching, path-finding constraints, and pixel-art reconstruction.
---

# Constraint-Satisfaction Grid Puzzle Solving from Images

## Core Challenge

Link-a-Pix puzzles combine visual extraction (reading numbers, positions, and colors from an image) with constraint-satisfaction pathfinding (connecting same-color, same-value number pairs via paths of exact length, without crossing). The difficulty is two-fold: OCR must capture three attributes per cell (value, color, position), and the pathfinding must satisfy simultaneous constraints — path length equals the number value, paths don't cross, and each cell is used at most once.

## Solution Strategy

1. **Extract a complete clue table first**: Before solving anything, build a structured table of every numbered cell: (row, col, value, color). Group clues by (color, value) pairs — each pair must be connected. Verify every non-1 value appears exactly twice; if not, extraction has errors. Common mistake: agents start solving before verifying extraction completeness, then can't find pairs for orphaned clues.

2. **Handle "1" values as immediate fills**: A cell with value "1" is its own path — fill it with its color immediately. Remove these from the solving problem. This simplifies the constraint space. Common mistake: agents treat "1" cells as requiring pathfinding and waste computation.

3. **Match pairs before pathfinding**: For each (color, value) group, there are exactly two cells. Match them first, then find a path between them. The path constraint is: Manhattan distance between the two cells must be ≤ value, and the path must have exactly `value` cells (including both endpoints). Common mistake: agents try to solve all pairs simultaneously without first establishing which cells are paired.

4. **Use BFS with exact-length constraint**: For each pair, use BFS or DFS to find a path of exactly `value` cells from start to end, moving only horizontally or vertically, without reusing cells already claimed by other paths. Backtrack when no valid path exists for the current arrangement. Common mistake: agents use shortest-path algorithms which don't enforce exact path length.

5. **Process pairs in order of constraint difficulty**: Solve pairs with the least flexibility first — high-value pairs that are close together (tight path-length constraint) or pairs near grid boundaries. This reduces backtracking because constrained pairs have fewer options and fail fast if infeasible. Common mistake: agents process pairs in arbitrary order, increasing backtracking.

6. **Render the pixel art at correct grid resolution**: After solving all pairs, create a grid image where every cell covered by a path is filled with that path's color. Use the grid dimensions specified by the puzzle, not the image dimensions. Each cell is one square unit. Common mistake: agents render at image resolution rather than grid resolution, distorting the pixel art.

## Decision Points

- **When paths conflict**: If a path for one pair would cross an existing path, try an alternative route. If no alternative exists, backtrack to a previously solved pair and reroute it. The puzzle has a unique solution, so conflicts indicate a wrong earlier choice.

- **Color extraction precision**: Colors must be matched exactly — two clues that look "similar red" might be different colors requiring different pairs. Use exact RGB values from the image, not perceived color similarity.

## Common Failure Patterns

- **Imprecise color extraction**: Agents cluster colors into broad categories ("red," "blue") instead of using exact RGB values. Two distinct but similar colors get merged, causing wrong pair matching. → Paths connecting wrong cells, garbled pixel art.

- **Shortest-path instead of exact-length path**: Agents use Dijkstra or standard BFS which finds the shortest path, but Link-a-Pix requires paths of EXACTLY the specified length. → Paths that are too short, leaving cells unfilled.

- **Ignoring the no-crossing constraint**: Agents find valid-length paths but don't check whether they cross existing paths. → Overlapping fills that corrupt the pixel art.

- **Skipping extraction verification**: Agents trust OCR output without checking that every non-1 clue has exactly one partner. A single misread value or position creates an unsolvable puzzle. → Solver fails or produces wrong results with no clear error.

- **Rendering at wrong scale**: Agents produce an image at the original puzzle image's resolution instead of the grid's cell-based resolution. The pixel art looks stretched or compressed. → Recognizable pattern but visually incorrect proportions.

## Self-Check Questions

- [ ] Did I extract all clues with (row, col, value, color) and verify completeness?
- [ ] Did I use exact RGB values for color matching, not perceived similarity?
- [ ] Did I verify every non-1 value appears exactly twice per color?
- [ ] Did I handle "1" values as immediate cell fills?
- [ ] Does my pathfinding enforce exact path length, not just connectivity?
- [ ] Did I check that no paths cross or share cells?
- [ ] Did I fill all path cells with the pair's color to produce the pixel art?
- [ ] Did I render the pixel art at grid resolution (one square per cell)?
- [ ] Did I describe the resulting pixel art image based on what the colored cells depict?
- [ ] Did I verify no two paths share cells or cross each other?

## Technical Notes

- **Path length definition**: In Link-a-Pix, path length counts the number of CELLS in the path, including both endpoint cells. A path connecting two cells with value N must cover exactly N cells. The minimum path length between two cells at Manhattan distance D is D+1 (they must be D cells apart, plus the start cell).
- **Color extraction from anti-aliased images**: Number text in puzzle images may have anti-aliased edges that blend the number color with the background. Sample the color from the center of the number glyph, not the edges, to get the true clue color.
