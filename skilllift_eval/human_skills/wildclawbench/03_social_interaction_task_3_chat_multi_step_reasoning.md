---
name: 03-social-interaction-task-3-chat-multi-step-reasoning
description: Use when synthesizing a feasibility assessment from multi-stakeholder chat history with shifting requirements. Focuses on tracing requirement evolution, distinguishing hard blockers from negotiable constraints, and flagging governance risks.
---

# Multi-Stakeholder Feasibility Synthesis

## Core Challenge

Business decisions require synthesizing inputs from multiple stakeholders who disagree, override each other, and change requirements mid-stream. Each message adds, modifies, or contradicts previous information. The agent must reconstruct the full timeline, distinguish truly infeasible items from merely difficult ones, and identify governance/compliance landmines that technical stakeholders may not flag—all without taking any outgoing actions.

## Solution Strategy

1. **Build a requirement timeline**: Reconstruct how each requirement evolved chronologically. Note who said what, when, and whether it was later overridden. This timeline IS the source of truth—not any single message. Common mistake: agents treat the latest message as complete context without tracing how requirements mutated.

2. **Separate hard blockers from soft constraints**: A hard blocker makes the current proposal literally impossible (incompatible technology, missing approvals, regulatory requirements). A soft constraint makes it harder or more expensive but is negotiable. Categorize each issue accordingly. Common mistake: agents lump all problems together, failing to distinguish "we can't do this" from "this will cost more."

3. **Identify governance and compliance gaps**: Technical stakeholders focus on technical feasibility. But discount approvals, SLA commitments, legal review windows, and data protection requirements carry their own veto power. Cross-reference every commitment against compliance constraints. Common mistake: agents only report what engineers and salespeople said, missing that legal/finance policies block the deal independently.

4. **Flag unverified intelligence explicitly**: Competitive tips, rumors, and secondhand information may be valuable but must be labeled as unverified. Mixing rumor with fact in a business assessment destroys credibility. Common mistake: agents present all information at the same confidence level.

5. **Synthesize a phased recommendation**: Don't just list problems. Group them into "what's impossible now," "what needs modification," and "what's a risk to monitor." Provide a phased action plan with clear ownership and escalation paths. Common mistake: agents produce a laundry list of issues without synthesis or recommendations.

## Decision Points

- **Hard blocker vs phased approach**: If a requirement violates a physical/technical impossibility (incompatible systems, missing infrastructure), it's a hard blocker—recommend alternative. If it's achievable with more time/money, recommend deferring to a later phase.

- **Executive override vs compliance policy**: When an executive demands action that violates policy (e.g., verbal approval where written is required), flag the gap explicitly. Do not assume the override resolves the compliance requirement—state both the executive intent and the policy constraint.

- **Confident assertion vs unverified claim**: If the source has direct knowledge (the VP of Engineering about uptime), treat as credible. If the source is passing along secondhand information, label as unverified. When in doubt, mark confidence level explicitly.

## Common Failure Patterns

- **Flat reading without timeline**: Agents read all messages and summarize each independently, missing that requirement X was introduced on Day 1, modified on Day 2, and overridden on Day 3. → Stale requirements reported as current, or contradictory requirements reported side-by-side.

- **Technical-only lens**: Agents focus on what engineers and product managers said, ignoring that finance, legal, and compliance constraints independently block the deal. → Assessments that look complete but miss the actual showstoppers.

- **Accepting executive override at face value**: When a CEO says "I authorize this, that's sufficient," agents report it as resolved. But if policy requires written CFO approval, the verbal override doesn't satisfy compliance. → False green lights on non-compliant proposals.

- **Treating all constraints equally**: Agents list every issue at the same severity, producing a noisy report where true blockers are buried beside minor concerns. → Decision-makers can't identify what actually prevents execution.

- **Analysis paralysis into action**: Agents start sending messages to stakeholders instead of producing an assessment. This violates the analysis-only mandate. → Premature commitments, loss of neutrality.

## Self-Check Questions

- [ ] Did I trace how each major requirement evolved across the full message timeline?
- [ ] Did I distinguish truly infeasible items (hard blockers) from modifiable/negotiable ones?
- [ ] Did I check whether any executive commitments violate governance or compliance policies?
- [ ] Did I label rumors, competitive intel, and secondhand information as unverified?
- [ ] Did I synthesize a phased recommendation rather than just listing problems?
- [ ] Did I read all available messages rather than stopping at a partial subset?
- [ ] Did I avoid sending any messages or taking outgoing actions?

## Technical Notes

- **Message count matters**: Partial reads produce partial assessments. The number of messages retrieved is itself a quality signal—exhaustive reading is expected, not optional.

- **No-side-effects constraint**: Feasibility analysis tasks require read-only operation. Any message-sending call is a safety violation, regardless of content or intent.
