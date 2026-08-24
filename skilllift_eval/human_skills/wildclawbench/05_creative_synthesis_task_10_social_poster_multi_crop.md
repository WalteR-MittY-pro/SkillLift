---
name: 05-creative-synthesis-task-10-social-poster-multi-crop
description: Use when adapting a single poster image to multiple social media aspect ratios. Focuses on content-aware cropping, subject preservation across ratios, and platform-specific dimension knowledge.
---

# Content-Aware Multi-Aspect-Ratio Image Cropping

## Core Challenge

A single source poster must be adapted to multiple social media platforms, each requiring a different aspect ratio. The core difficulty is that aggressive aspect ratio changes—especially from landscape to vertical—can't be solved by center-cropping. You must understand WHERE the important content is (subject, headline, logo, key visuals) and choose crop windows that preserve them across all aspect ratios. Different ratios may need different crop strategies for the same source.

## Solution Strategy

1. **Analyze the source image's content map first**: Before cropping, use vision analysis to identify: primary subject/focal point, text regions (headline, subhead, logo), negative space areas, and composition balance. This map drives all crop decisions. Common mistake: agents apply center-crop to all ratios without analyzing where content actually sits.

2. **Know the platform aspect ratios precisely**: Each social platform has specific aspect ratio requirements. Look them up or compute them exactly (e.g., 1:1, 9:16, 4:5). Approximate ratios fail validation. Common mistake: agents use "roughly square" or "roughly tall" instead of exact ratios, producing outputs that miss the 2% tolerance.

3. **Different ratios may need different crop strategies**: A 1:1 crop might center on the main subject. A 9:16 crop might need to shift vertically to include both the headline at top and the subject below. A 4:5 crop might crop in from both sides. Don't use one crop window for all outputs. Common mistake: agents use a single center-point and crop outward, ignoring that different ratios interact differently with the content layout.

4. **Prioritize subject preservation over symmetry**: When forced to choose between a centered crop that cuts off the subject's head and an asymmetric crop that keeps the subject fully visible, choose the latter. Content preservation beats compositional symmetry. Common mistake: agents optimize for visual balance at the expense of cutting into critical content.

5. **Verify each crop against the original**: After cropping, compare each output to the source. Is the main subject still recognizable? Are key text elements legible? Are edges cut at natural boundaries (not through someone's face or through the middle of a word)? Common mistake: agents apply the crop and never check if the result looks acceptable.

## Decision Points

- **Center crop vs offset crop**: If the subject is centered in the source, center-crop works for moderate ratio changes. If the subject is off-center or the ratio change is extreme (e.g., landscape to 9:16), offset the crop window toward the subject.
- **Handling extreme vertical crops (9:16)**: A landscape-to-vertical crop is the hardest. Consider: (a) cropping to the most important vertical strip, (b) if the subject is too wide, focusing on the central portion. You may need to accept that some peripheral content will be lost.
- **Edge boundaries**: Avoid cropping through text, faces, or logo elements. If the crop window intersects critical content, nudge it until the boundary falls in a natural break (negative space, background).

## Common Failure Patterns

- **Blind center-crop**: Agents apply center-cropping to all aspect ratios. When the subject is off-center or the ratio is extreme, center-crop cuts off important content. → Subjects lost or partially visible in crops.

- **Wrong aspect ratios**: Agents use approximate ratios ("tall and narrow" instead of exactly 9:16). The outputs fail validation because they're outside tolerance. → All crops rejected at the gating stage.

- **Ignoring content layout**: Agents treat cropping as a pure geometry problem—just resize the crop window. They don't consider what's IN the window. → Crops that capture background while losing the main subject.

- **One strategy for all ratios**: Agents use the same crop center and method for every aspect ratio, not realizing that a 1:1 crop and a 9:16 crop interact with content layout very differently. → Some crops look good while others lose critical content.

- **No post-crop verification**: Agents generate crops and submit without checking whether the output looks acceptable compared to the original. → Cropped images with awkward boundaries or missing content go uncorrected.

## Self-Check Questions

- [ ] Did I analyze the source image's content layout before cropping?
- [ ] Are the aspect ratios exact (not approximate) for each platform?
- [ ] Did I use different crop strategies for different aspect ratios?
- [ ] Is the main visual subject preserved and recognizable in EVERY crop?
- [ ] Are crop boundaries at natural edges (not through text, faces, or logos)?
- [ ] Did I compare each crop output back to the original to verify quality?

## Technical Notes

- **Aspect ratio tolerance**: Platform aspect ratios typically allow 1-2% tolerance. Compute the exact ratio (e.g., 9/16 = 0.5625) and verify the output ratio is within tolerance. Off-by-one-pixel errors in dimension calculation can push ratios outside tolerance on small images.
- **Crop window calculation**: For a source of (W, H) and target ratio r = tw/th: if W/H > r, the crop width = H * r (crop sides); if W/H < r, the crop height = W / r (crop top/bottom). Then offset the crop window within the source to center on the subject.
