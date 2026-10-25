---
name: docx-template-replacement
description: Fill Word placeholders and conditional sections while preserving template layout and styles.
---

# docx-template-replacement

## Workflow
1. Inspect the JSON keys and all placeholder tokens, including headers, footers, tables, and the document body. Identify conditional start/end markers and validate that each block is paired.
2. Decide each block from its control field, using an explicit value policy such as case-insensitive Yes/No. If retained, remove markers and replace enclosed placeholders; if omitted, remove the entire block, including extra blank paragraphs where appropriate.
3. Word often splits a placeholder across runs. A plain paragraph.text replacement can destroy formatting. Prefer a run-aware replacement that maps character spans back to runs, or edit the underlying XML text nodes while preserving run properties.
4. Reopen the saved document, search every part for unresolved double-brace tokens and condition markers, and render to PDF or images to inspect pagination, alignment, and emphasis.

## Example
    data = json.load(open(data_path))
    keep = str(data.get('RELOCATION_PACKAGE', '')).casefold() == 'yes'

Never substitute values absent from the source; report unresolved required fields.
