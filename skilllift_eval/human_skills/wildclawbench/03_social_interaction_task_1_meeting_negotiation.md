---
name: 03-social-interaction-task-1-meeting-negotiation
description: Use when coordinating multi-party schedules with evolving constraints. Focuses on contradiction detection, timezone handling, and constraint validation.
---

# Multi-Party Schedule Coordination

## Core Challenge

Multi-party scheduling with incomplete information requires iterative constraint discovery. Participants reveal constraints across multiple rounds, sometimes contradicting earlier statements. Agents must detect contradictions, handle timezone conversions, and validate solutions against ALL discovered constraints—not just the obvious ones.

## Solution Strategy

1. **Map the constraint space first**: Before proposing any time, extract ALL constraints from initial communications: timezones, existing conflicts, room requirements, duration, deadline, participant list. Read everything including P.S. notes and footer text. Common mistake: agents scan for bullet points and miss critical details buried in prose or footnotes.

2. **Detect contradictions proactively**: When a participant's statement has internal inconsistencies (e.g., "available 9:30–16:00" paired with "leading a meeting 9:00–10:30"), STOP immediately and request clarification. Don't pick the interpretation that seems convenient. Common mistake: agents assume one statement is more recent or more authoritative without confirmation.

3. **Make conversions explicit and written**: Timezone conversions, duration calculations, overlap analysis—write them down step-by-step before using. Never do calendar math in your head. Common mistake: mental arithmetic introduces off-by-one errors, especially with timezone DST assumptions.

4. **Validate globally, not locally**: After finding a candidate time, re-check it against EVERY constraint you've discovered. One participant being available is necessary but not sufficient. Common mistake: agents propose the first time that satisfies 80% of constraints and miss deal-breakers.

5. **Respect constraint hierarchy**: Original request > explicit requirements > participant preferences. If the original request specifies Room B, use Room B even if all participants later request Room A. Common mistake: agents treat all inputs as equal weight and use majority rule where they shouldn't.

## Decision Points

- **When to clarify vs proceed**: If information contradicts → ALWAYS clarify first. If information is incomplete but consistent → proceed with documented assumptions and state them explicitly.

- **How to handle timezone mentions**: If a participant mentions a city or timezone, convert immediately and write both versions (original + converted). Treat all times as suspect until timezone is confirmed.

- **Whether to propose immediately or gather more rounds**: If you haven't heard from all participants, don't propose yet. If you have full data but contradictions exist, clarify before proposing. Only propose when you have consistent, complete data.

## Common Failure Patterns

- **Trusting first-scan data**: Agents optimize for speed and stop reading after finding "enough" information. P.S. sections, footnotes, and parenthetical remarks often contain critical constraints (room assignments, conflicts, timezone notes) that get skipped. → Missing requirements, failed validation.

- **Picking convenient interpretations**: When faced with contradictory data, agents choose the interpretation that makes their current plan work instead of asking for clarification. → Proposing times that violate actual constraints.

- **Mental timezone arithmetic**: "JST is UTC+9, Beijing is UTC+8, so 10:00 JST = 09:00 Beijing" done in-head. → Off-by-one hour errors from DST assumptions or miscalculation.

- **Accepting participant suggestions uncritically**: Participants may suggest workarounds (delete this event, use this room) that violate higher-level constraints. → Breaking original requirements to satisfy local preferences.

- **Optimizing for first solution**: Agents find a time that works and stop. In tightly constrained problems, there's often only ONE valid solution that requires checking ALL constraints simultaneously. → Proposing sub-optimal or invalid times.

## Self-Check Questions

- [ ] Did I read every message completely, including P.S. sections and footer text?
- [ ] Did I extract constraints from ALL participants before proposing a time?
- [ ] Did I detect any contradictions in participant responses and clarify them?
- [ ] Did I explicitly write down timezone conversions before using them?
- [ ] Did I validate my proposed time against ALL discovered constraints?
- [ ] Did I check the original request for requirements that override participant preferences?
- [ ] Did I verify that my proposed duration satisfies the minimum requirement?
- [ ] Did I confirm the solution with all participants before finalizing?

## Technical Notes

- **Timezone conversion**: When converting between timezones, specify both the original and converted time explicitly in your working notes. If DST is ambiguous, state your assumption.

- **Conflict detection**: If a participant lists availability A-B but mentions an event within that range, the event takes precedence. Treat stated availability as provisional until cross-checked.
