---
name: 04-search-retrieval-task-2-conflicting-handling
description: Use when resolving conflicts between locally stored reference materials and current web sources. Focuses on staleness detection, source authority ranking, and contradiction resolution.
---

# Local-Web Information Conflict Resolution

## Core Challenge

When local reference materials (legal texts, regulations, internal docs) may be outdated, agents must cross-check local claims against current web sources, detect discrepancies, and determine which version is authoritative. The challenge is recognizing that local data has a staleness risk and knowing when to trust web authority over local convenience.

## Solution Strategy

1. **Read local materials completely first**: Extract the full reasoning framework and specific provisions from local files before searching the web. Local materials provide the analytical structure even if specific values are outdated. → Skipping local files and going straight to web search misses the domain framework and baseline reasoning the task expects.

2. **Flag temporal markers in local data**: Identify dates, version numbers, amendment references, or effective dates in local materials. Any provision without a recent verification date is a candidate for staleness. → Treating local provisions as current without checking temporal markers leads to citing superseded law.

3. **Verify each critical provision against current web sources**: For every specific rule, time limit, or threshold extracted locally, search the web to confirm it is still in effect. Focus on official government sources or authoritative legal databases. → Trusting local data for specific numbers without web verification is the primary failure mode.

4. **Resolve conflicts in favor of current authority**: When local and web sources disagree, the web source (if from an authoritative, current publication) wins. Document the conflict and the resolution explicitly so the reasoning is auditable. → Silently choosing one source without acknowledging the conflict produces untrustworthy analysis.

5. **Synthesize the final answer from verified information only**: After cross-checking, build the analysis from provisions that survived verification. Clearly cite which provisions were confirmed current and which were updated. → Mixing verified and unverified provisions creates internal inconsistency.

## Decision Points

- **When local data has no date markers**: Treat as potentially outdated. Prioritize web verification for all specific values. If web sources confirm the local value, it was current; if not, use the web value.

- **Conflicting web sources**: Prefer official government websites (.gov domains), official gazettes, or established legal databases over secondary commentary. If two authoritative sources disagree, cite both and flag the ambiguity.

- **When to search vs reason locally**: Use local materials for analytical framework (what factors to consider, what provisions apply). Use web search for specific current values (time limits, thresholds, effective dates).

## Common Failure Patterns

- **Trusting local data uncritically**: Agents read local legal provisions and treat them as current law, reasoning to a conclusion without any web verification. → Citing outdated statutes or repealed provisions, producing legally incorrect answers.

- **Ignoring local data entirely**: Agents skip local files and rely solely on web search, missing the specific analytical framework or jurisdiction scope the local materials establish. → Correct general reasoning but wrong specific application.

- **Treating all sources as equal authority**: Agents find a blog post that disagrees with an official statute and treat both as equally valid. → Giving equal weight to anonymous commentary and official law.

- **Partial verification**: Agents verify the main provision but not ancillary rules (e.g., verify the base limitation period but not the tolling or interruption rules). → Internally inconsistent analysis where the main rule is current but supporting rules are outdated.

## Self-Check Questions

- [ ] Did I read ALL local reference files before starting web searches?
- [ ] Did I identify temporal markers (dates, amendment references) in local materials?
- [ ] Did I verify every specific value (time period, threshold, date) against current web sources?
- [ ] Did I use authoritative web sources (official sites, not secondary commentary)?
- [ ] Did I explicitly document any conflicts found between local and web data?
- [ ] Is my final answer based only on verified-current information?

## Technical Notes

- **Legal amendment tracking**: Laws are frequently amended. When a local file cites "Article X of Law Y," verify whether Law Y has been amended since the local file was compiled. Search for the current effective version specifically.
- **Jurisdiction scope**: Local legal files may reference a specific jurisdiction. Ensure web verification searches within the same jurisdiction, not general international sources.
