---
name: evidence-linked-json-retrieval
description: Answer structured enterprise questions by tracing claims across product records and identity metadata.
---

# evidence-linked-json-retrieval

## Workflow
1. Parse each question into entity, relation, requested output type, and qualifiers such as author, reviewer, competitor, or URL. Keep each question's evidence separately.
2. Inspect JSON structure before bulk searching. Search likely product files for exact phrases and semantic variants; capture the surrounding record, source path, and field. Follow person-name links into employee metadata for IDs, and keep product/demo URLs as strings from the source.
3. Resolve ambiguity using role and document context rather than name-only matches. De-duplicate answers without discarding distinct people or URLs; preserve stable source order when no ordering is specified.
4. Serialize every answer as a list, including singletons. If a task asks for token consumption, use actual tokenizer or API usage data; a positive invented count is not a measurement. State the limitation when only an estimate is possible.

## Example pattern
Build an internal table of (question, answer, source_path, source_excerpt, relation). Validate each answer against its evidence before writing the requested JSON shape.
