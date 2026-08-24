---
name: 02-code-intelligence-task-3-jigsaw-puzzle-zh
description: Use when reassembling jigsaw puzzles from image fragments with distractors and rotations. Focuses on pixel-level edge matching, distractor filtering, and orientation detection.
---

# Pixel-Level Jigsaw Reassembly with Distractors

## Core Challenge

Reassembling a puzzle when the fragment set is contaminated with visually similar distractors requires distinguishing "correct neighbors" from "plausible but wrong neighbors." Color and texture similarity cannot separate distractors — only precise pixel-level edge alignment can. Additionally, some fragments are rotated, so orientation must be detected before edge comparison is meaningful.

## Solution Strategy

1. **Use pixel-exact edge matching, not feature similarity**: Compare raw pixel rows/columns at fragment edges using SSD (Sum of Squared Differences) or correlation. Correct neighbors have near-zero edge difference because they were cut from the same continuous image. Distractors, even from the same source image, were offset-cropped and will never achieve near-perfect alignment. Common mistake: agents use color histograms or texture features, which cannot distinguish aligned from non-aligned fragments.

2. **Try all four orientations for every edge comparison**: Before comparing edge A of fragment X with edge B of fragment Y, generate all four rotations (0, 90, 180, 270) of each fragment. The rotation that produces the best edge match IS the orientation detection. This jointly solves rotation detection and neighbor matching. Common mistake: agents try to detect rotation independently before matching, wasting effort on an under-constrained problem.

3. **Build a global compatibility matrix first**: Compute pairwise edge-match scores for ALL fragment pairs across ALL orientations before attempting assembly. This N×N matrix reveals which fragments have any compatible neighbor and which don't — the ones with no strong matches anywhere are likely distractors. Common mistake: agents start placing fragments greedily without first building the full compatibility picture.

4. **Filter distractors by exclusion, not by appearance**: Distractors look identical to real fragments. Identify them by their inability to form strong edge matches with ANY other fragment across all orientations. The correct set of fragments forms a closed graph where each has at least two strong-matching neighbors (or fewer for corners/edges). Common mistake: agents try to classify distractors by visual appearance or content, which fails because distractors come from the same source image.

5. **Assemble via global optimization, not greedy placement**: Starting from one fragment and greedily adding neighbors leads to cascading errors. Instead, find the arrangement of exactly N fragments (for an N-cell grid) that maximizes total edge-match score across all internal boundaries. Common mistake: agents place fragments one at a time and lock in early mistakes.

6. **Validate assembly by checking ALL internal edges**: After placing all fragments, verify that every pair of adjacent fragments in the grid has a strong edge match. Weak matches at internal boundaries indicate either a wrong placement or an undetected rotation. Common mistake: agents verify only the strongly-matched edges and ignore weak ones that signal errors.

## Decision Points

- **Edge match threshold for "compatible"**: Set the SSD threshold based on the distribution of scores. Correct matches have dramatically lower SSD than incorrect ones — look for the gap in the score distribution, not an arbitrary threshold.

- **Corner/edge vs interior fragments**: Fragments that match strongly on only 1-2 sides are likely corners or edges of the grid. Use this to anchor the assembly rather than starting from the center.

- **SSD threshold calibration**: Compute pairwise SSD for all fragments, sort the scores, and look for a natural gap between strong matches and noise. Correct neighbors produce SSD values orders of magnitude lower than incorrect pairs.

## Common Failure Patterns

- **Using feature matching instead of pixel matching**: Agents use SIFT, ORB, or color histograms to find similar fragments. Distractors are visually similar to real fragments, so feature matching includes them. → Wrong fragments selected, grid positions all wrong.

- **Detecting rotation before matching**: Agents try to determine each fragment's rotation independently (e.g., by text orientation or sky direction). This is unreliable for abstract images and wastes the strongest signal — edge match quality under rotation. → Rotation misclassification cascades into wrong placement.

- **Greedy assembly without backtracking**: Agents place the first fragment, attach its best match, then attach the next, never revisiting earlier decisions. One early mistake propagates through the entire grid. → Systematically wrong grid where errors compound.

- **Assuming distractors look different**: Agents expect distractors to be visually distinguishable and try to classify them by appearance. Since distractors are offset-crops from the same image, they look entirely plausible. → Real fragments excluded, distractors included.

- **Confusing rotation direction**: Agents report the correction rotation instead of the applied rotation, or vice versa. The task asks for the clockwise rotation that was APPLIED to the fragment, not the correction. → Correct placement but wrong transform labels.

## Self-Check Questions

- [ ] Am I using pixel-level edge comparison (SSD/correlation on raw pixel rows), not feature matching?
- [ ] Did I try all four rotations when comparing each edge pair?
- [ ] Did I build a full pairwise compatibility matrix before starting assembly?
- [ ] Did I identify distractors by their lack of strong edge matches, not by appearance?
- [ ] Am I optimizing the total edge-match score globally, not greedily placing fragments?
- [ ] Did I verify that the count of rotated fragments matches the stated number?
- [ ] Did I check that ALL internal grid edges have strong pixel-level matches?
- [ ] Did I produce the assembled image at the correct dimensions for the grid?

## Technical Notes

- **Edge comparison direction**: When comparing fragment A's right edge with fragment B's left edge, compare the last column of A with the first column of B. After rotating a fragment 90 degrees, its edges change — recompute which physical edge each matrix side represents after rotation.
- **Rotation inversion for assembly**: If a fragment was rotated 90 degrees clockwise, you must rotate it 90 degrees counter-clockwise (270 clockwise) before placing it in the grid. The reported transform is the applied rotation, not the correction.
