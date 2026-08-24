---
name: 04-search-retrieval-task-5-fuzzy-search
description: Use when identifying a unique item from vague, partial descriptors. Focuses on candidate enumeration, systematic elimination, and multi-attribute verification.
---

# Fuzzy Descriptor-Based Entity Identification

## Core Challenge

When a user provides partial clues (topic area, author surname, time range, popularity metric) rather than exact identifiers, the target entity must be disambiguated from a field of similar candidates. The challenge is generating a sufficiently broad candidate pool and then eliminating systematically rather than latching onto the first plausible match.

## Solution Strategy

1. **Search by the most distinctive clue first**: Identify which clue has the highest discriminative power. Niche research topics ("R1 approach applied to visual tasks") are more selective than common attributes ("first author Liu"). Start with the rare combination to get a focused candidate set. → Starting with common attributes returns hundreds of irrelevant results that dilute the search.

2. **Enumerate multiple candidates before committing**: The first plausible candidate is not always the correct one. List 3-5 candidates that match the topic description before applying secondary filters (author name, star count). → Committing to the first candidate skips verification against constraints that may disqualify it.

3. **Apply each clue as an independent filter**: Take the candidate list and check each item against every clue independently: Does the first author's surname match? Does the GitHub repo have the required stars? Is the time period correct? An item must pass ALL filters. → Applying filters mentally ("it probably matches") rather than checking each explicitly introduces confirmation bias.

4. **Verify metadata from primary sources**: Author order comes from the paper itself (arXiv, conference proceedings). Star counts come from the GitHub repository page. Do not infer these from blog posts or secondary summaries. → Using approximate or outdated metadata from secondary sources leads to false matches or misses.

5. **Confirm uniqueness of the final candidate**: After finding one entity that passes all filters, do a quick sanity check — could another entity also match all clues? If yes, the clues may be insufficient and the answer requires additional disambiguation. → Reporting a match without checking for ambiguity risks providing the wrong entity when multiple candidates satisfy the stated criteria.

## Decision Points

- **When clues are contradictory**: If no candidate satisfies all clues, re-examine whether you interpreted a clue correctly. "More than 2k stars" might refer to a fork, an organization, or a different metric. Relax interpretation before concluding no match exists.

- **Broad vs narrow initial query**: Start narrow (all distinctive clues combined). If zero results, progressively drop the most ambiguous clue until candidates appear. This prevents both information overload and empty results.

- **Which sources to trust for metadata**: arXiv listings for author order (canonical), Google Scholar for citation context, GitHub for repository metrics (live data). Conference/workshop pages for acceptance status.

## Common Failure Patterns

- **First-match bias**: Agents find a candidate that matches the topic description and author surname, then stop without checking the star count or other remaining constraints. → Recommending an entity that fails unverified constraints.

- **Over-constraining the initial query**: Agents include all clues in one search query, getting zero results because no single page mentions all attributes simultaneously. → Missing the target because the query was too specific for any single page to match.

- **Trusting secondary summaries for metadata**: Blog posts or review articles may misattribute author order or approximate star counts. → False elimination of the correct candidate or false acceptance of a wrong one.

- **Ignoring the "first author" specification**: Candidates match the surname but the person is a middle or last author, not first. → Recommending a paper where the named person is not in the first-author position.

## Self-Check Questions

- [ ] Did I search using the most distinctive clue first rather than common attributes?
- [ ] Did I enumerate multiple candidates before committing to one?
- [ ] Did I verify EVERY clue independently against each candidate?
- [ ] Did I confirm author order from the paper itself (arXiv/proceedings), not secondary sources?
- [ ] Did I verify the GitHub star count from the live repository page?
- [ ] Did I check whether multiple candidates could satisfy all clues (ambiguity check)?

## Technical Notes

- **arXiv author ordering**: arXiv lists authors in the order specified by the submission. This is the canonical order unless the published version differs. Check both if available.
- **GitHub star count volatility**: Star counts grow over time. If the clue specifies a threshold, verify the current count meets it. Historical snapshots (e.g., via star-history.com) can confirm the threshold was crossed within the relevant timeframe.
