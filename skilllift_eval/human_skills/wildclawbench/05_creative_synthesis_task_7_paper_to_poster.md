---
name: 05-creative-synthesis-task-7-paper-to-poster
description: Use when converting academic papers into visual conference posters. Focuses on content distillation, figure recreation, information density management, and scholarly design aesthetics.
---

# Academic Paper to Conference Poster Design

## Core Challenge

Transforming a research paper into a single-page conference poster requires distilling dense academic content into a spatially efficient visual format. The difficulty is threefold: extracting the paper's core contributions and translating them into poster-appropriate chunks, acquiring or recreating the paper's figures, and designing a layout that is simultaneously information-rich and readable from a distance. Posters that cram text at tiny sizes fail readability; posters that are too sparse fail content coverage.

## Solution Strategy

1. **Read the paper deeply before designing**: Understand the motivation, method, key results, and conclusion as distinct content blocks. Identify which figures and tables carry the most information. A poster is not a compressed paper—it's a curated highlight that communicates the work's essence. Common mistake: agents skim the abstract and introduction, missing the technical depth needed for a credible poster.

2. **Acquire real figures, don't fake them**: The poster must contain actual charts, architecture diagrams, and result visualizations from the paper. Extract them from the PDF directly, or find them on the project page/repo. Recreating a figure from scratch rarely matches the original's quality. Common mistake: agents draw generic placeholder diagrams because extracting real figures takes more effort.

3. **Design for the reading distance**: Conference posters are viewed from 1-2 meters away. Title and section headings must be large (readable from 2m). Body text must be at least 24pt equivalent. Information density should leave breathing room, not create a wall of text. Common mistake: agents pack all paper content into small fonts, making the poster unreadable at conference scale.

4. **Use academic poster conventions**: Multi-column layouts (2-3 columns), clear section headers (Introduction, Method, Results, Conclusion), consistent color scheme tied to the paper's figures, and a prominent title with author information. These conventions help viewers navigate quickly. Common mistake: agents design a web-page-style layout (single column, scrolling sections) instead of a spatial poster layout.

5. **Balance content coverage with visual breathing room**: Include motivation, method overview, key quantitative results, qualitative results/visualizations, and conclusion. But don't cram all five at equal density—allocate space by importance. Method and results deserve the most space. Common mistake: agents give equal space to all sections, producing a monotonous, text-heavy poster.

## Decision Points

- **Figure selection**: Choose 3-5 figures that best communicate the work: architecture diagram (method), quantitative comparison table or chart (results), qualitative visual examples (results). Skip figures that are minor or redundant.
- **Text density per section**: Introduction/Motivation: concise paragraph. Method: medium density with architecture figure as anchor. Results: figure-heavy with brief captions. Conclusion: 2-3 bullet points.
- **Color scheme**: Derive from the paper's figures. If the paper uses a blue/white palette, mirror it. Consistency between figures and poster background creates visual unity.

## Common Failure Patterns

- **Text-dump posters**: Agents paste large blocks of paper text at small font sizes to "fit everything." The poster becomes unreadable and fails its purpose as a visual communication tool. → Low readability scores, unprofessional appearance.

- **Placeholder figures**: Agents include generic diagrams (boxes and arrows) instead of extracting real figures from the paper. Reviewers immediately notice fake figures. → Loss of credibility, low content coverage.

- **Missing core sections**: Agents omit key sections (e.g., results or conclusion) due to space constraints, or because they didn't read those parts of the paper carefully. → Incomplete poster that doesn't represent the full work.

- **Web-page layout instead of poster layout**: Agents create a single-column flowing layout that looks like a website, not a conference poster. Posters are spatial—viewers scan, not scroll. → Layout that fails poster conventions.

- **Insufficient resolution**: Agents generate the poster at low resolution. Conference posters need high pixel counts on the longest edge to remain sharp when printed. → Blurry, low-quality output.

## Self-Check Questions

- [ ] Did I read the full paper (not just abstract) to understand all key contributions?
- [ ] Did I extract real figures from the paper PDF or project page (not placeholders)?
- [ ] Is the title prominently displayed with author information?
- [ ] Are section headings large enough to read from 2 meters away?
- [ ] Is body text large enough to read from 1 meter away (not tiny font)?
- [ ] Does the layout follow academic poster conventions (multi-column, section headers)?
- [ ] Are all core sections present (motivation, method, results, conclusion)?
- [ ] Is the resolution sufficient (longest edge ≥ 3000 pixels)?

## Technical Notes

- **Figure extraction from PDF**: Use a PDF library to extract embedded images directly. If images are vector graphics, render the relevant PDF page at high DPI and crop. Screenshots at low resolution produce blurry figures.
- **Resolution vs file size**: High-resolution posters (3000+ px) produce large PNG files. This is expected—do not compress to reduce file size at the expense of readability.
