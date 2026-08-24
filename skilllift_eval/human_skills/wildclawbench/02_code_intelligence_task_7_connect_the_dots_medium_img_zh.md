---
name: 02-code-intelligence-task-7-connect-the-dots-medium-img-zh
description: Use when extracting numbered coordinates from puzzle images and drawing sequential connections. Focuses on accurate visual coordinate extraction, number recognition, and programmatic line rendering.
---

# Visual Coordinate Extraction and Sequential Line Drawing

## Core Challenge

Extracting precise numbered dot coordinates from a puzzle image is a vision-from-pixel problem: every number must be correctly read and its exact pixel position recorded. A single misidentified number or coordinate shifts all subsequent connections. The challenge is achieving near-perfect OCR on small, densely packed numbered dots, then rendering connections at exact pixel positions on the original image.

## Solution Strategy

1. **Extract ALL dots before connecting ANY**: Scan the entire image, identify every numbered dot and its coordinates, and build a complete coordinate table before drawing any lines. Partial extraction leads to incomplete connections. Use systematic grid-based scanning or connected-component analysis to ensure full coverage. Common mistake: agents process dots sequentially and miss dots in regions they don't scan carefully.

2. **Use VLM for number reading, not coordinate detection**: VLMs are good at reading numbers but bad at reporting precise pixel coordinates. Use vision models to identify what number is at each location, but determine coordinates programmatically (connected components, contour detection, or template matching). Common mistake: agents ask a VLM to "give me the coordinates of dot 5" — VLMs hallucinate coordinates.

3. **Verify completeness against the expected count**: After extraction, check that you have exactly N numbered dots (1 through N) with no gaps or duplicates. Missing dots indicate scanning failures; duplicates indicate detection errors. Fix extraction before proceeding to drawing. Common mistake: agents proceed with incomplete coordinate sets, producing broken connection sequences.

4. **Draw on a copy of the original image**: Load the original image, create a writable copy, and draw line segments connecting consecutive numbered dots in order. Use appropriate line thickness (visible but not so thick it obscures dots). Save the annotated image. Common mistake: agents create a blank image instead of annotating the original, losing context.

5. **Validate the drawn pattern visually**: After drawing, inspect the result — does it form a recognizable shape? Discontinuities, crossings, or random-looking paths indicate extraction errors. Cross-check with a VLM to verify the pattern looks intentional. Common mistake: agents save the output without visual validation, missing obvious extraction failures.

6. **Handle dense or overlapping dot regions carefully**: When numbered dots are close together, numbers may overlap visually or be partially obscured. In these regions, zoom in or use image preprocessing (upscaling, contrast enhancement) before OCR. A single misread digit in a dense area reorders the connection sequence for all subsequent dots. Common mistake: agents apply uniform processing to the entire image and miss digits in dense clusters.

## Decision Points

- **OCR approach for number reading**: For clear, large numbers, simple OCR (Tesseract) or VLM works. For small, overlapping, or stylized numbers, use template matching or train a digit classifier on the specific font. The choice depends on number size and clarity in the image.

- **Coordinate precision**: Sub-pixel precision is unnecessary — integer pixel coordinates of each dot's centroid are sufficient. Use the centroid of the dot's detected region, not the position of the number text.

- **When to use VLM vs programmatic extraction**: For clear, well-spaced numbered dots, programmatic detection (contour/connected-component) is faster and more precise. Reserve VLM for verifying ambiguous numbers or describing the final connected pattern.

## Common Failure Patterns

- **Asking VLM for coordinates**: Agents prompt "what are the positions of all numbered dots?" and trust the VLM's coordinate output. VLMs cannot reliably report pixel coordinates. → Coordinates that are approximate at best, completely wrong at worst, leading to chaotic line patterns.

- **Incomplete extraction**: Agents miss dots in certain regions (corners, edges, areas with overlapping numbers). The connection sequence has gaps, breaking the pattern. → Discontinuous lines that don't form the intended shape.

- **Number misidentification**: Small or densely packed numbers are read incorrectly (3 vs 8, 1 vs 7). One misread number reorders the entire connection sequence. → Correct dots but wrong connection order, producing a scrambled pattern.

- **Drawing on wrong canvas**: Agents create a new blank image or use the wrong dimensions. The output doesn't match the original puzzle layout. → Lines that don't align with the original dots.

- **Line styling errors**: Agents draw lines that are too thin (invisible at scaled resolutions), too thick (obscuring dot numbers), or use colors that blend with the background. → Connections that exist but aren't visually verifiable.

## Self-Check Questions

- [ ] Did I extract ALL numbered dots before drawing any connections?
- [ ] Did I verify the dot count is complete (no missing numbers, no duplicates)?
- [ ] Did I determine coordinates programmatically rather than asking a VLM?
- [ ] Did I draw connections on a copy of the original image?
- [ ] Did I visually validate the resulting pattern for discontinuities or errors?
- [ ] Are line connections in sequential order (1→2→3→...→N)?
- [ ] Did I handle dense/overlapping number regions with extra care?
- [ ] Did I use appropriate line thickness so connections are clearly visible?

## Technical Notes

- **Coordinate system**: Image pixel coordinates use (0,0) at the top-left corner, with x increasing rightward and y increasing downward. Ensure your drawing library uses the same convention. PIL and OpenCV both use this convention, but matplotlib's `imshow` with extent parameters can flip axes.
- **Dot centroid calculation**: When detecting dots via contour or connected-component analysis, use the centroid (mean x, mean y of all pixels in the region) rather than the bounding box center, as the number text may be offset from the dot center.
