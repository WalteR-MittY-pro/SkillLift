---
name: 02-code-intelligence-task-10-acad-homepage-zh
description: Use when generating web pages that replicate a visual template style from screenshots. Focuses on visual style fidelity, content filtering from reference images, and resource integrity in static page generation.
---

# Visual Style Transfer for Web Page Generation

## Core Challenge

Replicating a web template's visual style from a screenshot requires reverse-engineering CSS layout, typography, component structure, and design conventions that are implicit in the image. Beyond style, content must be filtered from reference images (screenshots of existing pages) with precise rules — selecting only specific publications, only certain date ranges. The result must render correctly with all visual assets (images, icons, thumbnails) actually loading, not placeholders.

## Solution Strategy

1. **Analyze the template screenshot systematically**: Before writing any code, catalog every visual element: layout structure (columns, sections, ordering), typography (fonts, weights, sizes), component patterns (cards, lists, sidebars), color palette, spacing rhythm, and specific stylistic markers (emojis in headers, date formats, tag styles). Refer to this catalog throughout implementation. Common mistake: agents glance at the template and reproduce a rough approximation, missing specific stylistic details.

2. **Use the actual template repository when available**: If the template is based on a known open-source project, clone and modify it rather than building from scratch. This preserves the exact CSS, JavaScript, and component structure. Replace content while keeping the design system intact. Common mistake: agents build a new page from scratch, inevitably diverging from the template's specific style choices.

3. **Extract content from reference images with strict filtering**: When populating content from screenshots of an existing page, apply filtering rules precisely: specific publication venues only, specific date ranges only, specific author roles only. Each filter is a hard constraint, not a preference. Common mistake: agents include all visible content from the reference without applying filters, or apply them loosely.

4. **Ensure all visual assets are real, not placeholders**: Profile photos, paper thumbnails, social media icons, and any other images must be actual images that render correctly. Placeholder text, gray boxes, broken image links, or default avatars all fail. Download or generate real assets. Common mistake: agents use placeholder images or leave broken links, assuming they're "good enough."

5. **Screenshot with full-page capture**: Use headless browser automation to capture the ENTIRE page, not just the viewport. Configure the screenshot to include all content below the fold. Verify the screenshot is non-trivial in file size (indicating actual rendered content). Common mistake: agents capture only the visible viewport, missing most of the page content.

6. **Bold the target author's name in publication lists**: Academic homepage conventions require the page owner's name to be bolded in author lists. This is a specific visual marker that's easy to miss but consistently checked. Apply bold formatting to every instance of the target author's name in every publication entry. Common mistake: agents leave author lists in plain text, missing this standard convention.

## Decision Points

- **Clone template vs build from scratch**: Always prefer cloning if the template source is identifiable. Build from scratch only if no source exists. The template's CSS embodies hundreds of style decisions that are hard to reverse-engineer from a screenshot alone.

- **Content extraction from screenshots vs online sources**: The screenshot is the authoritative source. Online references may have changed. If the screenshot is unclear about a detail, note the ambiguity and make a best-effort extraction — don't substitute from online sources unless they provide assets (images, etc.) not available in the screenshots.

## Common Failure Patterns

- **Approximate instead of faithful style replication**: Agents create a generic "academic homepage" instead of replicating the specific template's style markers (particular emojis, date format, card layout, tag style). → Style scores low because specific visual details are missing.

- **Placeholder assets**: Agents leave gray boxes, default silhouettes, or broken image links where photos and thumbnails should be. → Visual resource scores zero for every placeholder.

- **Incomplete content filtering**: Agents include papers from wrong venues, news entries from wrong date ranges, or publications where the target author isn't first/co-first author. → Content accuracy scores low due to included items that should have been filtered.

- **Viewport-only screenshot**: Agents capture only the visible portion of the page, missing sections below the fold. → The screenshot doesn't show most of the page, and many rubric items can't be evaluated.

- **Missing author bolding convention**: Agents render publication author lists in uniform styling without bolding the target author's name. This standard academic homepage convention is easy to overlook. → Loss of specific style fidelity points across all publication entries.

## Self-Check Questions

- [ ] Did I systematically catalog every visual element from the template screenshot before coding?
- [ ] Did I use the actual template source code rather than building from scratch?
- [ ] Did I apply ALL content filtering rules strictly (venue, date range, author role)?
- [ ] Are ALL visual assets real images that actually render (no placeholders, no broken links)?
- [ ] Did I capture a full-page screenshot showing all content sections?
- [ ] Does the rendered page match the template's specific stylistic markers (emojis, formats, layout)?
- [ ] Did I verify the screenshot file is large enough to contain real rendered content?
- [ ] Is the target author's name bolded in every publication entry?
- [ ] Did I verify that section emojis, date formats, and tag styles match the template exactly?
- [ ] Did I use the reference screenshot as the authoritative source for content filtering?

## Technical Notes

- **Full-page screenshot**: Playwright's `page.screenshot(full_page=True)` captures the entire scrollable page. Ensure the page has fully loaded (wait for `networkidle` or a short delay) before capturing.
- **Template-specific style markers**: Academic homepage templates often use specific conventions: emoji-prefixed section headers (not generic icons), italicized YYYY.MM date format (not bracketed month abbreviations), plain-text venue tags (not colored badges), card-style paper layouts with left thumbnails. These details are intentional and must be preserved exactly.
