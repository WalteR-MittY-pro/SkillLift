---
name: 04-search-retrieval-task-8-paper-affiliation-search
description: Use when compiling and classifying accepted papers from a specific conference track. Focuses on comprehensive enumeration, first-affiliation determination, and exact counting under strict completeness requirements.
---

# Conference Paper Enumeration and Affiliation Classification

## Core Challenge

Enumerating a specific paper subset (e.g., Oral papers at a conference) requires finding the complete authoritative list, then for each paper, determining the first affiliation from sources that may not explicitly label it. The challenge is achieving full coverage — missing even one paper or misclassifying one affiliation invalidates the entire count.

## Solution Strategy

1. **Find the official accepted paper list first**: Conferences publish accepted papers on official websites, OpenReview, or conference management systems. Start with the official source to get the complete paper set for the relevant track. → Using third-party summaries or partial lists guarantees incomplete enumeration.

2. **Filter to the correct track**: Conferences have multiple acceptance categories (Oral, Spotlight, Poster, Highlight). Ensure you are enumerating only the specified track. Track lists may overlap or be nested. → Including posters or spotlights in an oral-only count inflates numbers; excluding oral papers listed under a combined category deflates them.

3. **Determine first affiliation for each paper individually**: The "first affiliation" is the affiliation of the first author, which may differ from the affiliation that appears first in the author list if authors list multiple affiliations. Check the paper's author-affiliation mapping carefully. → Assuming the first-listed affiliation block is the first author's affiliation without verifying the author-affiliation correspondence.

4. **Handle multi-affiliation and joint first authors**: Some first authors list multiple affiliations. Determine which is "primary" by checking affiliation order, corresponding author markers, or the paper's acknowledgment section. When in doubt, the first-listed affiliation for the first author is conventionally primary. → Picking the wrong affiliation from a multi-affiliation author misclassifies the paper.

5. **Count and list with explicit verification**: After classifying all papers, produce both the count and the full title list. Cross-verify by re-counting the listed titles. Ensure no duplicates and no extraneous papers are included. → Counting errors from off-by-one or including a paper in both target categories.

## Decision Points

- **When the official list is unavailable or incomplete**: Fall back to OpenReview, conference proceedings, or reputable tracking sites (e.g., conference-specific GitHub repos). Cross-reference multiple sources to reconstruct the full list.

- **How to resolve affiliation ambiguity**: If a paper's affiliation data is unclear from the paper page, check the arXiv preprint, the authors' personal pages, or institutional directories. Use the most recent authoritative source.

- **Zero-count verification**: If you find zero papers for a target institution, verify this is correct (not a search failure) by checking that the institution's researchers did submit to the conference and that your enumeration of the track is complete.

## Common Failure Patterns

- **Incomplete paper list**: Agents find a partial list (e.g., only papers on OpenReview) and miss papers that were accepted but not uploaded, or listed only on the conference website. → Undercounting or missing specific papers.

- **Wrong track filter**: Agents enumerate all accepted papers instead of filtering to the specific track (Oral vs all acceptances). → Inflated counts with wrong papers listed.

- **Affiliation from wrong source**: Agents use the corresponding author's affiliation instead of the first author's, or use a co-author's affiliation. → Misclassifying papers into wrong institution counts.

- **Stopping at first search**: Agents find a list on a blog or news article and treat it as complete without seeking the official conference source. → Missing papers that the secondary source didn't cover.

## Self-Check Questions

- [ ] Did I find the official conference source for the accepted paper list?
- [ ] Did I filter to the exact track specified (Oral, Spotlight, Poster, etc.)?
- [ ] Did I enumerate ALL papers in that track, not just the easily findable ones?
- [ ] For each paper, did I determine the first author's primary affiliation from the paper itself?
- [ ] Did I handle multi-affiliation authors with a consistent rule?
- [ ] Did I cross-verify my count against my title list (recount the list)?
- [ ] Did I verify zero-counts by confirming the enumeration is complete?

## Technical Notes

- **OpenReview vs conference website**: OpenReview may not distinguish tracks clearly, or track assignments may change after initial posting. The conference website's official program is the definitive track source.
- **Affiliation normalization**: Institutions may appear under different names (abbreviations, full names, departmental variants). Normalize to a canonical form before counting to avoid splitting or merging counts incorrectly.
- **First author vs first affiliation**: These are different concepts. The first author may have multiple affiliations; "first affiliation" typically means the first-listed affiliation of the first author, but verify the convention expected by the task.
