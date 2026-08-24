---
name: 04-search-retrieval-task-6-excel-with-search
description: Use when answering questions that require filtering structured spreadsheet data under multiple constraints, then enriching with web search. Focuses on constraint decomposition, cross-source integration, and threshold computation.
---

# Structured Data Filtering with Web Enrichment

## Core Challenge

The task requires identifying a specific data record from a spreadsheet using multiple simultaneous filters, then answering a follow-up question that requires external web data and arithmetic reasoning across sources. The challenge is decomposing a compound question into its filter conditions, executing them faithfully, and integrating local data with web-retrieved values without mixing up units or reference years.

## Solution Strategy

1. **Parse the compound question into atomic filter conditions**: Break multi-clause constraints into individual checkable predicates (e.g., "state = X", "role = Y", "service level = Z", "maximize metric W"). Each must be applied independently. → Holding compound conditions mentally leads to applying some and forgetting others.

2. **Apply filters in the most selective order**: Start with the filter that eliminates the most rows (e.g., a rare role or service level). This reduces the working dataset quickly and makes subsequent filters easier to verify visually. → Applying broad filters first leaves hundreds of rows to scan manually.

3. **Verify the filtered result against ALL original constraints**: After filtering, re-check the surviving rows against every stated condition. Spreadsheet data has edge cases (case sensitivity, trailing spaces, category naming variations) that filter logic may miss. → Trusting the first filter pass without cross-verification risks selecting a row that technically fails a constraint due to data formatting.

4. **Decompose the follow-up question into its computational components**: Identify what values are needed (current metric, threshold for next category) and where each comes from (local spreadsheet vs web search). Write down the formula explicitly before computing. → Computing in-head without decomposing the formula leads to subtracting the wrong values or using the wrong year's data.

5. **Cross-reference web data with the correct matching entity**: When web search provides the threshold or comparison value, ensure it corresponds to the exact entity identified locally (same airport, same year, same classification system). Classification definitions change between years. → Using a web-sourced threshold from a different classification system or year produces an answer that's arithmetically correct but semantically wrong.

## Decision Points

- **Filter via code vs manual inspection**: For datasets under ~100 rows after initial filtering, manual verification is feasible and catches formatting issues. For larger datasets, use programmatic filtering but add explicit sanity checks on the output.

- **Which worksheet or file holds the answer**: Multi-file Excel tasks require understanding the relationship between files. Check which file contains which columns before filtering. Don't assume a single file has all data.

- **When web data conflicts with local data**: Prefer the most recent authoritative source. If the local file is from a specific year and the web provides updated data, determine which year the question refers to before choosing.

## Common Failure Patterns

- **Applying only 2 of 3+ constraints**: Agents filter on the most obvious conditions (state + role) and miss the third (service level), selecting the wrong row. → Identifying a different airport than the correct answer.

- **Year confusion in cross-source reasoning**: The local file has data for year A, the web search returns a threshold for year B, and the question asks about year C. Agents conflate these. → Computing the right formula with the wrong year's values.

- **Sorting instead of filtering**: Agents sort by the target metric and pick the top row without applying categorical constraints, selecting a row that has the highest value but fails a category requirement. → Recommending a row that maximizes the metric but violates a filter condition.

- **Unit or definition mismatch**: Web-sourced thresholds use different units or category definitions than the local data. Agents compute without aligning definitions. → Arithmetically correct but semantically incorrect answers (e.g., "primary" defined differently across sources).

## Self-Check Questions

- [ ] Did I decompose the compound question into individual filter conditions?
- [ ] Did I apply ALL constraints, not just the most obvious ones?
- [ ] Did I verify the surviving rows against every original constraint?
- [ ] Did I write down the computation formula before calculating?
- [ ] Did I confirm the web-sourced value corresponds to the same entity, year, and classification system?
- [ ] Did I check the correct worksheet within multi-sheet Excel files?

## Technical Notes

- **Spreadsheet data cleaning**: Excel cells may contain trailing spaces, non-breaking spaces, or inconsistent casing that break exact-match filters. Strip whitespace and normalize case before filtering programmatically.
- **Multi-file relationships**: When a task references multiple Excel files, determine whether they are snapshots from different years, different views of the same data, or complementary datasets. The relationship determines which file to use for each sub-question.
