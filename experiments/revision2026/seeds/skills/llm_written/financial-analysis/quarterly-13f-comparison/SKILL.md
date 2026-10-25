---
name: quarterly-13f-comparison
description: Join quarterly 13F filing tables by accession and CUSIP for fund-level and holding-change analysis.
---

# quarterly-13f-comparison

## Workflow
1. Inspect the extracted quarter folders and their schemas. Identify COVERPAGE, fund summary, and holdings tables; do not assume the same accession across quarters.
2. Normalize manager names and use fuzzy search only to propose candidates. Confirm the candidate with filing metadata, CIK, reporting period, and manager identity. Treat similarly named affiliates separately.
3. Join a quarter's accession to its summary to obtain AUM and holding count. Check whether value units are dollars or thousands; report the dataset's definition rather than silently substituting market value for AUM.
4. To compare holdings, aggregate by CUSIP within each quarter, outer join, fill truly absent positions with zero, and calculate current minus previous value. Rank positive increases by dollar amount, using deterministic tie handling.
5. Resolve a target issuer to CUSIP from security metadata, then rank current-quarter holder values; join manager names through each holding's accession.

## Example
    change = q3.groupby('cusip').value.sum().sub(q2.groupby('cusip').value.sum(), fill_value=0)

Validate one manager and one CUSIP manually from source rows. Large archives unavailable in a preparation context must be examined in the execution environment.
