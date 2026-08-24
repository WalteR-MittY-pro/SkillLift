---
name: 01-productivity-flow-task-4-2022-conference-papers
description: Use when compiling academic publication records with strict provenance verification across multiple authoritative sources. Focuses on completeness, source-of-truth discipline, and multi-field data gathering.
---

# Multi-Source Academic Data Compilation

## Core Challenge

Compiling a complete publication record for a specific researcher requires gathering data from conference proceedings, personal homepages, code repositories, and paper source archives—each with different reliability. The challenge is maintaining strict source-of-truth discipline: knowing which source is authoritative for each field, avoiding inclusion of non-qualifying works (workshops, arXiv preprints), and resolving discrepancies between sources.

## Solution Strategy

1. **Establish the qualifying filter first**: Before gathering any data, define exactly what qualifies: main conference proceedings only, specific year, specific author involvement. Apply this filter consistently across all sources. → Common mistake: agents include workshop papers, arXiv preprints, or journal versions that appear in search results.

2. **Use official proceedings as source of truth**: Conference names, titles, author lists, and abstracts must come from the official conference website or proceedings—never from arXiv, Semantic Scholar, or secondary databases. These sources frequently modify titles or reorder authors. → Common mistake: agents copy titles from arXiv, which may differ from the camera-ready version.

3. **Resolve author links to personal homepages**: Prioritize personal academic websites over Google Scholar, DBLP, ORCID, or LinkedIn. If no personal homepage exists, record "not found" rather than substituting a lower-quality link. → Common mistake: agents default to Google Scholar profiles because they're easy to find, rather than searching for direct homepages.

4. **Trace code links from paper content**: GitHub commit IDs must come from links explicitly mentioned in the paper text. Read the latest paper version, find repository URLs, and query the repository API for the latest commit as of the specified date. → Common mistake: agents search GitHub for the paper title and link to unrelated repositories.

5. **Download source for every available version**: arXiv papers may have multiple versions (v1, v2, v3). Each version's main `.tex` file must be saved separately with version-suffixed naming. → Common mistake: agents save only the latest version, missing earlier versions.

## Decision Points

- **Official proceedings vs arXiv for title/abstract**: Always prefer official proceedings. If the proceedings page is inaccessible, use arXiv but note the potential discrepancy. Titles on arXiv are preprint titles and may differ from camera-ready.

- **When an author has no personal homepage**: Record "not found" explicitly. Do not substitute a DBLP page, Google Scholar profile, or university directory listing—these are not personal homepages.

- **Which `.tex` file is "main"**: The file containing `\documentclass` and `\begin{document}` is the main paper file. If multiple files have these (rare), choose the one that `\input`s the others.

## Common Failure Patterns

- **Including non-proceedings works**: Agents include workshop papers, tutorials, or arXiv-only preprints because they appear in author profile searches. → False positives in the paper set, reducing precision.

- **Missing papers due to incomplete search**: Agents search only one source (e.g., Google Scholar) and miss papers that appear in proceedings but not in the search index. → False negatives, reducing recall.

- **Wrong source for metadata**: Agents copy author lists from arXiv PDFs where author order may differ from the camera-ready version, or where accent marks are dropped. → Author field mismatches.

- **Inventing commit IDs**: Agents guess commit hashes or use `HEAD` at the wrong date, rather than querying the API for the specific commit as of the required date. → Invalid or incorrect commit references.

- **Wrong arXiv version**: Agents download only the latest arXiv version when earlier versions also exist, or save the wrong version's `.tex` file. → Missing `.tex` files for versions that exist on arXiv.

- **Abstract from wrong source**: Agents copy abstracts from arXiv or Semantic Scholar, which may differ subtly from the official conference proceedings abstract. → Abstract field mismatches after normalization.

## Self-Check Questions

- [ ] Did I verify each paper is in the main conference proceedings (not a workshop or tutorial)?
- [ ] Did I use official conference proceedings as the source for title, authors, and abstract?
- [ ] Did I search for personal academic homepages rather than defaulting to profile aggregators?
- [ ] Did I find GitHub links by reading the paper text, not by searching GitHub?
- [ ] Did I download and save the main `.tex` file for EVERY available arXiv version?
- [ ] Did I sort the output by conference and then by title as specified?
- [ ] Did I exclude arXiv-only preprints and journal-only versions?
- [ ] Did I verify every author in the `Authors` field also appears in `Author links` in the same order?
- [ ] Did I avoid creating any extra files beyond the TSV and `.tex` sources?
- [ ] Did I handle accent marks in author names correctly (e.g., á vs a)?

## Technical Notes

- **arXiv source versioning**: arXiv source packages are versioned. To get v1, v2, etc., download `eprint/{id}/v1`, `eprint/{id}/v2`. Each version is a separate tarball. The main `.tex` file is identified by `\documentclass`.
- **GitHub commit at a specific date**: Use the GitHub API: `GET /repos/{owner}/{repo}/commits?until={ISO_DATE}&per_page=1` to find the latest commit before a date. Don't use `HEAD`—it changes over time.
