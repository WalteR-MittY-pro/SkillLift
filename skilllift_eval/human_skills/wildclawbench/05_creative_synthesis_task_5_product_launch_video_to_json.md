---
name: 05-creative-synthesis-task-5-product-launch-video-to-json
description: Use when extracting structured product data from event videos and creating promotional materials. Focuses on exhaustive enumeration, strict null discipline, dual-format output quality, and anti-hallucination grounding.
---

# Video-Based Data Extraction and Promotional Synthesis

## Core Challenge

Product launch videos require two simultaneous outputs: a structured data file with exact specs and a visually polished promotional document. The difficulty is maintaining absolute grounding—every data field must come from the video, with null for anything not shown—while also producing design-quality output. Agents must enumerate ALL products exhaustively, resist hallucinating specs from prior knowledge, and create professional visual materials from extracted content.

## Solution Strategy

1. **Enumerate before extracting**: First pass—identify ALL hardware products announced in the video. Count them. Then verify: did I miss any? Watch transitions, recaps, and summary slides carefully. Only after confirming completeness, extract specs per product. Common mistake: agents start extracting details for products they noticed early and miss products shown briefly or later.

2. **Strict null discipline**: If a spec (chip, colors, battery, price) is not explicitly shown or stated in the video, it is null—not inferred, not estimated, not filled from prior knowledge. "This looks like it could have an A17 chip" → null. Every non-null field must have a clear video source. Common mistake: agents fill in specs from model knowledge of the product line, introducing hallucinated data.

3. **Dual-track your workflow**: The structured data and the promotional document serve different purposes and need different pipelines. Build the data file first (accuracy-critical), then design the promotional piece (aesthetics-critical) using verified data. Common mistake: agents build the promotional piece first and reverse-engineer the data from it, propagating design-driven errors.

4. **Design the promotional piece as marketing, not as a data dump**: A promotional document is visual storytelling—product images, highlights, clean layouts. Not a table of specs. Extract compelling frames from the video as product imagery. Common mistake: agents create a plain list or table of products with no visual hierarchy.

5. **Verify page and format constraints precisely**: If the output requires a specific page count and page size, verify with measurement tools after generation. "Approximately 5 pages" is not 5 pages. Each page must be the specified dimensions. Common mistake: agents generate content and assume the page count and sizes are correct without measuring.

## Decision Points

- **Product inclusion criteria**: Only include hardware products—physical devices that are announced. Exclude software updates, services, subscription tiers, and vague future teasers. If a segment mentions a product category but no specific product, don't invent one.
- **Spec extraction confidence**: If the video shows a spec briefly or the audio mentions it once, include it. If you're unsure about a specific numeric value, re-examine the relevant segment multiple times before committing.
- **Image selection for promotional piece**: Use the clearest, most representative product frames from the video. Avoid motion-blurred frames, transition animations, or frames with heavy text overlays.

## Common Failure Patterns

- **Incomplete product enumeration**: Agents identify the prominent products (main smartphones) but miss secondary ones (earbuds announced briefly, smartwatch shown for 10 seconds). → Missing entries in the data, incomplete promotional coverage.

- **Prior-knowledge hallucination**: Agents recognize the product line and fill specs from training data—chip names, colors, battery estimates—that weren't in the video. → Hallucinated data that doesn't match the source.

- **Null avoidance**: Agents feel uncomfortable leaving fields null and fill them with guesses or web-inferred values. Null is the CORRECT value for unspecified fields. → Inflated confidence in non-grounded data.

- **Promotional piece as spec sheet**: Agents create a document that lists products and specs in a table format. This is a catalog, not a promotional piece. → Low visual quality, failed design assessment.

- **Page/size constraint violations**: Agents generate a promotional document with 4 or 6 pages instead of exactly the required count, or use letter size instead of the specified format. → Automatic gating failures.

## Self-Check Questions

- [ ] Did I enumerate ALL hardware products before extracting specs?
- [ ] Is every non-null spec field grounded in explicit video evidence?
- [ ] Did I use null (not guesses) for all unspecified values?
- [ ] Did I exclude software/services and only include hardware?
- [ ] Does the promotional document use product images extracted from the video?
- [ ] Is the promotional piece a designed marketing layout (not a spec table)?
- [ ] Did I verify the exact page count and page dimensions after generation?
- [ ] Are product names and prices exactly matching what the video presents?

## Technical Notes

- **Page dimension verification**: After generating a PDF, use a library to open it and check both page count and pixel dimensions of each page. Don't trust the generation tool's defaults—measure the output.
- **Video frame extraction for images**: Extract frames at product reveal moments (when the product image is full-screen, not during transitions). Multiple frames per product give better image-text matching in the promotional piece.
