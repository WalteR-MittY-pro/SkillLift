---
name: 02-code-intelligence-task-12-connect-the-dots-hard-zh
description: Use when solving large-scale, multi-group connect-the-dots puzzles requiring pattern recognition. Focuses on scalable coordinate extraction under color-grouping constraints, efficient rendering, and post-hoc image description.
---

# Multi-Group Coordinate Extraction and Pattern Recognition

## Core Challenge

With 100+ numbered dots divided into color groups, the extraction problem scales dramatically: each number must be correctly identified AND assigned to the correct color group, with coordinates precise enough that sequential connections form recognizable patterns. The challenge combines large-scale visual extraction (where missing or misclassifying even a few dots distorts the pattern), multi-group awareness (numbers reset within each group), and post-hoc pattern recognition from the connected result.

## Solution Strategy

1. **Segment by color group before number extraction**: Different colored dots belong to different groups with independent numbering (each group has its own 1, 2, 3...). First, cluster all dots by color, then process each color group's number sequence independently. Mixing groups causes catastrophic numbering errors. Common mistake: agents process all dots as a single sequence, producing meaningless connections.

2. **Use color-based segmentation for dot detection**: Detect dots by their colored outline/fill, not by text content. Cluster detected dots by color similarity (exact RGB or tight color thresholds), then within each color cluster, read the numbers. This ensures correct group assignment even when numbers are hard to read. Common mistake: agents try to read numbers first, then assign groups — but number reading errors cascade into wrong group assignments.

3. **Verify per-group completeness**: Within each color group, verify the numbers form a complete sequence (1, 2, 3, ..., N) with no gaps or duplicates. Missing numbers in a group break the connection chain for that group. If gaps exist, re-scan the image for missed dots of that color before proceeding. Common mistake: agents proceed with incomplete per-group sequences, producing broken patterns.

4. **Render connections per group on the original image**: For each color group, load the original image (or a single shared copy) and draw line segments connecting consecutive numbers within that group. Use the group's color for the lines so the pattern is visually distinguishable. Save the final composite image with all groups' connections drawn. Common mistake: agents draw each group separately and don't composite them, or use wrong colors.

5. **Describe the pattern from the connected result, not the raw dots**: After drawing all connections, analyze the resulting composite image to identify what the pattern depicts. Use a VLM on the rendered result, not the original dot image. The connected lines reveal shapes and scenes that aren't visible in the unconnected dots. Common mistake: agents try to identify the pattern from the original image where the shapes aren't yet formed.

6. **Vectorize extraction and rendering for 100+ dots**: At this scale, per-dot Python loops for coordinate detection, number reading, and line drawing become prohibitively slow. Use NumPy for batch coordinate processing, PIL/ImageDraw for batch line rendering, and concurrent VLM calls for number reading across groups. Common mistake: agents write per-dot sequential loops and exhaust the time budget on extraction alone.

## Decision Points

- **Color clustering precision**: If two group colors are similar (e.g., two shades of blue), use tight RGB thresholds or HSV-space clustering to separate them. Misclassifying a dot into the wrong color group corrupts both groups' sequences. When colors are ambiguous, use the number sequence continuity as a tiebreaker — if assigning a dot to group A creates a gap in group A's sequence but not in group B's, assign to B.

- **Partial extraction handling**: If some dots in a group can't be read (too small, overlapping, unclear), connect the readable subset. A partial pattern with gaps may still be recognizable and partially scorable. Don't skip an entire group because a few dots are unreadable.

## Common Failure Patterns

- **Treating all dots as one sequence**: Agents ignore color grouping and connect all dots in a single 1-2-3-...-N sequence across groups. → Chaotic, meaningless lines that don't form any pattern.

- **Color misclassification**: Agents use loose color matching that merges two distinct groups or splits one group into two. → Wrong group assignments corrupt numbering for multiple groups simultaneously.

- **Incomplete extraction without re-scanning**: Agents miss dots in dense regions or at image edges and proceed without re-scanning. → Broken connection chains within groups, producing fragmented patterns.

- **Describing the wrong image**: Agents describe the original dot image instead of the connected result, or describe only one group's connections instead of the composite. → Description doesn't match what the connected image actually shows.

- **Sequential per-dot processing at scale**: Agents process each of 100+ dots individually in Python loops for detection, OCR, and drawing. At this scale, the time budget is consumed by extraction with nothing left for rendering or description. → No output produced within the time limit.

## Self-Check Questions

- [ ] Did I segment dots by color group BEFORE reading numbers?
- [ ] Did I verify each color group has a complete number sequence (1 through N, no gaps)?
- [ ] Did I re-scan for missed dots when a group's sequence has gaps?
- [ ] Did I draw connections per-group on the original image using each group's color?
- [ ] Is the final image a composite of ALL groups' connections, not separate images?
- [ ] Did I analyze the CONNECTED result (not the original dots) for pattern description?
- [ ] Did I write the description in the required language and format?
- [ ] Did I use vectorized/batch operations rather than per-dot loops for 100+ dots?
- [ ] Did I write the description from the composite connected image, not the original dots?

## Technical Notes

- **Color space for grouping**: HSV color space is often more robust than RGB for distinguishing similar colors, as it separates hue from intensity. Convert detected dot colors to HSV and cluster on the hue channel with tight tolerances.
- **Multi-group line rendering**: When drawing multiple groups' connections on a single image, draw each group's lines in its own color to maintain visual separation. If groups' lines overlap at intersection points, use line width or alpha blending to keep both visible.
- **Scale and performance**: For 100+ dots, vectorize the coordinate extraction and connection drawing using NumPy arrays and batch PIL/OpenCV drawing operations. Per-dot Python loops become slow at this scale.
