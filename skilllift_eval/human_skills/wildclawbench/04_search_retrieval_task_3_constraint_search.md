---
name: 04-search-retrieval-task-3-constraint-search
description: Use when searching for products or items that must satisfy multiple simultaneous constraints. Focuses on infeasibility detection, constraint relaxation strategy, and near-match recommendation.
---

# Multi-Constraint Product Search with Infeasibility Detection

## Core Challenge

When a user specifies many constraints (6-7 criteria), the probability that any single product satisfies ALL of them drops sharply. The key cognitive challenge is recognizing infeasibility — that no item meets every constraint — rather than forcing a false positive, then systematically identifying the closest near-misses.

## Solution Strategy

1. **Enumerate constraints as a checklist before searching**: Write down every constraint explicitly with its exact threshold (e.g., "battery ≥ 5400mAh", "storage = 512GB"). This checklist drives the verification logic. → Agents hold constraints in working memory, conflate similar ones, and miss subtle differences between "above" and "at least."

2. **Search systematically per constraint combination**: Start by searching for products matching the most restrictive or unusual constraints first (e.g., "satellite communication" is rarer than "512GB storage"). Unusual constraints eliminate candidates fastest. → Starting with common constraints returns too many results that fail on rare ones later.

3. **Test for infeasibility explicitly**: After evaluating top candidates against ALL constraints, if none passes every check, conclude infeasibility. State clearly that no single product satisfies all requirements. → Forcing a "best match" as if it meets all constraints is a critical error — it misleads the user.

4. **Rank near-misses by constraint satisfaction count**: For products that miss 1-2 constraints, enumerate exactly which constraints are met and which are not. Present multiple alternatives rather than a single "closest." → Recommending only one near-miss denies the user choice between different trade-offs.

5. **Verify specifications from authoritative sources**: Manufacturer pages and reputable review sites are authoritative. Forum posts and retail listings may contain errors. Cross-check critical specs (sensor size, chipset, battery capacity) against multiple sources. → Trusting a single retail listing for specs leads to incorrect constraint evaluation.

## Decision Points

- **How many constraints must fail before declaring infeasibility**: If even one constraint cannot be met by any product, the full set is infeasible. Report this immediately rather than continuing to search for a non-existent perfect match.

- **Which constraints to relax for near-miss recommendations**: Relax soft constraints (preferences) before hard constraints (requirements). Hardware specs (chipset, sensor) are usually hard; capacity tiers may have acceptable alternatives.

- **How many near-misses to recommend**: At least 3-4 alternatives covering different trade-off profiles, so the user can choose which constraint to sacrifice.

## Common Failure Patterns

- **Assuming a product meets all constraints without verification**: Agents find a product that seems to match and report it as fully compliant without checking every spec against every constraint. → Recommending a product that actually fails 2-3 constraints, which is factually wrong.

- **Over-trusting marketing summaries**: Marketing pages use terms like "flagship camera" without specifying sensor size. Agents treat these as constraint-matching. → Claiming a product has a "1-inch sensor" based on marketing copy rather than verified specifications.

- **Declaring infeasibility without recommending alternatives**: Agents correctly determine no product matches all constraints but stop there, offering no near-miss recommendations. → Incomplete answer that doesn't help the user make a purchasing decision.

- **Constraint conflation**: Agents merge or blur distinct constraints — treating "Snapdragon 8 Gen 3" as matching "Snapdragon 8 Gen 2," or "above 5400mAh" as matching "5000mAh." → Incorrectly claiming constraint satisfaction through fuzzy matching of distinct specs.

## Self-Check Questions

- [ ] Did I write down every constraint with its exact threshold before searching?
- [ ] Did I search using the most restrictive/unusual constraints first?
- [ ] Did I verify each candidate against EVERY constraint, not just most?
- [ ] If no product meets all constraints, did I explicitly state infeasibility?
- [ ] Did I recommend multiple near-miss alternatives with clear trade-off analysis?
- [ ] Did I verify specifications from manufacturer pages or reputable reviews?
- [ ] Did I clearly label which constraints each near-miss does NOT satisfy?

## Technical Notes

- **Spec sheet ambiguity**: Manufacturers sometimes list "effective" vs "physical" sensor sizes differently. Verify the exact metric the constraint refers to (e.g., optical format vs diagonal measurement).
- **Regional variants**: The same product model may have different specs in different markets (e.g., different chipsets for different regions). Verify the specification applies to the relevant market.
