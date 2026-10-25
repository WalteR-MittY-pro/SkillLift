---
name: jackson-polymorphic-security
description: Close JSON deserialization bypasses in polymorphic security controls while retaining valid requests.
---

# jackson-polymorphic-security

## Workflow
1. Reproduce the request path in the target version: HTTP body to Jackson subtype selection, filter construction, JavaScript enablement, and execution. Trace how unknown or empty property names are handled by the affected deserializer.
2. Specify the invariant at the trusted boundary: a dangerous script-bearing subtype must never activate when scripting is disabled, regardless of duplicate, empty, case-varied, or nested JSON keys. Enforce it in one authoritative parsing/validation layer, not only at a UI or endpoint surface.
3. Add focused tests that prove malicious variants are rejected and ordinary non-script filters, permitted scripts where configured, and unrelated sampler requests still work. Include malformed JSON and nested subtype cases.
4. Patch only the necessary source, save a reproducible patch, compile the affected Maven module and dependencies, and inspect the resulting JAR and tests. Do not weaken security settings to accommodate the payload.

## Example test matrix
Inputs: ordinary selector filter; legitimate JavaScript filter with authorized setting; disabled JavaScript; empty-key override; nested filter override. Assert rejection mode and no execution side effect, not just an HTTP error code.
