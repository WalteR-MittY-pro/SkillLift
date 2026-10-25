---
name: content-based-document-sorting
description: Classify PDF, DOCX, and PPTX files by document content with one destination per original file.
---

# content-based-document-sorting

## Workflow
1. Inventory every original path and hash before moving files. Extract searchable text from PDF (pdftotext), DOCX (paragraphs and tables), and PPTX (slide text). Use OCR only when a document is image-only and a tool is available.
2. Classify by subject evidence in title, abstract, headings, and distinctive terminology. Avoid relying on filenames or single ambiguous keywords. Use a confidence queue for unclear cases and inspect their first pages or slides manually.
3. Stage a mapping of source path to one of the required destination directories. Check that every file appears exactly once and no destination collision would overwrite a different original. Resolve collisions without renaming only if the task permits; otherwise stop and report conflict.
4. Move files, then compare counts and hashes against the original inventory. Check that the source intake directory has no unclassified files and that contents are unchanged.

## Example
    expected = {path: sha256(path) for path in originals}
    assert len(assignments) == len(expected)

For a large corpus, use candidate scoring to prioritize review, but keep evidence snippets for ambiguous decisions.
