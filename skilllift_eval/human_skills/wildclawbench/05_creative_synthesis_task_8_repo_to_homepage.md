---
name: 05-creative-synthesis-task-8-repo-to-homepage
description: Use when creating project landing pages from open-source repositories. Focuses on source distillation, self-contained web design, responsive implementation, and professional visual polish.
---

# Open-Source Repository to Project Homepage

## Core Challenge

Building a compelling project homepage from a code repository requires understanding the project's purpose and value proposition from its README, docs, and code, then translating that into a professional single-page website. The difficulty is moving from a text-heavy README to a visual medium: the homepage needs real imagery, clear value propositions, and polished design—not just "the README rendered as HTML." All assets must be self-contained in one file.

## Solution Strategy

1. **Mine the repository for real content**: Read the README thoroughly, check docs/ directories, look at the project's existing logo, badges, and screenshots. Extract the project's actual value proposition, supported features, and quick-start instructions. Common mistake: agents write generic descriptions instead of using the project's own framing.

2. **Gather real images, not placeholders**: The homepage needs 5+ images. Sources: project logo from the repo, architecture diagrams from docs, screenshots from README, badges/shields, or generated visual elements. Placeholder images signal an unfinished page. Common mistake: agents use CSS-only decorations (colored divs, gradient backgrounds) and count them as "images."

3. **Design as a product page, not a documentation page**: Modern project homepages have: a hero section with project name and tagline, a features grid, a "how it works" section, quick-start code snippets, and community/citation sections. Use visual hierarchy, not flat text. Common mistake: agents create a single-column page that reads like the README.

4. **Ensure true self-containment**: All CSS must be inline or in `<style>` tags. All images must be embedded as data URIs, local files, or external CDN URLs. JavaScript (if any) must be inline. The single HTML file must render completely offline (assuming internet for CDN resources). Common mistake: agents reference external CSS/JS files that won't be available.

5. **Implement responsive design properly**: Include the viewport meta tag and at least one `@media` query that adapts layout for mobile widths. This is not optional—modern pages must work on phones. Common mistake: agents add the viewport tag but no actual responsive CSS rules.

6. **Screenshot with headless browser**: Use Playwright or similar to produce a full-page screenshot at the specified width. The screenshot IS part of the deliverable—it verifies the page renders correctly. Common mistake: agents forget the screenshot or produce a viewport-only capture instead of full-page.

## Decision Points

- **Content sections**: Always include: project introduction, key features, supported models/benchmarks/tools (depending on project type), quick start. Optionally: architecture overview, citation, community links. Choose based on what the repository emphasizes.
- **Design style**: Match the project's tone. Research projects → clean, academic, restrained. Developer tools → modern, dark mode option, code-forward. Match the repo's existing visual identity if one exists.
- **Image strategy**: Prefer real screenshots and diagrams from the repo. If none exist, generate simple architectural illustrations using inline SVG. Avoid stock photos—they feel inauthentic for tech projects.

## Common Failure Patterns

- **README-as-HTML**: Agents wrap the README content in basic HTML with minimal styling. The page reads as documentation, not a product showcase. → Low design quality, no visual appeal.

- **Insufficient real images**: Agents include the logo and maybe one screenshot, then fill with CSS decorations. A page with fewer than 5 actual images feels empty and unfinished. → Gating failures on image count.

- **Missing responsive design**: Agents add the viewport meta tag but no `@media` queries, or vice versa. Both are needed for genuine mobile support. → Responsive design failures.

- **External file dependencies**: Agents link to separate CSS or JS files that aren't included. When opened as a single file, the page is broken. → Page doesn't render correctly.

- **No screenshot**: Agents build the page but forget to capture the screenshot, or capture at wrong width, or capture only the visible viewport instead of full page. → Missing or incorrect verification artifact.

## Self-Check Questions

- [ ] Did I read the full README and extract the project's actual value proposition?
- [ ] Are there 5+ real images (logo, screenshots, diagrams—not CSS decorations)?
- [ ] Is the page designed as a product landing page (not rendered documentation)?
- [ ] Is all CSS inline or in `<style>` tags (no external file dependencies)?
- [ ] Is there a viewport meta tag AND at least one `@media` query?
- [ ] Does the page include a navigation bar and project GitHub link?
- [ ] Did I capture a full-page screenshot at the specified width?
- [ ] Does the page render correctly when opened as a standalone file?

## Technical Notes

- **Self-contained images**: Use data URIs for small images (logos, icons), external CDN URLs for libraries/badges, and inline SVG for generated graphics. Avoid referencing local image files that won't be bundled.
- **Full-page screenshot**: Use Playwright with `full_page: true` option. Set viewport width to the specified size. The screenshot should capture the entire scrollable content, not just the initial viewport.
