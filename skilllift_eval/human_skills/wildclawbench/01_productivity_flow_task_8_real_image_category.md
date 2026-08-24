---
name: 01-productivity-flow-task-8-real-image-category
description: Use when classifying a large batch of heterogeneous images into predefined categories. Focuses on consistent classification criteria, boundary disambiguation, and handling edge cases at scale.
---

# Large-Scale Multimodal Image Classification

## Core Challenge

Classifying 100 diverse images into 5 categories requires consistent application of category definitions across visually dissimilar items. The difficulty is boundary disambiguation: many images could plausibly fit two categories (a scanned document of a chart, a UI screenshot of a data table), and classification quality depends on understanding the INTENT behind each category definition, not just surface-level visual similarity.

## Solution Strategy

1. **Internalize category definitions before classifying**: Read each category description carefully and understand the distinguishing criteria. "Charts and tables" is about data visualization; "documents and text" is about text-dominant content; "synthetic/3D/UI" is about computer-generated content. These boundaries matter. → Common mistake: agents classify by superficial visual similarity without understanding category intent.

2. **Establish priority rules for ambiguous cases**: Some images span categories. A presentation slide containing a chart is both "document" and "chart". Decide: which category's PRIMARY purpose does the image serve? Create a decision hierarchy. → Common mistake: agents assign ambiguous images randomly or inconsistently, splitting similar images across categories.

3. **Process every image—no skipping**: At 100 images, it's tempting to batch-classify from filenames or thumbnails. Each image must be individually inspected. Filenames are unreliable indicators of content. → Common mistake: agents classify from filenames or low-resolution thumbnails, misclassifying images whose content differs from what the name suggests.

4. **Preserve filenames exactly**: When moving images to category folders, preserve the original filename character-for-character. Don't normalize case, fix typos, or rename during the move. → Common mistake: agents "helpfully" rename files, breaking the filename-to-content mapping.

5. **Verify the partition is complete**: After classification, verify that the number of files across all folders equals the original count. No duplicates, no missing files, no extras. → Common mistake: agents leave images in the source directory or accidentally copy rather than move.

## Decision Points

- **Chart inside a document**: If the image is primarily a page of text containing a chart, classify by dominant content. If the chart is the main subject, it's a chart. If the text dominates, it's a document. The key question: what is the image PRIMARILY communicating?

- **Photograph of a screen/synthetic content**: A photo of a monitor showing a UI is a natural-scene photo (the camera captured a real scene). The content on the screen doesn't change the image's fundamental nature. Distinguish "image of" from "screenshot of".

- **Medical vs scientific**: Medical images (X-rays, MRI) are diagnostic/clinical. Scientific images (microscopy, data plots in papers) are analytical. If an image could be either, check: does it show anatomy or pathology? If yes, medical. If it shows experimental data or formulas, scientific.

## Common Failure Patterns

- **Inconsistent boundary classification**: Similar images classified into different categories because the agent uses different criteria at different points during the batch. Two chart-heavy presentation slides go to different folders. → Reduced purity and completeness scores.

- **Filename-based classification**: Agents classify from filename patterns ("chart_001.jpg" → charts) without viewing the image. Filenames may be misleading or intentionally ambiguous. → Systematic misclassification of mismatched filenames.

- **Missing or duplicate images**: Agents lose track of which images they've processed, leaving some in the source or creating duplicates in category folders. → Fails the "all expected files present" and "no duplicates" checks.

- **Creating extra files**: Agents generate classification logs, thumbnail sheets, or metadata files alongside the category folders. → Fails the "no extra files" cleanliness check.

- **Misunderstanding "synthetic"**: Agents put rendered photographs or digitally enhanced photos into the synthetic category. "Synthetic" means entirely computer-generated (3D renders, UI mockups), not digitally processed. → Pollution of the synthetic category.

- **Boundary case flooding**: A few ambiguous images (photo of a chart, screenshot of a document) consume disproportionate classification effort. Agents either overthink these or assign them randomly. → Inconsistent treatment of structurally similar ambiguous cases.

## Self-Check Questions

- [ ] Did I view and classify every image individually, not from filenames?
- [ ] Did I apply category boundary rules consistently across all images?
- [ ] Are there exactly 5 subdirectories with the specified names?
- [ ] Does the total file count across all folders equal the original input count?
- [ ] Are all original filenames preserved without modification?
- [ ] Did I avoid creating any extra files (logs, metadata, summaries) in the results directory?
- [ ] Did I handle ambiguous images using a consistent priority rule?
- [ ] Did I verify no image appears in more than one category folder?
- [ ] Did I create the category directories with the exact specified names?

## Technical Notes

- **Vision model classification**: When using multimodal APIs for classification, provide the full category definitions in the prompt and ask for a single category assignment. Batch multiple images per API call to reduce request count, but verify each image is individually assessed.
- **File operations**: Use `shutil.move()` or `shutil.copy2()` to preserve file metadata. Avoid renaming during transfer. Verify file integrity by checking that source and destination have the same byte count.
