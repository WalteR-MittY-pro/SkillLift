---
name: 01-productivity-flow-task-9-scp-crawl
description: Use when systematically crawling a series of structured web pages with content, image, and metadata extraction. Focuses on completeness across pages, content-vs-chrome separation, and consistent metadata rules.
---

# Systematic Web Crawling with Structure Preservation

## Core Challenge

Crawling 50 pages from a wiki site and extracting three distinct artifacts per page—body text, content images, and structured metadata—while maintaining consistent extraction rules across all pages. The difficulty is distinguishing content from site decoration (navigation icons, avatars, social buttons) and applying fallback rules consistently for metadata fields that may be absent or variably named.

## Solution Strategy

1. **Crawl systematically with consistent structure**: Create a directory per item with a predictable naming scheme. Process items in order. Don't skip items that appear empty—verify before skipping. → Common mistake: agents skip items that return 404 or redirect, creating gaps in the sequence.

2. **Separate content from chrome**: Wiki pages contain site navigation, sidebars, user avatars, social media icons, and rating widgets alongside the article body. Extract only the article content area, not the full page HTML. → Common mistake: agents save all images from the page, including site logos and user avatars.

3. **Apply metadata fallback chains**: When a primary metadata field is absent, fall through to secondary fields in a defined order. Record "Unknown" only when all fallbacks are exhausted. Apply this chain identically for every page. → Common mistake: agents apply the fallback inconsistently, using secondary fields for some pages and "Unknown" for others with the same structure.

4. **Count structural elements by their logical units**: Collapsible/expandable blocks are logical units, not raw HTML tags. Each collapsible section counts once, regardless of how many child elements it contains. → Common mistake: agents count raw HTML tags (`<details>`, collapsible class instances) without understanding the logical structure.

5. **Preserve text completeness**: The extracted text should represent the full article body, not a summary or excerpt. Include all paragraphs, headings, and formatted text blocks. Exclude navigation, comments, and footer content. → Common mistake: agents save only the first paragraph or a truncated version of the article.

## Decision Points

- **What counts as a "content image"**: Images within the article body that illustrate the SCP entry itself. Exclude: site logos, user avatars, social media icons, rating badges, navigation arrows, advertisements. When uncertain, check if the image appears in the article content div or in the page chrome.

- **How to handle missing pages**: If an SCP page doesn't exist (404 or redirect to a different page), still create the directory and an empty or placeholder text file. The directory structure must be complete from 001 to 050 regardless of content availability.

- **Text format: Markdown vs plain text**: Save as Markdown to preserve headings, lists, and formatting. Plain text loses structure. But don't include raw HTML tags in the Markdown—convert to Markdown equivalents.

## Common Failure Patterns

- **Including chrome images**: Agents save all `<img>` tags from the page, including navigation icons, avatars, and social media buttons. → Inflated image counts that don't match ground truth.

- **Inconsistent metadata fallback**: Some pages have "Object Class" in a standard infobox; others use "Containment Class" or have neither. Agents handle each variant ad hoc rather than applying a consistent fallback chain. → Class field errors for pages with non-standard metadata.

- **Counting raw HTML instead of logical blocks**: Collapsible blocks may be implemented via different HTML patterns (`<details>`, JavaScript toggles, CSS classes). Agents count HTML elements rather than logical collapsible sections. → Incorrect n_mask counts.

- **Truncated text extraction**: Agents extract only visible (non-collapsed) text, missing content inside collapsed blocks that are part of the article. Or they extract only the first section. → Text content that fails anchor-based recall checks.

- **Missing directories for empty pages**: Agents skip creating directories for SCPs with no images or minimal content. → Incomplete directory structure, failing coverage checks.

## Self-Check Questions

- [ ] Did I create directories for ALL items (001-050), even those with missing content?
- [ ] Did I filter images to include only article body content, excluding site chrome?
- [ ] Did I apply the metadata fallback chain consistently (Object Class → Containment Class → Unknown)?
- [ ] Did I count collapsible blocks as logical units, not raw HTML tags?
- [ ] Is the extracted text a complete representation of the article body?
- [ ] Did I verify each image file is a valid JPEG?
- [ ] Did I name image files sequentially (1.jpg, 2.jpg, ...) within each item directory?
- [ ] Does the summary JSONL contain exactly one entry per item?
- [ ] Did I handle SCP pages with non-standard or missing Object Class fields using the fallback chain?

## Technical Notes

- **Content area isolation**: Wiki platforms typically wrap article content in a specific div (e.g., `#page-content` for Wikidot). Use this selector to scope text and image extraction. Full-page scraping includes chrome.
- **Collapsible block detection**: Wikidot uses `[[collapsible]]` syntax rendered as `<div class="collapsible-block">`. Each such div is one logical block. Check both the raw wikitext (via API) and rendered HTML for reliability.
- **JPEG validation**: Valid JPEG files start with bytes `FF D8` and end with `FF D9`. Some wiki platforms serve PNG or WebP with `.jpg` extension—convert or reject non-JPEG data.
