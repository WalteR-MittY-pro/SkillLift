---
name: 01-productivity-flow-task-10-pdf-digest
description: Use when processing a batch of PDFs through rename, classify, and targeted table extraction phases. Focuses on reliable title extraction from content (not metadata), consistent batch processing, and selective deep extraction.
---

# Batch PDF Processing with Multi-Phase Output

## Core Challenge

Processing 65 PDFs through three sequential phases—rename by title, classify by topic, extract specific tables from identified papers—each requiring different cognitive skills. The difficulty is reliable title extraction from first-page text (not unreliable metadata), consistent classification across a large batch, and selectively drilling into specific papers for deep table extraction after the classification phase.

## Solution Strategy

1. **Extract titles from first-page text, never metadata**: PDF metadata (`/Title`, `/Author`) is frequently wrong, empty, or set to generic template values. Parse the first page's visible text and identify the title from its typographic prominence (large font, centered, at the top). → Common mistake: agents trust PDF metadata fields, which are intentionally unreliable in many papers.

2. **Process all phases after initial data gathering**: First, extract titles and abstracts for ALL papers. Then classify ALL papers. Then identify target papers and extract their tables. This prevents context-switching and ensures classification uses the complete dataset. → Common mistake: agents interleave phases, classifying some papers before all titles are extracted, leading to inconsistency.

3. **Classify from title + abstract, not title alone**: Titles are often ambiguous or creative. The abstract contains the methodology, domain, and contribution details needed for accurate categorization. Read both before assigning a category. → Common mistake: agents classify from title keywords only, misclassifying papers with ambiguous titles.

4. **Apply renaming rules as an exact pipeline**: Title → replace spaces with underscores → replace slashes with underscores → append `.pdf`. Each transformation step must be applied in order and consistently across all files. → Common mistake: agents apply replacements inconsistently or in wrong order.

5. **Extract tables by parsing structure, not OCR**: Table extraction should use PDF structure (line positions, cell boundaries) when available, falling back to text-based heuristics. Identify the correct table by its number (e.g., "Table 2") and extract its full content including headers and all rows. → Common mistake: agents extract the wrong table or miss rows, especially when tables span pages.

## Decision Points

- **When first-page text is unreadable**: If the PDF is image-based (scanned), use OCR. If the title is still unclear, look for the title in the PDF's bookmark structure or file properties as a last resort—but never as first choice.

- **How to identify the target table**: Tables are numbered by their appearance order in the paper. "Table 2" is the second table environment, not the second page. Search for the caption "Table 2:" or "Table 2." in the text and extract the tabular content that follows.

- **Which papers qualify for deep extraction**: After classification, identify papers matching the specified research interest (e.g., image captioning). Use both title and abstract signals. A paper about "image understanding" that includes captioning components qualifies; a paper about "video generation" does not.

## Common Failure Patterns

- **Metadata-based titles**: Agents extract titles from PDF `/Title` metadata, which is frequently set to LaTeX template defaults like "Main" or the filename. → Wrong filenames, wrong classification, cascading failures.

- **Inconsistent renaming**: Agents apply the space-to-underscore rule for some files but forget the slash replacement for others. → Rename mismatches that fail exact comparison.

- **Surface-level classification**: Agents classify from title alone without reading the abstract. Papers with creative or ambiguous titles get placed in wrong categories. → Classification accuracy drops significantly.

- **Missing caption-related papers**: Agents identify obvious matches (title contains "caption") but miss papers that contribute to captioning under different terminology (e.g., "visual description generation" or "image-to-text"). → Incomplete identification of target papers.

- **Wrong table extraction**: Agents extract the first table they find instead of the specified table number. Or they extract only visible rows when the table spans a page break. → Table content that doesn't match expected data.

## Self-Check Questions

- [ ] Did I extract titles from first-page visible text, not PDF metadata fields?
- [ ] Did I apply the renaming rules (spaces → underscores, slashes → underscores) consistently?
- [ ] Did I classify each paper using both its title AND abstract?
- [ ] Did I identify ALL papers matching the research interest, including non-obvious ones?
- [ ] Did I extract the correct numbered table (not just the first table found)?
- [ ] Did I render the extracted table as valid markdown with all rows and columns?
- [ ] Did I process all 65 PDFs without skipping any?
- [ ] Did I verify the rename mapping table is complete and matches actual filenames?
- [ ] Did I handle papers where the second table spans multiple pages or has complex formatting?
- [ ] Did I use consistent underscore replacements for both spaces and slashes in filenames?

## Technical Notes

- **PDF title extraction**: Use `pdfplumber` or `PyMuPDF` to extract text from the first page. The title is typically the first line with significantly larger font size, or the text between the top margin and the author list. Don't use `PyPDF2`'s `.metadata['/Title']`—it's the source of most title errors.
- **Table extraction from PDFs**: `pdfplumber.extract_tables()` provides cell-level extraction. For papers where tables are images, OCR with `camelot` or `tabula` may be needed. Always verify the table caption matches the expected number before extracting.
- **Classification at scale**: For 65 papers, batch classification is efficient. Group papers by likely category based on title keywords first, then verify each placement using the abstract. This is faster than individual classification while maintaining accuracy.
