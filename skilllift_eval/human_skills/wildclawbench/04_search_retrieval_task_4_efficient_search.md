---
name: 04-search-retrieval-task-4-efficient-search
description: Use when answering factual questions under a strict search budget. Focuses on query optimization, multi-hop evidence chaining, and knowing when to stop searching.
---

# Budget-Constrained Multi-Hop Web Search

## Core Challenge

Finding two related facts (e.g., a version number AND its corresponding PR) within a tight search budget requires each query to be maximally informative. The challenge is designing queries that resolve multiple unknowns simultaneously rather than one at a time, and building an evidence chain that proves correctness without exhausting the budget on verification.

## Solution Strategy

1. **Decompose the question into its atomic facts**: Identify exactly what unknowns must be resolved (e.g., "which version?" and "which PR?"). Determine whether a single well-crafted query can resolve both or whether they require separate searches. → Treating a two-part question as one vague query returns results that answer neither part definitively.

2. **Craft maximally specific queries**: Use exact identifiers, API names, and context terms in the query. "pathlib.Path.walk Python version added" is better than "Python pathlib walk method." Specificity reduces result noise and increases the chance of hitting changelogs or release notes directly. → Generic queries return tutorials and blog posts instead of authoritative changelog entries.

3. **Target authoritative primary sources**: Official documentation, changelogs, GitHub repositories, and PEP documents are primary sources. A single hit on the official changelog resolves the fact definitively, while a blog post requires cross-verification (costing another search). → Landing on secondary sources that paraphrase without citing specifics requires additional searches to confirm.

4. **Read results thoroughly before searching again**: A single search result page often contains both the version number and a link or reference to the PR. Scan the entire page for both unknowns before issuing a second query. → Issuing a new search for the PR when the version page already mentioned it wastes budget.

5. **Design query 2 based on query 1's findings**: If query 1 resolved the version but not the PR, make query 2 specifically about that version's PR (e.g., combining the confirmed version name with "bpo PR" or "pull request"). Adaptive queries are more efficient than pre-planned ones. → Pre-committing to a fixed query sequence regardless of intermediate findings wastes budget on irrelevant results.

## Decision Points

- **One query for both facts vs separate queries**: If a single authoritative page (e.g., the official "What's New" document) likely contains both the version and PR reference, use one query. If the facts live in different document types (changelog vs GitHub), plan for two.

- **When to stop after 2 searches**: If both facts are resolved and each is backed by a primary source citation, stop. Additional "verification" searches waste budget and add no value.

- **When to say "unable to confirm"**: If the budget is exhausted and either fact lacks primary-source backing, state inability to confirm rather than guessing.

## Common Failure Patterns

- **Wasting searches on exploration**: Agents use the first query to "explore" broadly, the second to narrow, the third to find the specific page, and the fourth to confirm. → Exhausting the budget on process overhead rather than finding the answer.

- **Querying for one fact at a time when combined is possible**: Agents search for the version, then separately for the PR, when a single query targeting the "What's New in Python X" page would have yielded both. → Using 2 searches where 1 would suffice.

- **Trusting secondary sources without verification**: Agents accept a blog post's claim without finding the primary source, then can't build a reliable evidence chain. → Weakened confidence in the final answer, or using remaining budget to verify rather than advance.

- **Guessing after budget exhaustion**: Agents run out of searches and fill in missing facts from memory. → Providing incorrect answers presented as confirmed facts.

## Self-Check Questions

- [ ] Did I identify all atomic facts that need resolution before searching?
- [ ] Is each query maximally specific (using exact names, version hints, context)?
- [ ] Am I targeting primary sources (official docs, changelogs, repositories)?
- [ ] Did I thoroughly scan each result page for ALL unknowns before next query?
- [ ] Is my query N+1 informed by findings from query N?
- [ ] Did I provide a complete evidence chain with page titles, claims, and URLs?
- [ ] If I could not confirm within budget, did I state "unable to confirm" rather than guess?

## Technical Notes

- **Changelog vs "What's New" documents**: Python's "What's New in X.Y" documents often reference the originating issue or PR directly. These are more efficient targets than generic documentation pages.
- **GitHub PR search**: Searching `repo:python/cpython <feature description>` on GitHub or Google can surface the exact PR in one query, combining the version confirmation with the PR identification.
