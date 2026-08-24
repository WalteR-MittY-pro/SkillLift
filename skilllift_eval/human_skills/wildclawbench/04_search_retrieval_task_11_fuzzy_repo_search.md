---
name: 04-search-retrieval-task-11-fuzzy-repo-search
description: Use when identifying a specific open-source project from a constellation of indirect descriptive clues. Focuses on candidate generation, clue-by-clue elimination, and differentiation between superficially similar projects.
---

# Multi-Clue Repository Identification

## Core Challenge

When a user describes a project through indirect attributes (implementation language, naming theme, creator's other work, technical contribution) rather than by name, the target must be disambiguated from a field of similar projects in the same domain. The challenge is that multiple projects match some clues, and only the intersection of ALL clues uniquely identifies the target.

## Solution Strategy

1. **Generate a broad candidate pool from the domain clue first**: Search for the general domain (e.g., "run LLMs locally without GPU") to enumerate all known projects. This gives you the candidate set to filter. → Searching for a specific clue first may return only one project, blinding you to alternatives that also match.

2. **Identify the most discriminative clues**: Not all clues are equally powerful. "Implemented in C/C++" eliminates Go/Rust/Python projects. "Creator also built a speech recognition tool" is highly specific. Rank clues by discriminative power and apply the strongest filters first. → Applying weak clues first leaves too many candidates and wastes verification effort.

3. **Cross-reference creator history for verification**: The clue about the creator's other work is often the strongest disambiguator. Check each candidate's creator profile for the referenced side project. Only one candidate's creator will match. → Ignoring the creator clue and matching only on project attributes, which multiple projects may satisfy.

4. **Verify the naming clue creatively**: "Animal associated with South America" is an indirect reference. Think about which candidate names reference animals and which of those animals are South American. This is often the confirming clue rather than the initial filter. → Taking the naming clue too literally and missing projects whose name indirectly references the animal.

5. **Confirm quantitative thresholds last**: Star count and other metrics are verification tools, not filters — they confirm the right candidate but don't help discriminate between similar projects. Check star count only after the qualitative clues identify the unique candidate. → Using star count as an early filter eliminates small but correct projects or includes large but wrong ones.

## Decision Points

- **When two candidates match most clues**: Apply the remaining unverified clues as tie-breakers. If both still match, check for subtle clue interpretations you may have missed (e.g., "creator" could mean original author vs current maintainer).

- **When the domain search returns too many candidates**: Apply the language constraint immediately (C/C++ projects only), then the creator-history constraint. These two filters typically reduce the pool to 1-2 candidates.

- **When no candidate matches all clues**: Re-examine each clue's interpretation. "Low-level systems language" might include Rust in some interpretations. "Animal associated with South America" might refer to the Andes, Amazon, or Pampas regions. Broaden interpretation before concluding no match.

## Common Failure Patterns

- **Committing to the first plausible candidate**: Agents find a well-known project that matches 3 of 4 clues and stop, missing that the 4th clue (creator history) disqualifies it. → Recommending a popular but wrong project (e.g., recommending the tool written in Go when the clue specifies C/C++).

- **Matching on domain alone**: Multiple projects serve the same domain ("run LLMs locally"). Agents recommend the most famous one without checking implementation language or creator history. → Recommending a project that matches the use case but not the specific technical attributes.

- **Ignoring the creator's other work**: The creator-history clue is the strongest discriminator but agents often skip it because it requires an extra search (creator profile). → Missing the unique identifier that separates the target from all impostors.

- **Treating the naming clue as decoration**: The naming clue ("animal from South America") seems vague but is highly specific when combined with the candidate pool. Agents dismiss it as unhelpful. → Failing to use a powerful discriminative clue.

## Self-Check Questions

- [ ] Did I generate a broad candidate pool from the domain before filtering?
- [ ] Did I apply the most discriminative clues first (language, creator history)?
- [ ] Did I verify the creator's other projects match the stated clue?
- [ ] Did I check the naming clue against the narrowed candidate list?
- [ ] Did I verify quantitative thresholds (star count) AFTER qualitative identification?
- [ ] Did I confirm that only ONE candidate satisfies ALL clues, not just most?

## Technical Notes

- **Creator cross-referencing**: GitHub user profiles list all public repositories. Checking a creator's profile for the referenced side project is a single page visit that definitively confirms or denies a candidate.
- **Naming interpretation**: Project names often reference their domain indirectly (llama references the model family, not directly an animal clue). Distinguish between names that are domain-referential and names that match the specific clue.
- **Quantitative metric timing**: Star counts grow over time. If the clue specifies a threshold, verify current data. Repositories that recently crossed a threshold may not have been indexed by secondary tracking sites yet.
