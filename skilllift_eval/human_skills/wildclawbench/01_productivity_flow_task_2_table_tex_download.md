---
name: 01-productivity-flow-task-2-table-tex-download
description: Use when extracting structured elements from downloadable source archives with fidelity preservation. Focuses on complete enumeration, order preservation, and verbatim content extraction.
---

# Structured Content Extraction from Source Archives

## Core Challenge

Downloading a source archive and extracting every instance of a specific structural element (e.g., LaTeX table environments) in the correct order, verbatim and without modification. The difficulty lies in completeness—finding ALL instances across multiple files—and fidelity—preserving exact formatting without adding wrappers, commentary, or normalization.

## Solution Strategy

1. **Enumerate before extracting**: Before writing any output, scan every `.tex` file in the archive and record the location of every target element. Build a complete inventory first. → Common mistake: agents extract tables as they find them, missing tables in secondary files or files included via `\input{}`.

2. **Follow include chains**: LaTeX papers split content across multiple files using `\input{}` or `\include{}`. The main `.tex` file is not the whole paper—trace all includes to find every table environment. → Common mistake: agents only scan the main `.tex` file and miss tables in chapter or appendix files.

3. **Match environments by delimiters, not keywords**: Extract `\begin{table}...\end{table}` blocks precisely. Don't search for the word "table" or guess boundaries from context. Use the environment delimiters as your scanner. → Common mistake: agents use fuzzy matching that captures partial environments or misses nested ones.

4. **Preserve verbatim content**: Copy the exact source text including comments, whitespace, and formatting. Do not reformat, normalize, or "clean up" the LaTeX. The extracted block must be byte-faithful to the original. → Common mistake: agents tidy the LaTeX (removing comments, aligning columns), causing mismatches.

5. **Order by document appearance, not file order**: Tables are numbered by their appearance order in the compiled paper, which may differ from file order if files are `\input`'d in a non-alphabetical sequence. Resolve the include order first. → Common mistake: agents sort files alphabetically and number tables by file, producing wrong ordering.

## Decision Points

- **Which file is the "main" `.tex` file**: If the archive has multiple root files, identify the one that `\input`s others (usually has `\documentclass`). If unclear, compile order is typically specified in a Makefile or submission script.

- **How to handle `table*` vs `table` environments**: Both are table environments. Unless the task specifies otherwise, treat all variants (`table`, `table*`) as tables. Check the task wording carefully.

## Common Failure Patterns

- **Missing tables in included files**: Agents scan only the top-level `.tex` file, missing tables in files pulled via `\input{}`. → Incomplete extraction, wrong numbering for later tables.

- **Modifying extracted content**: Agents "helpfully" reformat the LaTeX—removing comments, aligning `&` separators, or trimming whitespace. → Content no longer matches ground truth after normalization.

- **Wrapping output with commentary**: Agents add markdown code fences, explanatory text, or section headers around the extracted environment. → Extra content in output files causes match failures.

- **Skipping gaps in numbering**: When a table environment is malformed or ambiguous, agents skip it rather than investigating, creating gaps in the sequence. → Numbered files don't align with ground truth positions.

- **Wrong table environment type**: Agents capture only `\begin{table}` but miss `\begin{table*}` (two-column spanning tables) or vice versa. Both are table environments and must be extracted. → Missing tables that use the starred variant.

- **Ignoring float placement options**: Tables with `[t]`, `[h]`, `[b]` placement specifiers are still tables. Agents sometimes skip tables with unfamiliar options. → Missing tables with non-default float options.

## Self-Check Questions

- [ ] Did I scan ALL `.tex` files in the archive, including those loaded via `\input{}`?
- [ ] Did I resolve the include order to determine document appearance order?
- [ ] Did I extract each environment using `\begin{...}...\end{...}` delimiters?
- [ ] Did I preserve the exact source text without reformatting or removing comments?
- [ ] Did I output ONLY the table environment with no extra wrappers or commentary?
- [ ] Did I number files sequentially without gaps?
- [ ] Did I clean up downloaded archives and intermediate files after extraction?
- [ ] Did I handle both `table` and `table*` environments?
- [ ] Did I verify the number of extracted tables matches the expected count?

## Technical Notes

- **LaTeX `\input` resolution**: `\input{foo}` loads `foo.tex` (extension is optional). `\include{foo}` is similar but has restrictions on where it can appear. Trace these recursively to find all source files.
- **Comment stripping in normalization**: If grading normalizes by removing `%` comments, your verbatim extraction will still match because normalization applies to both sides. Don't pre-strip comments yourself.
- **Archive format**: arXiv source packages are typically tar.gz. Download via the `/eprint/` endpoint, not the `/pdf/` endpoint. The PDF is rendered output; the source archive contains the original `.tex` files.
