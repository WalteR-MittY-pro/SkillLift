---
name: 01-productivity-flow-task-1-arxiv-digest
description: Use when aggregating and classifying academic papers from daily listings into structured reports with metadata auditing. Focuses on multi-phase content processing, accurate document parsing, and completeness under scale.
---

# Multi-Phase Academic Content Aggregation

## Core Challenge

Processing dozens of papers through sequential transformation phases—fetch, classify, deep-parse, personalize, extract—while maintaining accuracy at each stage. The difficulty compounds: classification errors propagate to metadata extraction, incomplete fetching misses papers, and surface-level document parsing produces incorrect counts. Agents must sustain precision across heterogeneous data transformations within a single workflow.

## Solution Strategy

1. **Fetch completely before processing**: APIs often paginate or return partial results. Retrieve ALL results for the target date before classifying anything. Verify the total count against expected volume. → Common mistake: agents process the first API response page without checking for additional pages, missing 30-50% of papers.

2. **Classify from abstracts, not titles alone**: Titles are ambiguous; abstracts contain the methodology and domain signals needed for accurate categorization. Read both before assigning a category. → Common mistake: agents classify from title keywords only, placing papers in wrong categories.

3. **Parse document structure, not just text**: Counting figures and tables requires understanding document boundaries—where the main paper ends and the appendix begins. Identify structural markers (section headings, page breaks) before counting. → Common mistake: agents count all figure references in the text, inflating numbers by counting subfigure markers or cross-references.

4. **Enforce internal consistency**: Total counts must equal main + appendix splits. If they don't match, re-count before outputting. This is a computable invariant—verify it programmatically. → Common mistake: agents independently estimate main and appendix counts without checking that they sum to the reported total.

5. **Anchor personalized recommendations in stated interests**: When recommending papers, explicitly connect paper content to the user's declared research profile. Don't recommend based on general popularity. → Common mistake: agents recommend the "best" paper rather than the most relevant one.

## Decision Points

- **When to use HTML vs PDF for parsing**: HTML renders are often available and easier to parse for structure (figure tags, table tags). Prefer HTML when available; fall back to PDF only when HTML is absent or incomplete.

- **How to handle borderline classifications**: If a paper touches two categories, assign it to the one matching its primary contribution, not its application domain. A medical imaging paper using VLMs belongs in Medical if its evaluation is medical, but in VLMs if its contribution is the model architecture.

## Common Failure Patterns

- **Truncated fetching**: Agents stop after the first API response without checking pagination. ArXiv APIs typically return results in pages. → Missing papers in classification, incomplete metadata tables.

- **Partial author lists**: Agents copy the first few authors visible on the abstract page instead of finding the complete list. → Metadata rows with incomplete author fields, failing validation.

- **Counting subfigure labels as figures**: "(a)" and "(b)" are subfigure markers within one figure, not separate figures. Agents count them individually. → Inflated figure counts that don't match ground truth.

- **Skipping the benchmark extraction phase**: Agents complete classification and metadata but forget to search for papers mentioning the user's specific methods for benchmark comparison. → Missing the benchmark section entirely.

- **Conflating main paper with appendix**: Agents fail to identify the appendix boundary, counting appendix figures as main figures or vice versa. The split between main and appendix is a structural determination, not a heuristic guess. → Incorrect split counts that fail internal consistency checks.

## Self-Check Questions

- [ ] Did I verify I fetched ALL papers for the target date, checking for pagination?
- [ ] Did I classify each paper using its abstract, not just its title?
- [ ] Did I count figures/tables from the document content, excluding subfigure markers?
- [ ] Did I verify that total figures = main figures + appendix figures (and same for tables)?
- [ ] Did I include the COMPLETE author list for each metadata row, not just the first few?
- [ ] Did I identify the appendix boundary explicitly before splitting counts?
- [ ] Did I search for papers mentioning the user's specific methods for benchmark extraction?
- [ ] Did I check that every paper fetched appears in exactly one classification category?
- [ ] Did I verify that "Others" contains only papers that don't fit the five named categories?
- [ ] Did I recommend exactly the number of papers specified, grounded in the user's stated research interests?
- [ ] Did I verify that figure/table counts come from actual document content, not from abstract-page metadata?

## Technical Notes

- **ArXiv API pagination**: The API returns results in batches. Check the `totalResults` field in the response metadata and iterate until all results are fetched. A single request rarely returns all papers for an active submission day.
- **HTML vs PDF figure counting**: ArXiv HTML views (ar5iv or arxiv-html) wrap figures in `<figure>` tags, making counting reliable. PDFs require rendering and heuristic detection, which is error-prone for appendices.
- **Appendix boundary detection**: Look for section headings like "Appendix A", "Supplementary Material", or sudden changes in figure/table numbering schemes. These mark where main paper content ends.
