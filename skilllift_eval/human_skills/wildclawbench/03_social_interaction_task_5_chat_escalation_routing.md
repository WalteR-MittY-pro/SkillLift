---
name: 03-social-interaction-task-5-chat-escalation-routing
description: Use when triaging and routing a batch of customer issues by severity. Focuses on proactive investigation to unlock hidden context, disambiguating similar-named contacts, and identifying test/dummy messages from metadata.
---

# Customer Issue Triage and Escalation Routing

## Core Challenge

Surface-level triage of support messages produces wrong severity ratings and wrong routing decisions. The real picture requires proactively contacting internal teams to unlock follow-up context that transforms a routine issue into a critical escalation. Contact directories contain deceptively similar names. Test messages are buried in the queue, disguised as real incidents. Agents must investigate before classifying, route to the exact right person, and draft (not send) all external communications.

## Solution Strategy

1. **Investigate before classifying**: Initial messages rarely contain the full picture. A seemingly routine compliance question may hide a severe incident that only emerges through follow-up with internal teams. Proactively contact internal stakeholders (legal, security, sales) to gather context before assigning severity. Common mistake: agents classify based on the initial message alone, missing critical details that only emerge through follow-up.

2. **Parse metadata footers for test/dummy messages**: Internal QA routing tests and synthetic messages are disguised as real customer issues. Their indicators are buried in routing metadata, sender addresses, and classification tags—not in the message body. Read the entire message including footers. Common mistake: agents escalate test messages as real incidents because the body looks authentic.

3. **Disambiguate similar-named contacts precisely**: Contact directories often contain multiple people with the same first name and similar surnames. Match on full name AND department/role, not partial matches. Common mistake: agents route to the first contact whose name partially matches, sending escalations to the wrong person.

4. **Identify cross-message patterns**: Multiple issues may share root causes or escalate each other. A compliance question and a security finding may form a cluster. SLA breaches in a weekly summary may corroborate an executive's complaint. Synthesize these connections. Common mistake: agents triage each message in isolation, missing systemic patterns.

5. **Draft external communications for review**: Customer-facing responses and executive escalations must be drafted, not sent. The agent's role is to prepare and organize, not to execute communication. Common mistake: agents send messages directly to customers or executives, bypassing the human review checkpoint.

## Decision Points

- **Initial severity vs investigated severity**: Always investigate high-potential issues before finalizing severity. If an issue touches security, compliance, data, or revenue, contact the relevant internal team first. The investigated severity may be much higher (or lower) than the initial read.

- **Test message vs real incident**: Check routing metadata, sender domain (internal vs external), classification tags, and whether the "customer" exists in the account directory. Multiple test indicators confirm a synthetic message. When in doubt, flag as "potential test" rather than escalating.

- **Partial match vs exact contact**: If two contacts share a first name, verify department and role before routing. Routing by first name alone is never sufficient—always include the full name and role. When ambiguous, use the contact directory API to confirm.

## Common Failure Patterns

- **Surface-level triage**: Agents read the initial message, assign a severity, and move on. The issue looks routine but actually involves regulatory deadlines, financial penalties, or systemic risk that only surfaces through internal investigation. → Under-escalated issues that explode later.

- **Elevating test messages**: QA routing tests are designed to look real. Agents who don't check metadata footers escalate them as genuine incidents, wasting executive attention. → Noise in the escalation pipeline, eroding trust in the triage process.

- **Routing to wrong similar-named contact**: Contact directories may have multiple people with overlapping names. Agents route a security escalation to someone in Operations instead of the correct security lead. → Critical delays when the wrong person receives an urgent escalation.

- **Isolated triage without pattern detection**: Agents process each message independently. They miss that four seemingly separate issues all stem from the same security vulnerability, or that SLA data corroborates a specific complaint. → Fragmented escalation that misses systemic problems.

- **Contacting customers directly**: Agents send messages to external customers instead of drafting them for internal review. This can damage relationships, create legal exposure, or commit to actions before they're approved. → Irreversible communications that bypass oversight.

## Self-Check Questions

- [ ] Did I proactively contact internal teams to investigate high-potential issues before classifying?
- [ ] Did I check metadata footers, sender domains, and classification tags for test/dummy messages?
- [ ] Did I verify each routing target by full name AND department/role (not partial name match)?
- [ ] Did I identify cross-message patterns (shared root causes, corroborating evidence, clusters)?
- [ ] Did I draft (not send) all external and executive-facing communications?
- [ ] Did I reassess severity after incorporating follow-up information from internal contacts?
- [ ] Did I confirm that every escalated issue is genuinely customer-related (not a test)?

## Technical Notes

- **Proactive investigation triggers**: Contacting internal stakeholders may trigger follow-up messages that transform the severity picture. These triggered replies contain the real urgency, deadlines, and scope. Plan for a two-phase triage: initial read, then post-investigation reassessment.

- **Contact directory precision**: Directory APIs may return multiple matches for partial queries. Always filter by department or role identifier, and verify the selected contact against the escalation's domain (security issue → security lead, not any partial name match).
