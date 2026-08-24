---
name: 04-search-retrieval-task-10-tomllib-trace
description: Use when tracing the origin of a standard library feature through version history and pull requests under a tight search budget. Focuses on query efficiency, primary-source targeting, and evidence chain construction.
---

# Standard Library Feature Provenance Tracing

## Core Challenge

Tracing when a standard library module was added and which specific PR introduced it requires navigating across changelogs, "What's New" documents, and the version control history. The challenge is doing this efficiently — each search must yield maximum information, and the evidence chain must be complete and self-supporting within a strict budget.

## Solution Strategy

1. **Target the "What's New" document first**: Python's "What's New in X.Y" documents are the authoritative source for standard library additions. They typically name the module, the version, and often reference the originating issue or PR. A single hit here can resolve both unknowns. → Searching generic documentation or tutorials first wastes queries on pages that mention the module without explaining its origin.

2. **Design queries that can resolve multiple unknowns simultaneously**: If the version is unknown, a query like "Python tomllib standard library added version" can return both the version and the issue reference in one result. Read the full page before issuing a second query. → Issuing separate queries for version and PR when a single page contains both.

3. **Trace from the changelog to the repository**: If the "What's New" document references an issue number (bpo-XXXXX) but not the PR, search GitHub for that issue number within the relevant repository. Issue pages link to their merging PRs. → Searching GitHub broadly for the module name when a specific issue reference is already known.

4. **Build the evidence chain as you go**: For each search, immediately note the page title, key claim, and URL. Don't defer documentation to the end — by then you may have forgotten which source provided which fact. → Producing an evidence chain with missing URLs or vague citations that can't be verified.

5. **Prefer repository pages over secondary commentary**: Blog posts discussing the feature may get the version right but rarely cite the exact PR. The official "What's New" page or the GitHub issue/PR page is definitive. → Building an evidence chain on secondary sources that themselves lack citations.

## Decision Points

- **When to search GitHub vs documentation**: After establishing the version from documentation, search the CPython repository for the specific PR. If the documentation already references the bpo issue, go directly to that issue page rather than searching.

- **One comprehensive query vs two focused queries**: A well-crafted single query targeting the "What's New" page for the likely version can resolve both facts. If unsure of the version, one exploratory query to find it, then one targeted query for the PR is efficient.

- **When to conclude "unable to confirm"**: If after reasonable searches, the PR number cannot be found from primary sources (only referenced in secondary commentary without a link), state inability to confirm rather than guessing from partial evidence.

## Common Failure Patterns

- **Exploratory over-spending**: Agents use 2-3 queries just to find the right version, leaving insufficient budget for the PR search. A single well-targeted query at the "What's New" documents should establish the version. → Budget exhaustion before the PR is found.

- **Trusting blog posts for PR numbers**: Blog posts may mention a feature without citing the PR. Agents treat the blog's version claim as sufficient and skip PR verification. → Incomplete evidence chain that can't prove the PR number.

- **Conflating issues with PRs**: CPython uses bpo-XXXXX for issues and #YYYYYY for PRs. Agents report the issue number when asked for the PR number, or vice versa. → Wrong identifier in the final answer.

- **Guessing the version from memory**: Agents "know" the module was added in a certain version and skip the version-confirmation search, spending all budget on the PR. → If the memory is wrong, the entire answer is wrong.

## Self-Check Questions

- [ ] Did I target the "What's New" document or changelog as my primary source?
- [ ] Did I read each result page thoroughly for ALL unknowns before next query?
- [ ] Did I trace from documentation references (bpo issues) to the actual PR?
- [ ] Am I reporting the PR number, not the issue number (or vice versa)?
- [ ] Does my evidence chain include page title, key claim, and URL for each source?
- [ ] Is each fact backed by a primary source (official docs, GitHub)?
- [ ] If I could not confirm within budget, did I state "unable to confirm"?

## Technical Notes

- **bpo vs GitHub issues**: Python's legacy bug tracker (bugs.python.org / bpo-XXXXX) predates the GitHub migration. Older features reference bpo numbers; the bpo issue page links to the corresponding GitHub PR. Post-migration features reference GitHub issues directly.
- **"What's New" document structure**: These documents are organized by category. New modules appear under "New Modules." Each entry typically credits the contributor and references the originating issue or PEP.
