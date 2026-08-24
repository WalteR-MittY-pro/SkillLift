---
name: 01-productivity-flow-task-5-wikipedia-biography
description: Use when extracting entities from a source document and recursively retrieving linked content from each entity. Focuses on complete entity identification, conditional filtering, and faithful content extraction.
---

# Entity Extraction and Recursive Content Retrieval

## Core Challenge

Reading a source document, identifying every person mentioned, then for each person checking whether they meet a secondary condition (having a specific section) and extracting that section's full content. The difficulty is threefold: complete entity identification (no missed persons), accurate conditional filtering (only those with the target section), and faithful content extraction (exact text without additions or omissions).

## Solution Strategy

1. **Extract entities exhaustively**: Read the entire source section carefully and list every named person. Don't stop at the first few—scan every paragraph, every sentence. Historical texts are dense with references. → Common mistake: agents identify prominent figures and miss minor mentions in later paragraphs.

2. **Distinguish names from titles**: Historical figures are often referenced by title (emperor name, posthumous name, era name) rather than personal name. Map titles to actual personal names consistently. → Common mistake: agents use the title as the filename instead of the actual name.

3. **Check the filtering condition for each entity**: After identifying all persons, verify for each one whether their Wikipedia page has the target section. This is a per-entity lookup, not a batch assumption. → Common mistake: agents assume all mentioned persons have biography sections and extract whatever content exists.

4. **Extract section content faithfully**: Copy the section content exactly as it appears—including paragraphs, subsections, and formatting. Remove reference markers if needed, but don't summarize, paraphrase, or omit content. → Common mistake: agents produce a summary or extract only the first paragraph of the section.

5. **Normalize consistently**: Use the same character set (simplified vs traditional) and quotation mark style throughout. Mixed character sets or quote styles cause mismatches. → Common mistake: agents copy content that mixes simplified and traditional characters, or uses corner brackets instead of curved quotes.

## Decision Points

- **When a person is mentioned but has no Wikipedia page**: Exclude them from output entirely. The task requires both being mentioned AND having the target section. No page = no output file.

- **How to handle variant names**: Use the standard historical name as it appears in authoritative sources. If a person is known by multiple names, use the most common personal name (not the reign title).

- **Section variants**: Biography sections may be titled "Biography", "生平", "传记", or similar. Check for all reasonable variants before concluding a page lacks the section.

## Common Failure Patterns

- **Incomplete entity extraction**: Agents identify the first few prominent figures and stop, missing persons mentioned in later paragraphs or in passing. → Missing output files for less-prominent figures.

- **Title-as-name confusion**: Agents name files by imperial titles or posthumous names rather than personal names. → Wrong filenames that don't match expected names.

- **Over-extraction**: Agents include persons who lack the target section, or extract content beyond the section boundaries. → Extra files or bloated content that fails comparison.

- **Content paraphrasing**: Agents summarize the section instead of copying it verbatim. → Content mismatch after normalization.

- **Character set inconsistency**: Agents mix simplified and traditional Chinese characters, or fail to normalize quotation marks. → Character-level mismatches in comparison.

- **Including the source subject**: Agents forget to exclude the main article's subject from the entity list. The task explicitly requires excluding the source article's own subject. → Extra file that shouldn't exist.

## Self-Check Questions

- [ ] Did I read the ENTIRE source section and identify every person mentioned?
- [ ] Did I map all titles and aliases to actual personal names?
- [ ] Did I verify each identified person has the target section on their Wikipedia page?
- [ ] Did I extract the full section content without summarizing or paraphrasing?
- [ ] Did I use consistent character formatting (simplified/traditional) throughout?
- [ ] Did I exclude the source article's own subject from the output?
- [ ] Did I avoid including URLs or hyperlinks in the extracted text?
- [ ] Did I verify no extra files were created beyond the expected set?
- [ ] Did I check for all reasonable section title variants (Biography, 生平, 传记)?
- [ ] Did I use the person's actual name (not title or alias) as the filename?

## Technical Notes

- **Wikipedia section extraction**: The MediaWiki API provides section-level content via `action=parse&page={title}&section={index}`. This is more reliable than scraping HTML, which may include navigation elements or infoboxes.
- **Reference markers**: Wikipedia content includes `[1]`, `[2]` style citation markers. These are typically ignored during grading normalization, but their presence doesn't cause failures. Focus on content fidelity.
