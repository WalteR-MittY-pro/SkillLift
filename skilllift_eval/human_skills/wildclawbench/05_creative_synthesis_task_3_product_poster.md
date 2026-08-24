---
name: 05-creative-synthesis-task-3-product-poster
description: Use when creating visual marketing materials from product images. Focuses on feature extraction, design hierarchy, and iterative quality refinement.
---

# Multimodal Product Marketing Design

## Core Challenge

Creating compelling marketing visuals requires extracting specific, persuasive details from product images and translating them into design elements with strong hierarchy. Generic descriptions produce generic designs. Agents must analyze visual features deeply, prioritize information hierarchically, and iterate on composition quality.

## Solution Strategy

1. **Analyze deeply before generating**: Use vision models to extract 5+ specific, concrete features from the product image. Don't stop at category labels ("leather", "metal hardware"). Dig deeper: what kind of leather texture? What hardware finish? What stitching pattern? Common mistake: agents generate immediately from high-level descriptions, producing generic outputs.

2. **Specific beats generic**: "Full-grain calfskin with visible pebble texture" is persuasive. "Premium leather" is not. "Antique brass buckles with hand-buffed patina" is persuasive. "Quality hardware" is not. Specificity signals craftsmanship. Common mistake: agents use marketing-speak instead of concrete observations.

3. **Hierarchy drives persuasion**: Product features are the hero. Brand, price, and CTA are supporting elements. Allocate visual weight accordingly—features should dominate the composition, not the price tag. Common mistake: agents treat all elements as equal, creating flat, unpersuasive layouts.

4. **Design is not layout**: Arranging elements on a canvas is layout. Design is hierarchy, typography, color relationships, whitespace, and visual rhythm. Code-generated posters often look like HTML templates (stacked boxes, thin borders, default fonts). Iterate specifically on typographic contrast, compositional balance, and visual polish. Common mistake: agents stop after first generation without critiquing design quality.

5. **Iterate on weak dimensions**: Generate once, critique against the strategy above, identify the weakest dimension (feature specificity? hierarchy? visual polish?), then regenerate with explicit instructions targeting that dimension. Common mistake: agents declare "done" after first output without self-assessment.

## Decision Points

- **When to iterate vs accept**: If your output has generic feature labels, flat hierarchy, or template aesthetics, iterate. If features are specific, hierarchy is clear, and design is polished, accept.

- **How much product analysis**: Minimum 5 specific features (texture, hardware, stitching, structure, finish). More is better. Stop when you're listing details invisible in the photo.

- **Design style selection**: Match product positioning. Luxury goods need whitespace, elegant typography, and restrained color. Mass-market goods can use bolder, denser layouts. Infer from product category and price point.

## Common Failure Patterns

- **Surface-level analysis**: Agents identify the product category ("briefcase") and stop. Deep feature extraction is seen as optional. → Generic outputs that could apply to any product in the category.

- **HTML template aesthetics**: Agents use code to generate images, producing compositions that look like rendered HTML: stacked vertical boxes, thin borders, default fonts, no typographic hierarchy. → Low design-impact scores, unprofessional appearance.

- **Price-dominant hierarchy**: Agents make the price the largest element because it's a "key detail". In premium products, price is confidence—it should be visible but not dominant. Features sell, price confirms. → Discount-bin aesthetic on luxury goods.

- **One-shot generation**: Agents generate once and submit. Multimodal generation requires iteration—first draft establishes structure, second refines weak dimensions. → Mediocre outputs that could be excellent with one revision.

- **Ignoring whitespace**: Agents fill every pixel with content. Premium design uses whitespace for focus and sophistication. Dense layouts signal clutter, not value. → Unprofessional, crowded compositions.

## Self-Check Questions

- [ ] Did I extract at least 5 specific, concrete features from the product image?
- [ ] Are my feature descriptions specific (not generic marketing terms)?
- [ ] Is the visual hierarchy clear (features dominant, not price)?
- [ ] Does the design have typographic contrast (varied weights and sizes)?
- [ ] Does the composition use whitespace purposefully (not just "fill all space")?
- [ ] Does it look like professional graphic design (not an HTML template)?
- [ ] Did I iterate at least once to address the weakest dimension?

## Technical Notes

- **Multimodal generation APIs**: Most APIs support style/aesthetic direction parameters. Use them. "Professional product poster with elegant typography and whitespace" >> "poster design".

- **Composition vs rendering**: If your generation tool produces template-like outputs, consider: (a) iterating with more specific design direction, or (b) generating layout concept then using design tools for final render.
