---
name: 02-code-intelligence-task-4-jigsaw-puzzle-medium-zh
description: Use when reassembling medium-difficulty jigsaw puzzles (larger grids with more distractors). Focuses on scalable edge-matching, combinatorial pruning, and global optimization under tighter time constraints.
---

# Scalable Jigsaw Reassembly with Combinatorial Optimization

## Core Challenge

As puzzle grids grow (4x4 and beyond), the combinatorial space of possible arrangements explodes. Greedy methods that worked for small grids break down because early mistakes compound across more positions. The challenge shifts from "can you match edges?" to "can you efficiently search the arrangement space without exhaustive enumeration?"

## Solution Strategy

1. **Pre-compute and cache all pairwise edge scores**: For N fragments with 4 orientations each, compute the full N×N compatibility matrix once and store it. Assembly then becomes pure lookup, not recomputation. This front-loads computation but makes the search phase dramatically faster. Common mistake: agents recompute edge matches during assembly, wasting time on repeated calculations.

2. **Prune distractors before assembly**: After computing the compatibility matrix, rank fragments by their best edge-match score across all neighbors. Fragments whose best match is significantly weaker than the population average are likely distractors. Filter them before searching for the grid arrangement. Common mistake: agents include all fragments in the assembly search, exponentially increasing the search space.

3. **Anchor from corner fragments**: Fragments with exactly two strong-matching edges (not four) are corner candidates. Identify the four corners first, then fill inward. This constrains the search space by fixing boundaries before interior placement. Common mistake: agents start from an arbitrary fragment and build outward, with no geometric anchoring.

4. **Use row-by-row or column-by-column assembly with backtracking**: Build the grid linearly (e.g., left-to-right, top-to-bottom), using edge compatibility to select each next fragment. When no fragment achieves a strong match, backtrack to the previous position and try alternatives. Limit backtracking depth to avoid exponential blowup. Common mistake: agents use greedy selection without backtracking, locking in early errors.

5. **Optimize for time efficiency**: Larger puzzles have strict time budgets. Prioritize the highest-discriminability comparisons first (strong edges with clear match/no-match gaps) and skip low-value comparisons. Use vectorized operations (NumPy) for batch edge comparisons rather than Python loops. Common mistake: agents write naive O(N²) loops in pure Python for large fragment sets.

6. **Validate grid consistency after assembly**: After filling the grid, check that every internal boundary (between adjacent cells) has a strong edge match. A single weak boundary indicates either a placement error or an undetected rotation. Use this as a final quality gate. Common mistake: agents accept the first complete grid without checking internal boundary quality.

## Decision Points

- **When to stop backtracking and accept a placement**: If the best available fragment at a position has a match score above a clear threshold (separated from the next-best by a significant gap), accept it. If scores are close, explore alternatives.

- **Vectorized vs per-pair computation**: For grids up to 5x5 with ~40 fragments, vectorized batch computation of all edge pairs is feasible and fast. Use NumPy array operations to compare all fragment edges simultaneously.

- **When to accept a placement vs backtrack**: If the best fragment at a position has a match score significantly above the next-best candidate (clear gap), accept it. If two candidates are nearly tied, explore both with limited-depth backtracking.

## Common Failure Patterns

- **Greedy placement without backtracking**: Agents place each fragment by best match and never reconsider. At 4x4+, one wrong placement cascades through 8+ subsequent positions. → Grid that starts correct but accumulates errors toward the end.

- **Exhaustive search timeout**: Agents attempt brute-force enumeration of all possible arrangements, which is infeasible for 4x4 (16! × 4^16 combinations). → Task timeout with no result produced.

- **Distractor contamination inflating search space**: Including 8+ distractors in the assembly search means the optimizer must reject them during search, increasing runtime exponentially. → Slow execution, suboptimal results.

- **Pure Python loops for pixel comparison**: Agents write nested for-loops over fragment pairs and pixel arrays. For medium grids, this is 10-100x slower than vectorized NumPy operations. → Time budget exhausted before assembly completes.

- **Wrong rotation count assertion**: Agents don't verify that the number of rotated fragments matches the stated count. If the puzzle specifies exactly 5 rotations, reporting 4 or 6 causes the entire rotation dimension to score zero. → Correct placements but zero rotation score.

## Self-Check Questions

- [ ] Did I pre-compute the full pairwise compatibility matrix before starting assembly?
- [ ] Did I filter distractors using edge-match quality before searching for arrangements?
- [ ] Am I using vectorized (NumPy) operations for batch edge comparisons?
- [ ] Did I anchor assembly from corner fragments with exactly two strong-matching edges?
- [ ] Does my assembly algorithm include backtracking when no strong match exists?
- [ ] Did I verify the assembled grid uses exactly the correct number of fragments?
- [ ] Did I validate that all internal boundaries have strong edge matches?
- [ ] Did I produce the assembled image at the correct dimensions for the grid?

## Technical Notes

- **Vectorized edge comparison**: Stack all fragment edge arrays into a 3D NumPy array (fragments × edge_length × channels), then use broadcasting to compute pairwise SSD in a single operation. This turns O(N²) Python loops into a single vectorized computation.
- **Rotation caching**: Precompute all four rotations of each fragment once, then index into the cached versions during edge comparison. Avoid rotating fragments inside comparison loops.
