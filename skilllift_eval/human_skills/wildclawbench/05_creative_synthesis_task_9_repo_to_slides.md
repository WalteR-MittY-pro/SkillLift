---
name: 05-creative-synthesis-task-9-repo-to-slides
description: Use when creating project presentation decks from open-source repositories. Focuses on technical content distillation, fixed-page-count structuring, visual consistency, and slide-appropriate information density.
---

# Repository to Presentation Deck Generation

## Core Challenge

Converting a code repository into a fixed-page presentation requires distilling complex technical content into exactly the right number of slides—each carrying one key idea with visual support. The difficulty is managing information density: slides must be self-explanatory enough to stand alone, but sparse enough to work as presentation visuals. Too much text per slide produces readable-but-ugly decks; too little produces beautiful-but-empty decks. Page count is a hard constraint.

## Solution Strategy

1. **Structure before designing**: Plan the slide-by-slide narrative arc before creating any content. For a deck about a technical project: title slide, problem/motivation, key innovation, architecture/method, results/benchmarks, dataset/details, conclusion. Assign one topic per slide. Common mistake: agents start designing slides one by one without a global plan, resulting in unbalanced coverage.

2. **One key idea per slide**: Each slide should communicate a single message. If you need two ideas, use two slides. Slides are not paragraphs—they are visual anchors for spoken content. Common mistake: agents cram multiple topics onto one slide, creating dense text walls.

3. **Mine the repository for visual assets**: Architecture diagrams, result charts, dataset examples, and demo screenshots from the repo are worth more than any generated graphic. Extract real images from the README, docs, or paper. Common mistake: agents create text-only slides because extracting images takes extra effort.

4. **Maintain visual consistency across all slides**: Use a consistent color palette, font family, layout grid, and header style across every slide. The deck should look like one designed artifact, not a collection of unrelated pages. Common mistake: agents vary the design per slide, producing an incoherent deck.

5. **Verify exact page count after generation**: If the requirement is exactly N pages, the output must have exactly N pages—not N-1, not N+1. Check with a PDF parsing tool after creation. Rework if the count is wrong. Common mistake: agents generate content and assume the page count is correct without verification.

## Decision Points

- **Slide allocation**: Given a fixed page count, decide how many slides per topic. Title gets 1. Core technical content (method + results) should get 50-60% of slides. Background/motivation gets 1-2. Conclusion gets 1. Budget before designing.
- **Text vs visual ratio**: Aim for 30-40% text, 60-70% visual (images, diagrams, charts) per slide. If a slide is more than 50% text, it's a document page, not a slide.
- **Content depth**: Include enough technical specificity (model names, benchmark results, dataset names) to demonstrate genuine understanding. Generic descriptions ("achieves state-of-the-art results") are weaker than specific ones with concrete numbers.

## Common Failure Patterns

- **Wrong page count**: Agents produce 7 or 9 slides instead of exactly 8. This is usually a gating failure—the entire score collapses. → Always verify and adjust page count.

- **Text-dense slides**: Agents paste paragraph-style content onto slides, making them look like document pages. Presentation slides use keywords, short phrases, and visuals. → Low visual quality, unprofessional appearance.

- **No real images**: Agents create text-only slides or use generic shapes/colors instead of extracting diagrams and charts from the repository. → Slides feel hollow and don't leverage the project's actual visual assets.

- **Inconsistent design**: Each slide has different fonts, colors, or layout structure, making the deck look assembled from unrelated pieces. → Lack of professional polish.

- **Shallow content**: Agents write generic descriptions ("this is a powerful segmentation model") without specific technical details from the repo. The deck doesn't demonstrate deep understanding of the project. → Low content coverage scores.

## Self-Check Questions

- [ ] Did I plan the slide-by-slide narrative arc before designing?
- [ ] Does each slide carry ONE key idea (not multiple)?
- [ ] Did I extract real images/diagrams from the repository?
- [ ] Is the visual design consistent across ALL slides (colors, fonts, layout)?
- [ ] Did I verify the EXACT page count with a PDF parsing tool?
- [ ] Is text density appropriate for slides (short phrases, not paragraphs)?
- [ ] Are technical details specific (actual numbers, model names, benchmark scores)?

## Technical Notes

- **Page count verification**: After generating the PDF, use a PDF library to check `len(document)` matches the required count exactly. If off by one, restructure content to add or remove a slide.
- **HTML-to-PDF pipeline**: When generating slides as HTML then converting to PDF, control page breaks explicitly with CSS `page-break-after: always` to ensure one slide per page. Implicit pagination can produce unexpected page counts.
