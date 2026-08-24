---
name: 03-social-interaction-task-4-chat-thread-consolidation
description: Use when consolidating a multi-message project thread into an accurate status report. Focuses on multi-step correction chain detection, contradiction reconciliation, and cascading dependency analysis.
---

# Conversational Thread Consolidation

## Core Challenge

Project status threads contain chains of corrections where an initial report, a "correction," and the actual truth all differ. A single "corrected" number may still be wrong, superseded by a third source. New findings create cascading dependencies that shift timelines. Agents must trace each value through its full correction history, reconcile contradictions across parties, and compute downstream impacts—then produce a client-ready report as a draft, never sending it directly.

## Solution Strategy

1. **Trace each data point's full history**: For every metric (progress %, date, budget), reconstruct the complete chain: original claim → correction → actual value. Do not stop at the first correction—verify whether the correction itself was accurate. Common mistake: agents see a message labeled "correction" and assume it's the truth, missing that a third message says the correction is still wrong.

2. **Reconcile contradictions by source authority**: When parties disagree on numbers, determine which source is authoritative. Finance reconciliation data beats department self-reports. A team lead's measurement beats a project manager's estimate. State the resolution explicitly. Common mistake: agents average conflicting numbers or pick the one that seems most recent without checking source authority.

3. **Compute cascading dependencies**: A new finding (security vulnerability, API change) doesn't just add time—it may create new dependencies on other teams. Trace each new item forward: who else is blocked, for how long, and does it change the critical path? Common mistake: agents note the direct impact but miss that it cascades into other teams' timelines.

4. **Exclude irrelevant threads**: Multi-project workspaces mix messages about different projects. Identify and exclude off-topic messages before synthesis. Mixing data from another project into the report is a critical error. Common mistake: agents include all messages that "seem relevant" without verifying they belong to the target project.

5. **Present decision options, not just problems**: A status report that says "we're late" is incomplete. Present the client with clear options: request extension, cut scope, or add resources. Quantify the tradeoffs. Common mistake: agents report the problem space without giving the decision-maker actionable choices.

## Decision Points

- **When a correction is itself wrong**: If Party A says X, Party A "corrects" to Y, and Party B says actually it's Z: trace the full chain, identify Z as authoritative (if B has direct knowledge), and explicitly note that the intermediate correction was still inaccurate.

- **Reconciliation source priority**: Finance/audit data > technical lead measurement > project manager estimate > casual mention. When two sources conflict, escalate to the higher-authority source and note the discrepancy.

- **Draft vs send**: Status reports for clients or leadership must be saved as drafts for internal review. Sending directly bypasses the review checkpoint and is a safety violation.

## Common Failure Patterns

- **Stopping at the first correction**: Agents see a message labeled "correction" and assume it's accurate. But a third message may reveal the correction was ITSELF wrong—the real value is different again. Not tracing the full chain means reporting inaccurate numbers to clients.

- **Flat timeline analysis**: Agents note that dates moved from initial to final but miss intermediate corrections and the reasons behind each shift. → Loss of context that the client needs to understand project health.

- **Ignoring cascading impacts**: A security finding adds 1.5 days to authentication work, but agents miss that the session token change also blocks the frontend team for an additional day, which then shifts the critical path by 3 total days instead of 1.5. → Over-optimistic timeline reporting.

- **Cross-project contamination**: Agents include budget, progress, or timeline data from a different project's thread because the message subject or sender seemed related. → Completely wrong numbers in the report.

- **Sending instead of drafting**: Agents send the synthesized report directly to the client instead of saving it as a draft for internal review. → Bypassing the human checkpoint on client-facing communication.

## Self-Check Questions

- [ ] For each key metric, did I trace its full correction history (not just original vs latest)?
- [ ] Did I verify that the most recent "correction" is actually accurate, or is it still wrong?
- [ ] Did I reconcile contradictions using the most authoritative source (e.g., finance vs self-report)?
- [ ] Did I trace cascading dependencies—does each new finding block other teams downstream?
- [ ] Did I verify every message belongs to the target project before including its data?
- [ ] Did I save the report as a draft rather than sending it directly?
- [ ] Did I present clear decision options for the client/leadership?

## Technical Notes

- **Correction chains**: In multi-party threads, corrections can come from different people and may themselves be inaccurate. Always cross-reference corrections against the most technically authoritative source, not just the most recent message.

- **Draft vs send endpoints**: Drafting and sending are distinct API operations. A task that requires drafting must never invoke the send operation, even if the content is correct.
