---
name: pdf-court-form-fields
description: Map case facts into fillable court PDF fields while preserving unprovided and court-only fields.
---

# pdf-court-form-fields

## Workflow
1. Inspect the actual PDF page count, AcroForm field names, widget locations, and instructions. A form revision may change field names or required checkboxes.
2. Build a fact table from the case narrative: parties, contact details, claim amount and basis, event dates, venue reason, demand attempts, and filing date. Leave facts absent from the narrative blank. Do not infer legal conclusions or complete court-use fields.
3. Fill using pypdf or fillpdf while preserving original pages. Example: writer.update_page_form_field_values(page, {field_name: value}); set appearance generation as needed. For non-AcroForm PDFs, overlay text at verified coordinates rather than guessing positions.
4. Render every completed page and inspect alignment, clipping, checkbox state, and date format. Reopen the saved PDF and extract field values to compare against the fact table.

## Example and boundary
Use the supplied form and case facts as the source of truth. Form completion does not imply filing or legal advice.
