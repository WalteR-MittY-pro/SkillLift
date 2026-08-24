---
name: 04-search-retrieval-task-1-google-scholar-search
description: Use when finding connection chains between two entities through a shared collaboration network. Focuses on graph traversal strategy, frontier management, and shortest-path verification.
---

# Academic Co-Authorship Chain Discovery

## Core Challenge

Finding the shortest connection path between two people in a collaboration graph requires breadth-first exploration where the graph is not pre-loaded — each node's neighbors must be discovered incrementally by crawling profile pages. The challenge is managing an expanding search frontier while proving no shorter path exists.

## Solution Strategy

1. **Start by profiling both endpoints**: Crawl both starting profiles completely to extract their direct co-authors (depth-1 neighbors). These are the most likely bridge nodes and should be expanded first. → Skipping endpoint profiling and diving into random traversal wastes the structural advantage of knowing both anchors.

2. **Use BFS from one or both endpoints**: Breadth-first search guarantees shortest-path discovery. Expand depth-1 neighbors of A, check for intersection with B's neighbors. If none found, expand to depth-2. BFS from both endpoints simultaneously (bidirectional) converges faster. → Using DFS or random exploration finds paths but cannot prove they are the shortest.

3. **Track visited nodes to avoid cycles**: Academic collaboration graphs are dense with triangles. Without a visited set, the search re-enters the same highly-connected hubs repeatedly, creating infinite loops or redundant work. → Forgetting visited sets causes exponential blowup on well-connected subgraphs.

4. **Extract co-authors from paper pages, not just profile pages**: A scholar's profile lists co-authors, but some co-authors only appear on individual paper pages. Cross-reference both sources for completeness. → Relying solely on profile-level co-author lists misses collaborators who appear on specific papers.

5. **Verify each link in the chain with a shared paper**: Every adjacent pair in the discovered chain must have at least one genuine co-authored paper. Verify by confirming the paper exists on both scholars' profiles. → Reporting chains based on name similarity rather than verified co-authorship produces unverifiable links.

## Decision Points

- **When to stop expanding**: If a path of length N is found, continue expanding all frontiers at depth N-1 to confirm no shorter path exists. Only stop when all nodes at the current depth boundary have been checked.

- **Bidirectional vs unidirectional BFS**: For chains expected to be length 2-4, bidirectional BFS (from both endpoints) is more efficient. For very long chains, unidirectional may be simpler to implement.

## Common Failure Patterns

- **Depth-first exploration**: Agents follow the first interesting lead deeply rather than exploring all neighbors at each level. → Finding a length-4 path when a length-3 path exists, missing the shortest chain.

- **Incomplete neighbor extraction**: Agents scrape only the top co-authors from a profile page and miss less prominent ones who may serve as critical bridge nodes. → Missing shorter paths that route through secondary collaborators.

- **Treating name matches as edges**: Two people with similar names on different papers are assumed to be the same person, creating false links. → Fabricated chains that don't represent real collaboration paths.

- **Stopping at first path found**: Agents discover one valid chain and report it without verifying whether shorter alternatives exist. → Failing to find the shortest path, which is the actual requirement.

## Self-Check Questions

- [ ] Did I fully crawl both endpoint profiles before starting traversal?
- [ ] Am I using BFS (or bidirectional BFS) rather than DFS?
- [ ] Did I maintain a visited set to prevent cycles and redundant expansion?
- [ ] Did I verify every adjacent pair in the chain shares at least one co-authored paper?
- [ ] Did I continue searching after finding the first path to confirm no shorter path exists?
- [ ] Did I check both profile-level co-author lists and individual paper pages for collaborators?

## Technical Notes

- **Profile page pagination**: Scholar profile pages may paginate co-author lists and publication lists. Ensure all pages are loaded before extracting neighbors.
- **Name disambiguation**: Common names (e.g., "J. Wang") may refer to different people. Use affiliation or domain overlap to disambiguate before treating two name instances as the same node.
