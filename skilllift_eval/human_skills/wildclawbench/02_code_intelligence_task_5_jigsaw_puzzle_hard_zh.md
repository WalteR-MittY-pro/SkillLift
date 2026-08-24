---
name: 02-code-intelligence-task-5-jigsaw-puzzle-hard-zh
description: Use when reassembling large-scale jigsaw puzzles (5x5+ grids with many distractors). Focuses on computational efficiency, aggressive pruning, and time-budget-aware optimization strategies.
---

# Large-Scale Jigsaw Reassembly Under Time Pressure

## Core Challenge

At 5x5+ grids with 37+ fragments and 12+ distractors, the challenge is no longer just correctness — it's completing within a strict time budget. The compatibility matrix grows quadratically, the search space explodes, and naive approaches time out before producing any result. Success requires aggressive pruning, efficient data structures, and a willingness to accept "good enough" arrangements when optimal search is infeasible.

## Solution Strategy

1. **Vectorize everything from the start**: At this scale, every computation must use NumPy batch operations. Stack all fragment edges into arrays, compute pairwise SSD via broadcasting in a single operation. Pure Python loops over 37 fragments × 4 rotations × 37 fragments = 5,476 comparisons will be 100x slower than needed. Common mistake: agents prototype in pure Python and run out of time before vectorizing.

2. **Aggressively prune before searching**: With 12+ distractors among 37+ fragments, over a third of fragments are noise. Use the compatibility matrix to identify and exclude distractors immediately: sort fragments by their best-match score and cut at the natural gap between real fragments and distractors. Reducing from 37 to 25 fragments shrinks the search space by orders of magnitude. Common mistake: agents include all fragments in the assembly search, making it intractable.

3. **Build from borders inward**: For a 5x5 grid, identify the 16 border fragments (top row, bottom row, left column, right column) first — they have fewer matching edges (2-3 instead of 4). Assemble the border ring, then fill the 3x3 interior. This decomposition turns a 25-fragment problem into a 16-fragment problem plus a 9-fragment problem. Common mistake: agents try to fill the grid linearly, forcing every position to be solved simultaneously.

4. **Accept approximate solutions when time runs short**: For large grids, finding the globally optimal arrangement is NP-hard. Use heuristics: place fragments by best-greedy-match, then perform local swaps between adjacent mismatched pairs to improve the total score. One or two rounds of local optimization often fix most greedy-placement errors. Common mistake: agents insist on finding the perfect solution and produce nothing within the time limit.

5. **Parallelize VLM calls for description**: If you need to identify the assembled image's content, call the VLM immediately after assembly while handling file output in parallel. Don't serialize dependent and independent operations. Common mistake: agents serialize every step, burning the time budget on sequential waits.

6. **Write outputs before perfection**: Save the assembled image and result JSON as soon as a complete grid is produced, even if you're not 100% confident. You can always re-save with improvements later. Having NO output at deadline is far worse than having a 70%-correct output. Common mistake: agents hold results in memory, intending to write the "perfect" version, and lose everything when time runs out.

## Decision Points

- **When to switch from optimal search to heuristic**: If the compatibility matrix shows clear, unambiguous matches for most positions (large gaps between best and second-best scores), greedy placement will work. If scores are ambiguous (many close alternatives), invest in backtracking or local optimization.

- **How to handle uncertain distractor classification**: If you can't confidently classify all distractors, err on the side of including a few extra fragments in the search. Having 26 candidate fragments instead of 25 is manageable; having 37 is not. Filter conservatively.

- **When to produce a partial output**: If time is nearly exhausted and only a partial grid is assembled, save what you have immediately. A partially correct grid scores higher than no output at all.

## Common Failure Patterns

- **Timeout from exhaustive search**: Agents attempt brute-force or deep backtracking on a 25-fragment search space. → No output produced, zero score.

- **Unvectorized prototype in production**: Agents write clean, readable Python loops for edge comparison. For 37 fragments, this takes minutes instead of seconds. → Time budget exhausted on computation, no time left for assembly or verification.

- **Including all fragments in assembly**: Agents search over all 37 fragments for each of 25 positions, trying to let the optimizer reject distractors during search. → Search space so large that no arrangement is found.

- **Perfectionism on individual placements**: Agents spend excessive time verifying and re-verifying each fragment placement instead of producing a complete draft and then optimizing. → Partial completion with large sections missing.

- **Ignoring the stated rotation count**: For puzzles that specify an exact number of rotations, agents don't verify their detected count matches. An off-by-one in rotation count zeros out the entire rotation scoring dimension. → Correct grid but zero transform score.

## Self-Check Questions

- [ ] Is all edge comparison vectorized using NumPy broadcasting, not Python loops?
- [ ] Did I prune distractors before starting assembly search?
- [ ] Did I decompose assembly into border-first then interior?
- [ ] Did I produce a complete first draft before optimizing individual placements?
- [ ] Am I monitoring elapsed time against the stated budget?
- [ ] Did I write output files before attempting further optimization?
- [ ] Is my detected rotation count consistent with the stated puzzle parameters?
- [ ] Did I produce the assembled image at the correct dimensions for the grid?

## Technical Notes

- **Pre-computing rotation variants**: For 37 fragments with 4 rotations, pre-generate all 148 rotated variants as a 4D NumPy array (fragment × rotation × height × width). This allows vectorized edge comparison across all rotation combinations in a single broadcasting operation.
- **Memory vs time tradeoff**: At 5x5 scale, the full compatibility matrix (37 × 4 × 37 × 4 × edge_metrics) fits comfortably in memory. Don't optimize for memory at the expense of computation speed — store everything, compute once.
