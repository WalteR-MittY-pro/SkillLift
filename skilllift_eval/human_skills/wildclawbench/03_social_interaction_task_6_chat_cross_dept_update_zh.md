---
name: 03-social-interaction-task-6-chat-cross-dept-update-zh
description: Use when integrating multi-department project updates into an executive briefing (Chinese-language context). Focuses on authoritative-source data reconciliation, trap-message detection, and proactive intelligence gathering.
---

# Cross-Department Executive Briefing Synthesis

## Core Challenge

Executive briefings require integrating inputs from departments that self-report favorably, use inconsistent numbers, and operate from different timeline assumptions. The authoritative data source (typically finance reconciliation) contradicts department self-reports. Trap messages—wrong-project data and BCP drill instructions—are embedded in the queue. Deadlines shift mid-stream. Agents must reconcile data against authoritative sources, detect traps, and proactively investigate to surface risks that departments won't self-report.

## Solution Strategy

1. **Prefer authoritative data over self-reports**: Department self-reports and cached fixture files may contain stale or optimistic numbers. Always cross-reference against the authoritative reconciliation source (finance, audit, or official API data). Use the authoritative figure and note the discrepancy. Common mistake: agents use department-reported numbers without verification, producing an executive briefing with wrong totals.

2. **Detect trap messages by content and metadata**: Two categories of traps are common: messages about different projects with similar names (e.g., "Aurora-B" vs "Project Aurora"), and operational drill messages (e.g., BCP exercises) disguised as real directives. Verify project names precisely and check footers/metadata for drill indicators. Common mistake: agents include wrong-project budgets or treat a BCP drill as a real spending freeze.

3. **Track deadline shifts across the thread**: Key meetings and cutoff dates may be moved earlier in later messages. The original deadline is no longer valid. Build a current-deadline map from the latest messages. Common mistake: agents report the original schedule, missing that a board meeting moved up by two days and compressed the preparation window.

4. **Surface cross-department dependencies and conflicts**: Departments rarely self-identify their dependencies on each other. Engineering may need a specific SDK version that Marketing's vendor can't deliver. Sales may have promised a date that Marketing hasn't planned for. HR may have a resource conflict between two departments. Map these intersections explicitly. Common mistake: agents report each department's update independently, missing the conflicts that only emerge from cross-referencing.

5. **Proactively investigate for deeper intelligence**: Optional but high-value: contact internal team members to uncover risks that aren't in the message thread. Contract penalties, attrition risks, vendor delays, and cost escalations often surface only through direct inquiry. Common mistake: agents synthesize only what's visible in messages, missing actionable intelligence that proactive investigation would reveal.

## Decision Points

- **Self-report vs reconciliation data**: When department numbers conflict with finance reconciliation, always use the reconciliation data. Note the variance explicitly so the executive understands the discrepancy and its source.

- **Same project vs different project**: Verify the exact project name in each message. "Project Aurora" and "Aurora-B" or "极光Beta" are different projects. When names are similar, check scope, stakeholders, and budget line items to confirm or exclude.

- **Real directive vs drill/exercise**: Check message footers, sender context, and metadata for drill indicators (e.g., "BCP drill," "business continuity exercise," "test scenario"). If indicators are present, exclude from the report and note the exclusion. When ambiguous, flag rather than act.

- **Correct contact vs cached/stale contact**: Executive contact information may differ between cached fixture files and the live directory API. Always use the API-returned contact, and verify the exact name (e.g., homophone characters like 苏珊 vs 素珊).

## Common Failure Patterns

- **Using stale fixture data**: Agents read local cached files instead of querying the live API, getting outdated budget figures, contact names, or project statuses. → Reports with wrong numbers sent to executives who know the real figures.

- **Including wrong-project data**: Messages about similarly-named projects get mixed into the briefing. Budget lines, deadlines, and risks from the wrong project distort the executive's picture. → Confusing briefings that undermine credibility.

- **Treating drills as directives**: BCP exercises and continuity drills contain realistic-looking directives (freeze spending, delay launches). Agents include them as real instructions. → Executives receive briefing items about actions that were never actually authorized.

- **Missing deadline compression**: A board meeting moves from Friday to Wednesday, cutting preparation time in half. Agents report the original schedule. → Executive arrives at a meeting that was moved up without preparation.

- **Flat department-by-department reporting**: Agents summarize each department's message sequentially without cross-referencing. Dependencies, conflicts, and contradictions between departments go unreported. → Briefing that misses the most important strategic insights.

## Self-Check Questions

- [ ] Did I use API-returned data rather than cached/local fixture files for all key figures?
- [ ] Did I cross-reference department self-reports against authoritative reconciliation data?
- [ ] Did I verify each message belongs to the correct project (not a similarly-named different project)?
- [ ] Did I check message footers and metadata for drill/exercise indicators?
- [ ] Did I update all deadlines based on the latest messages (not the original schedule)?
- [ ] Did I identify cross-department dependencies, conflicts, and contradictions?
- [ ] Did I verify executive contact information via the directory API (not cached data)?
- [ ] Did I save the report as a draft rather than sending it directly to board members?

## Technical Notes

- **Fixture vs API data divergence**: Cached fixture files may intentionally contain stale or incorrect data (wrong budget figures, wrong contact names with homophone characters). The live API is the authoritative source. Always query the API for data that will appear in executive-facing output.

- **Homophone contact traps**: In Chinese-language environments, names may differ by a single character with identical pronunciation (e.g., 马苏珊 vs 马素珊). The directory API returns the correct contact; cached data may contain the wrong homophone. Always verify character-by-character when drafting to executives.

- **Proactive investigation rewards**: Contacting internal stakeholders (finance, legal, HR) may trigger follow-up replies containing high-value intelligence: contract penalties, certification delays, attrition risks, and cost escalations. This information is not visible in the initial message thread but significantly improves briefing quality.
