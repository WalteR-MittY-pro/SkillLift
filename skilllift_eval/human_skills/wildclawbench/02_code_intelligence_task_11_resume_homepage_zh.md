---
name: 02-code-intelligence-task-11-resume-homepage-zh
description: Use when generating styled web pages from structured documents (PDFs, resumes). Focuses on document content extraction, strict filtering rules, style fidelity from reference screenshots, and visual asset integrity.
---

# Document-to-Web Generation with Style Fidelity

## Core Challenge

Generating a styled web page from a document (e.g., PDF resume) requires accurately extracting structured information from a semi-structured format, then applying precise filtering rules to select which content appears. Unlike screenshot-based extraction, documents contain more complete data but require judgment: which papers qualify, which dates are relevant, which roles to highlight. The output must match a visual template's style conventions while faithfully representing the document's content.

## Solution Strategy

1. **Extract ALL document content before filtering**: Read the entire document and structure it into categories: personal info, education, publications, awards, experience, services. Don't filter during extraction — capture everything, then apply filters in a separate pass. Common mistake: agents extract selectively, missing items that should appear after filtering reveals the selection criteria.

2. **Apply filtering rules as hard constraints**: Each filtering requirement is a binary test, not a preference. For publications: is the target author first or co-first author? Is the paper formally accepted (not under review)? Is it a main track paper (not a Findings or workshop)? For news: does the date fall within the specified range? Apply ALL filters — an item must pass every filter to be included. Common mistake: agents apply some filters but miss others, including items that fail one criterion while passing the rest.

3. **Use the template's design system, not a custom design**: Clone the template source or replicate its exact CSS structure. The template defines specific conventions: section ordering, header emojis, card layouts, tag styles, date formats. Every design choice in the template is intentional — don't substitute "equivalent" alternatives. Common mistake: agents create a "similar" design instead of an exact match, losing points on specific style markers.

4. **Resolve real visual assets**: Profile photos, paper thumbnails, and social icons must render as actual images. Extract images from the document if available, download from linked sources if the document references them, or generate appropriate alternatives. Gray placeholders, broken links, and default avatars all fail. Common mistake: agents leave image src attributes empty or pointing to non-existent files.

5. **Verify content completeness against the source**: After populating the page, cross-check every section against the source document. Did you include all qualifying publications? All awards? All education entries? Missing content is scored as zero for each missing item. Common mistake: agents include some content from the document but miss items buried in less prominent sections.

6. **Screenshot the full page with Playwright, not browser tools**: Use headless browser automation specifically configured for full-page capture. Set appropriate viewport width, disable lazy-loading delays, and wait for network idle before capturing. The screenshot must show ALL sections from top to bottom in a single image. Common mistake: agents use a browser's built-in screenshot tool which captures only the viewport, or don't wait for images to load before capturing.

## Decision Points

- **Document vs online source for content**: The document is authoritative. Online sources may be useful for obtaining visual assets (profile photos, paper images) but should never override the document for content. If they conflict, trust the document.

- **Co-first author detection**: Documents may mark co-first authors with asterisks, footnotes, or explicit statements. Check for these markers carefully — a paper where the target author is second-listed but marked as co-first qualifies for inclusion.

## Common Failure Patterns

- **Incomplete document extraction**: Agents miss items in less-scannable parts of the document (e.g., awards at the bottom, services in a sidebar, open-source contributions in a footnote). → Missing content that was present in the source.

- **Filter misapplication**: Agents include under-review papers, Findings papers, or papers where the target author isn't first/co-first. Or they include news from wrong year ranges. → Wrong content included, qualifying content crowded out.

- **Style drift from template**: Agents produce a page that's "academic styled" but doesn't match the specific template's conventions — wrong emojis, wrong date format, wrong card layout, wrong tag style. → Style fidelity scores low despite functional correctness.

- **Placeholder or broken images**: Agents don't obtain real profile photos or paper thumbnails, leaving broken links or gray boxes. → Visual resource scores zero for every missing or broken image.

- **Incomplete screenshot capture**: Agents capture only the browser viewport or don't wait for resources to load. The screenshot misses content below the fold or shows broken images that haven't finished loading. → Many rubric items cannot be evaluated from the screenshot.

## Self-Check Questions

- [ ] Did I extract ALL content from the source document before applying any filters?
- [ ] Did I apply EVERY filtering rule as a hard constraint (author role, acceptance status, track, date range)?
- [ ] Did I verify co-first author markers are detected and applied?
- [ ] Is the page style cloned from the actual template, not custom-built?
- [ ] Are ALL visual assets real images that render correctly?
- [ ] Did I cross-check every page section against the source document for completeness?
- [ ] Did I capture a full-page screenshot showing the complete rendered page?
- [ ] Did I wait for all images and resources to load before taking the screenshot?
- [ ] Did I verify that ALL filtering rules were applied (author role, acceptance status, track type, date range)?
- [ ] Did I use the source document as authoritative content, overriding any conflicting online sources?

## Technical Notes

- **PDF extraction precision**: PDF text extraction tools (pdfplumber, PyMuPDF, pdfminer) vary in how they handle multi-column layouts, tables, and embedded links. If one tool produces garbled output, try another. Verify extracted publication lists against visual inspection of the PDF — author order and acceptance status are critical and easily garbled.
- **Template conventions inventory**: Before populating content, create an explicit list of the template's style markers: which emojis precede which sections, what date format is used, how paper tags are styled, how social links are arranged. Check each against your implementation before taking the final screenshot.
