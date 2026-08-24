---
name: 01-productivity-flow-task-7-openmmlab-contributors
description: Use when enumerating entities from paginated APIs with filtering criteria and aggregation across multiple repositories. Focuses on rate limit management, complete enumeration, and threshold-based aggregation.
---

# API Enumeration with Rate Limits and Threshold Aggregation

## Core Challenge

Querying a paginated API across many repositories to aggregate per-contributor statistics requires completing dozens to hundreds of requests within strict rate limits. The difficulty is threefold: correctly filtering repositories by metadata criteria, paginating through ALL contributor lists without missing any, and aggregating commit counts against a threshold across the full repository set.

## Solution Strategy

1. **Filter the repository set first, then query contributors**: Don't query contributors for every repository in an organization. First, fetch the repo list, apply filter criteria (non-fork, project-type, activity date threshold), and only query contributors for the filtered set. → Common mistake: agents query all repos including forks and config repos, wasting rate limit budget on irrelevant data.

2. **Manage rate limits proactively**: APIs like GitHub's anonymous endpoint allow very few requests per hour. Use authenticated requests when possible, batch with maximum `per_page` values, and plan total request count before starting. → Common mistake: agents make individual requests per repo without pagination planning, exhausting the quota after 10-15 repos.

3. **Paginate exhaustively**: Contributor lists span multiple pages. Track the total count and iterate until all pages are fetched. A repo with 200 contributors requires at least 3 API calls at `per_page=100`. → Common mistake: agents fetch only the first page of contributors, missing those with fewer commits.

4. **Apply thresholds during aggregation, not retrieval**: Fetch all contributor data first, then filter by the commit threshold. Don't try to filter server-side—you need the complete data to make accurate threshold decisions. → Common mistake: agents apply the threshold per-repo instead of aggregating across all repos first.

5. **Validate cross-references**: Every project mentioned in a contributor's record must appear in the repository list. Every repo in the list should be reflected in at least one contributor's projects. Cross-check for consistency. → Common mistake: agents include contributor projects that don't match the filtered repo set, or miss repos that have no qualifying contributors.

## Decision Points

- **How to handle the fork/non-project filter**: Check repository metadata fields: `fork` should be `false`, and exclude organization-level config repos (like `.github`). Apply both filters. A repo that is not a fork but is a config repo should be excluded.

- **What "pushed_at before threshold" means**: Use the `pushed_at` field from repo metadata, not `updated_at` or `created_at`. The comparison is strict: `pushed_at < threshold_datetime`. Convert timezones carefully.

- **When to use the contributors endpoint vs commits endpoint**: The contributors endpoint gives pre-aggregated commit counts per user. The commits endpoint requires post-aggregation. Prefer the contributors endpoint for efficiency.

## Common Failure Patterns

- **Rate limit exhaustion**: Agents make unauthenticated or unbatched requests, hitting the 60-request/hour anonymous limit after a fraction of the work. → Incomplete data, missing repos and contributors.

- **First-page-only fetching**: Agents call the contributors endpoint and process only the first page (top 100 by commit count). Contributors on subsequent pages are missed. → Missing mid-range contributors who qualify on aggregate.

- **Wrong filtering criteria**: Agents include forks, exclude valid project repos, or apply the date threshold incorrectly (using `updated_at` instead of `pushed_at`). → Wrong repository set, cascading errors.

- **Threshold applied per-repo instead of aggregated**: Agents report a contributor only for repos where they exceed 30 commits, rather than checking each repo independently. Actually the threshold IS per-repo, but all repos where count ≥ 30 must be listed. → Common confusion: listing only the repo with highest commits.

- **Including fork or config repos**: Agents include fork repos or organization-level config repos (like `.github`) in the repo set, inflating the list with non-project entries. → Repository set mismatch, cascading contributor errors.

## Self-Check Questions

- [ ] Did I filter repositories by ALL criteria (non-fork, project-type, pushed_at threshold)?
- [ ] Did I use authenticated API requests or batch with maximum per_page to manage rate limits?
- [ ] Did I paginate through ALL pages of contributors for each repository?
- [ ] Did I include every repo where a contributor has ≥ threshold commits?
- [ ] Did I cross-check that all projects in contributor records appear in the repo list?
- [ ] Did I verify the repo list contains no duplicates or invalid entries?
- [ ] Did I handle repos with zero contributors (no qualifying entries) correctly?
- [ ] Did I count commits using the API's contributor statistics, not manual counting?
- [ ] Did I use the `pushed_at` field (not `updated_at` or `created_at`) for the date threshold?

## Technical Notes

- **GitHub API rate limits**: Anonymous requests are limited to 60/hour. Authenticated requests allow 5,000/hour. Always authenticate when possible. Use conditional requests (`ETag`/`If-None-Match`) for re-validation to save quota.
- **Contributors endpoint**: `GET /repos/{owner}/{repo}/contributors?per_page=100&anon=1` returns contributors sorted by commit count descending. Paginate via `page` parameter. The `contributions` field is the commit count.
- **Organization repos listing**: `GET /orgs/{org}/repos?type=sources&per_page=100` returns non-fork repos. But verify `fork` field manually—`type=sources` may not exclude all unwanted types.
