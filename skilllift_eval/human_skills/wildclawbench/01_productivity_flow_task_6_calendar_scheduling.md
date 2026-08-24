---
name: 01-productivity-flow-task-6-calendar-scheduling
description: Use when resolving scheduling conflicts under hard and soft constraints with priority optimization. Focuses on constraint satisfaction, conflict detection, and optimal prioritization.
---

# Constraint Satisfaction Scheduling

## Core Challenge

Scheduling new meetings into an existing calendar requires satisfying multiple interacting hard constraints (attendee availability, lunch breaks, daily caps, preferred windows) while maximizing total priority weight. The difficulty is that constraints interact: placing one meeting consumes attendee slots and time windows, affecting future placements. This is an optimization problem, not a greedy assignment—locally optimal choices can block globally better solutions.

## Solution Strategy

1. **Parse and index all constraints before scheduling**: Read the calendar, requests, and rules files completely. Build a structured representation: existing events by attendee, availability windows, daily caps, lunch hours, hard exclusions. Only then attempt scheduling. → Common mistake: agents start placing meetings while still discovering constraints, violating rules they haven't read yet.

2. **Respect hard constraints absolutely**: Hard constraints (lunch break, daily meeting cap, attendee unavailability, required attendees must be present) are inviolable. A single violation often zeroes the entire score. Check each constraint explicitly for every placement. → Common mistake: agents treat daily caps or lunch breaks as soft preferences, leading to zero-score submissions.

3. **Optimize globally by priority, not greedily**: When not all requests fit, the goal is maximizing total priority weight, not maximizing count. A high-priority meeting is worth more than multiple low-priority ones. Try different orderings. → Common mistake: agents schedule in request order or by first-fit, missing higher-value configurations.

4. **Preserve original events unchanged**: Existing calendar events must appear in the output unchanged. Don't modify, delete, or reschedule them. They occupy time that new meetings must work around. → Common mistake: agents modify or drop original events to make room for new meetings.

5. **Document rejection reasons precisely**: For each unscheduled request, provide a specific reason: which constraint was violated, which attendee was unavailable, which competing request won the slot. Generic reasons like "no time available" are insufficient. → Common mistake: agents provide vague rejection reasons that don't identify the blocking constraint.

## Decision Points

- **When two requests compete for the same slot**: Compare priority weights. Schedule the higher-priority request and reject the lower-priority one with reason "lower_priority_than_competing_request". If equal priority, prefer the one whose attendees have fewer alternative slots.

- **Preferred window vs flexible scheduling**: If the constraints say "schedule only within preferred windows", never place a meeting outside the stated windows. If flexible scheduling is allowed, you may search beyond preferred windows—but check this rule explicitly.

- **Required vs optional attendees**: Required attendees MUST be available for a meeting to be scheduled. Optional attendees are nice-to-have. Missing a required attendee means the meeting cannot be placed at that time.

## Common Failure Patterns

- **Hard constraint violations**: Agents miss a constraint type (e.g., attendee unavailability on specific weekdays) and schedule a meeting that violates it. → Score zeroes entirely; no partial credit.

- **Greedy instead of optimal**: Agents schedule requests in order of appearance, filling the first available slot for each. This produces a valid but suboptimal schedule that misses high-priority meetings blocked by earlier low-priority placements. → Low optimality ratio.

- **Destroying original events**: Agents modify the original calendar to "optimize" it, deleting or resrolling existing events. → Fails the "preserve original events" hard check.

- **Missing duration enforcement**: Agents place a meeting but set the wrong duration (e.g., 60 minutes instead of the requested 30). → Fails duration check, potentially zeroing the score.

- **Inconsistent scheduled/unscheduled sets**: Agents schedule a request but also list it in unscheduled, or forget to list an unscheduled request. → Fails the "request coverage consistent" check.

- **Ignoring timezone in constraints**: Attendee unavailability rules are often specified in local time. Agents apply them in UTC or vice versa, creating false conflicts or missing real ones. → Meetings placed in wrong time slots.

## Self-Check Questions

- [ ] Did I read and index ALL constraints before attempting any placement?
- [ ] Did I verify every scheduled meeting against ALL hard constraints (lunch, daily cap, availability, windows)?
- [ ] Did I preserve all original calendar events unchanged in the output?
- [ ] Did I maximize total priority weight, not just meeting count?
- [ ] Did I set the correct duration for each scheduled meeting?
- [ ] Did I ensure every request appears in exactly one of scheduled or unscheduled?
- [ ] Did I provide specific reason codes and texts for each unscheduled request?
- [ ] Did I verify that no attendee has double-booked meetings?
- [ ] Did I check timezone conversions for all time-based constraints?
- [ ] Did I verify the output calendar is valid iCalendar format?

## Technical Notes

- **iCalendar parsing**: ICS files use line folding (continuation lines start with space/tab). Unfold before parsing. DTSTART/DTEND may have TZID parameters or Z suffix for UTC. Parse timezone explicitly—naive UTC comparison across timezones causes false conflicts.
- **Optimization approach**: For small request counts (15-20), brute-force or backtracking search over priority-sorted orderings is feasible. For larger sets, use constraint propagation with priority-guided heuristics. Libraries like OR-Tools CP-SAT solver handle this efficiently.
