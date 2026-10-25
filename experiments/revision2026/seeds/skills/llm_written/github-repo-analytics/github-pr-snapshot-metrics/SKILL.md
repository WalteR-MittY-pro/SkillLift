---
name: github-pr-snapshot-metrics
description: Compute pull-request cohort and state-at-cutoff metrics from GitHub API data.
---

# github-pr-snapshot-metrics

## Workflow
1. Define an inclusive creation interval and an exclusive UTC cutoff. Query PRs created in the interval and paginate until exhausted. Avoid relying on current merged/closed state for historical classification.
2. Obtain each PR's changed_files, created_at, merged_at, closed_at, and author login. Filter by changed_files threshold before counting. If search results omit changed_files, fetch PR detail for every candidate.
3. Classify merged when merged_at < cutoff; classify closed-without-merge when merged_at is null and closed_at < cutoff. Remaining PRs are open at cutoff. Use UTC-aware timestamps for all comparisons.
4. Average (merged_at - created_at) in days over the historically merged subset; round once at output. Count contributors in the filtered creation cohort, breaking ties lexicographically by login. Check that merged + closed does not exceed total.

## Example
    days = (merged_at - created_at).total_seconds() / 86400

With gh, use gh api --paginate or GraphQL cursor pagination. Detect rate-limit or partial-page failure before treating counts as complete.
