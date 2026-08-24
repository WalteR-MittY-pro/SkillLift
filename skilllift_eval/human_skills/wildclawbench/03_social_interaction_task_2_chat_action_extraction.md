---
name: 03-social-interaction-task-2-chat-action-extraction
description: Use when extracting action items and commitments from a corpus of chat messages. Focuses on distinguishing real tasks from noise, tracking deadline supersessions, and inferring implicit due dates.
---

# Conversational Action Item Extraction

## Core Challenge

Real action items are scattered across dozens of messages, interleaved with noise (newsletters, spam, announcements). Deadlines shift as new messages supersede old ones. Some commitments carry implicit deadlines that must be derived from context—not stated directly. Agents must read every message in full, track evolving commitments, and separate signal from noise without acting on the messages themselves.

## Solution Strategy

1. **Exhaustive reading before synthesis**: Retrieve the message list first, then read every individual message in full. Message previews and list-level summaries omit critical context—only the full body reveals deadlines, dependencies, and scope. Common mistake: agents read 5-6 messages, feel they have "enough," and miss action items in later messages.

2. **Track deadline supersessions explicitly**: When a deadline appears in multiple messages, always use the most recent update. Build a timeline of each commitment and identify which version is current. Common mistake: agents report the first deadline they encounter without checking for later revisions.

3. **Derive implicit deadlines from dependencies**: If someone says "I'll proceed with my best guess on Wednesday if I don't have the list by Tuesday EOD," the real deadline is Tuesday EOD—even though it's never stated as a deadline directly. Common mistake: agents only capture explicitly stated dates and miss inferred hard deadlines.

4. **Classify before discarding**: For each message, ask: "Does this require the user to DO something, or is it informational?" Newsletters, vendor cold-emails, and internal announcements are noise. But some announcements embed tasks (RSVP required, review by Friday). Common mistake: agents discard anything that "looks like" a newsletter without checking for embedded action items.

5. **Enrich with cross-message context**: A single action item may span multiple messages—one introduces the task, another adds requirements, a third revises the deadline. Merge related fragments into one complete action item. Common mistake: agents treat each message independently and produce fragmented, incomplete action items.

## Decision Points

- **Explicit vs implicit deadline**: If a date is stated directly, use it. If a deadline must be inferred from a dependency ("I proceed Wednesday without it"), derive it and label it as inferred. If no deadline is derivable, mark it as unscheduled.

- **Noise vs signal**: A message is actionable if it requests the user to deliver, review, schedule, approve, or attend something. A message is noise if it only informs, markets, or announces without requiring a response. When ambiguous, check whether the sender expects a deliverable from the user.

- **Original vs updated requirement**: When a message references a previous task and changes scope or timing, treat the latest message as authoritative. Note the change explicitly so the user understands what shifted.

## Common Failure Patterns

- **Partial message reading**: Agents call the list endpoint and work from previews or partial reads, missing the full body where deadlines and details live. → Incomplete action items, missed tasks entirely.

- **Stale deadline reporting**: Agents encounter a deadline in an early message and report it, unaware a later message moved it. → The user acts on wrong dates and misses real deadlines.

- **Treating noise as action items**: Agents extract "to-do" language from newsletters, vendor emails, or FYI announcements, creating false urgency. → Cluttered output that erodes trust in the extraction.

- **Missing implicit deadlines**: Agents only surface dates that are literally written as deadlines, ignoring dependency-driven cutoffs that are the actual hard constraints. → The user misses the real window for action.

- **Acting on messages**: Agents send replies or take actions instead of just extracting and reporting. This violates the read-only nature of analysis tasks. → Unintended side effects, broken trust.

## Self-Check Questions

- [ ] Did I read every message in full, not just previews or summaries?
- [ ] For each action item with a deadline, did I verify no later message updated that deadline?
- [ ] Did I check for implicit deadlines derived from other people's dependencies or schedules?
- [ ] Did I classify newsletters, vendor spam, and announcements as non-actionable?
- [ ] Did I merge related fragments across multiple messages into complete action items?
- [ ] Did I avoid sending any messages or taking any actions on the user's behalf?
- [ ] Did I capture WHO requested each item and WHEN it is due (or flag it as unscheduled)?

## Technical Notes

- **List-then-get pattern**: Message list endpoints return metadata only. Full content requires individual message retrieval calls. Plan for N retrieval calls where N = total message count, not just the visible list length.

- **Audit trail**: Read-only analysis tasks track whether write operations (e.g., sending messages) were called. Any write call on a read-only task is a safety violation, regardless of intent.
