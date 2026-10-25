---
name: excel-two-key-gdp-formulas
description: Fill two-key Excel lookups and calculate GDP-weighted net-export statistics without changing workbook formatting.
---

# excel-two-key-gdp-formulas

## Workflow
1. Inspect both worksheets, series-code column, year header row, source ranges, and yellow target ranges. Record existing styles before editing. Determine source units for exports, imports, and GDP.
2. Use INDEX with two MATCH functions so each target uses its row's series code and column's year. Example: =INDEX(Data!$H$21:$L$40,MATCH($D12,Data!$D$21:$D$40,0),MATCH(H$10,Data!$H$20:$L$20,0)). Adjust all ranges to the actual workbook; avoid hardcoded returned values.
3. Compute net exports as (exports - imports)/GDP with the requested percentage scaling. For the cross-country weighted mean, use SUMPRODUCT of percentage results and GDP weights divided by SUM of GDP weights, or equivalently total net exports divided by total GDP, keeping units consistent.
4. Calculate MIN, MAX, MEDIAN, AVERAGE, PERCENTILE.INC for 25th and 75th percentiles where available. Round in the formula or display to one decimal as required; do not double-scale values.
5. Save with openpyxl preserving existing cell styles. Recalculate in a spreadsheet engine, then inspect formulas, cached/displayed values, percent scale, and style IDs against the original. Do not add sheets, macros, or VBA.

## Example
    weighted = SUMPRODUCT(percent_range, gdp_range)/SUM(gdp_range)

A formula string in openpyxl is not a computed result; use a calculation engine for final verification.
