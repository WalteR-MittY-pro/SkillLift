---
name: 01-productivity-flow-task-3-bibtex
description: Use when recovering metadata from degraded or unreliable source files with multi-field verification. Focuses on handling corrupted inputs, exact identifier recovery, and cross-referencing authoritative sources.
---

# Metadata Recovery from Degraded Sources

## Core Challenge

Files arriving with unreliable metadata—corrupted PDFs, messy filenames, missing pages—must be mapped to their authoritative identity (official title, canonical ID) and have structured data extracted accurately. The core difficulty is knowing when to trust local file content and when to discard it in favor of external authoritative sources, especially when files are damaged.

## Solution Strategy

1. **Identify via multiple signals, not metadata alone**: Use first-page text content, embedded metadata, and web search as complementary signals. When they disagree, prioritize the official source page over local metadata fields. → Common mistake: agents trust PDF `/Title` metadata, which is often wrong or missing.

2. **Handle corrupted files explicitly**: For truncated or damaged PDFs, extract whatever text is available and use it as a search query to find the official paper. Don't skip corrupted files or mark them as unidentifiable. → Common mistake: agents skip corrupted PDFs entirely rather than using partial content for identification.

3. **Count from visible content only**: For figure counting in degraded PDFs, count only what actually renders in the provided pages. A corrupted PDF missing pages should reflect the reduced count, not the official paper's count. → Common mistake: agents look up the paper online and report the official figure count instead of counting visible figures.

4. **Apply transformation rules precisely**: Filename sanitization rules (Unicode normalization, character replacement, whitespace collapsing) must be applied as an exact pipeline. Implement each step explicitly and in order. → Common mistake: agents apply rules partially or in wrong order, producing filenames that don't match.

5. **Copy files byte-for-byte**: When creating renamed copies, use binary copy operations. Any text-mode conversion can alter bytes. → Common mistake: agents re-encode PDFs during copy, changing byte content and failing integrity checks.

## Decision Points

- **When to search online vs use local content**: If the PDF renders cleanly with readable title text, extract locally. If the PDF is corrupted, truncated, or has garbled text, use whatever fragments are available as search queries against the official repository.

- **How to identify a "corrupted" PDF**: Missing pages (page count far below expected), garbled or unreadable text, visual scribbles or watermarks obscuring content. These require external identification rather than local parsing.

## Common Failure Patterns

- **Trusting embedded PDF metadata**: PDF `/Title`, `/Author` fields are set by LaTeX templates and are frequently incorrect or generic (e.g., "Main Paper"). → Wrong titles propagated to filenames and manifest.

- **Skipping corrupted files**: Agents encounter a damaged PDF and skip it or assign a guessed identity rather than using partial content for identification. → Missing entries in output, zero coverage for corrupted papers.

- **Counting from official source instead of provided file**: Agents identify the paper, look it up online, and report the official figure count. But the provided PDF may be a corrupted version with fewer pages. → Figure counts that don't match what's actually visible.

- **Text-mode file copying**: Agents read the PDF as text and write it back, corrupting binary content. → Files fail byte-identity checks.

- **Inconsistent BibTeX sourcing**: Agents generate BibTeX entries manually from metadata fields instead of copying the official entry from the source page. Minor formatting differences (field order, brace style) cause exact-match failures. → BibTeX entries that don't match the official format after normalization.

- **Extra output files**: Agents leave downloaded archives, temporary scripts, or intermediate files in the workspace. → Workspace clutter that may interfere with output validation.

## Self-Check Questions

- [ ] Did I extract titles from first-page text content rather than PDF metadata fields?
- [ ] Did I handle corrupted PDFs by using partial content for web identification?
- [ ] Did I count figures from the actual visible pages in the provided PDF?
- [ ] Did I apply the filename sanitization rules in the exact specified order?
- [ ] Did I create renamed copies using binary copy (byte-for-byte)?
- [ ] Did I generate BibTeX from the official source page, not synthesized from metadata?
- [ ] Did I include every input PDF in the manifest with no omissions or extras?
- [ ] Did I verify each manifest entry's renamed filename matches the actual file in renamed_papers/?
- [ ] Did I clean up all temporary files, leaving only the required outputs?
- [ ] Did I handle Unicode special characters in titles during sanitization?

## Technical Notes

- **PDF metadata unreliability**: LaTeX-generated PDFs often have `/Title` set to the document title from `\title{}`, but many templates leave it blank or set it to generic values. Never rely on metadata fields for ground truth.
- **Binary file copying**: Use `shutil.copy2()` or equivalent binary copy. Never read-then-write a PDF through text processing, as this corrupts binary streams.
- **Unicode NFKC normalization**: This normalization form decomposes compatibility characters (e.g., fullwidth letters to ASCII equivalents). Apply it before character replacement in filename sanitization.
