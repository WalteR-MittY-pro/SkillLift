---
name: 05-creative-synthesis-task-6-clothing-outfit-to-model-image
description: Use when classifying fashion items, composing coherent outfits from classified items, and generating model images wearing those outfits. Focuses on visual taxonomy, combinatorial matching coherence, and cross-modal consistency.
---

# Fashion Item Classification, Outfit Composition, and Model Image Generation

## Core Challenge

This task chains three distinct cognitive operations: classifying individual clothing items from flat-lay photos (what category, what features), grouping them into stylistically coherent complete outfits (which items go together), and generating model images where the worn clothing faithfully matches the outfit description. Each stage compounds errors—misclassification cascades into wrong outfits, which cascade into inconsistent model images.

## Solution Strategy

1. **Classify each item independently before pairing**: Analyze each clothing photo separately to determine: category (top, bottom, shoes), specific type (e.g., denim jeans vs pleated skirt), color, pattern, material, and style formality. Build a structured profile per item. Common mistake: agents rush to pairing without establishing what each item actually is.

2. **Match on coherence principles, not just completeness**: An outfit isn't just "any top + any bottom + any shoes." Consider color harmony, style consistency (formal with formal, casual with casual), gender alignment, and visual balance. The best outfit is one a stylist would actually recommend, not the first valid permutation. Common mistake: agents use greedy assignment—first available top with first available bottom—producing incoherent combinations.

3. **Use all items exactly once**: With N items and M outfits, every item must appear in exactly one outfit. No item left behind, no item reused. Track assignment as a constraint satisfaction problem. Common mistake: agents reuse an item across outfits or leave one unassigned.

4. **Generate model images that match the ACTUAL outfit**: The generated model must wear the specific items from the outfit—not a similar garment, not a generic version. Include the exact colors, patterns, and item types in the generation prompt. Verify by comparing the output to the source flat-lay photos. Common mistake: agents generate a model wearing "a white shirt and jeans" when the outfit has a specific striped blouse and tailored trousers.

5. **Verify cross-modal consistency post-generation**: After generating each model image, visually compare it back to the source clothing photos. Does the top match? The bottom? The shoes? If any item is misrepresented, regenerate with more specific descriptions. Common mistake: agents trust the generation prompt and never verify visual consistency.

## Decision Points

- **Outfit pairing strategy**: If items have clear formal/casual/gender signals, cluster by those attributes first, then build outfits within clusters. If signals are mixed, optimize for color harmony and visual balance.
- **Gender assignment**: Infer from the clothing style (cut, fit, typical gender association) and assign consistently per outfit. If an outfit mixes masculine and feminine items, choose the dominant signal.
- **Model image generation specificity**: Always include exact item descriptions in the prompt: "a female model wearing a cream-colored knit sweater, high-waisted dark wash straight-leg jeans, and white leather minimalist sneakers, full body shot." Vague prompts produce inconsistent results.

## Common Failure Patterns

- **Rushed classification**: Agents assign categories without careful visual analysis, confusing a tunic (top) with a short dress, or a skirt with wide-leg pants. → Wrong categories cascade into structurally invalid outfits.

- **First-fit pairing**: Agents pair items greedily—the first top with the first bottom that's available—ignoring whether the combination makes aesthetic sense. → Incoherent outfits that clash in color, style, or formality.

- **Generic model images**: Agents generate "a person wearing clothes" without specifying the exact items. The model ends up wearing something that looks vaguely like the outfit but doesn't match specifics. → Model images that don't correspond to the classified outfit.

- **No verification loop**: Agents generate model images and submit without checking whether the depicted clothing matches the source items. → Persistent mismatches between flat-lay photos and model renderings.

- **Assignment constraint violations**: Agents lose track of which items they've used, reusing or omitting items across outfits. → Structural failure: not all items represented, or duplicates present.

## Self-Check Questions

- [ ] Did I classify each clothing item independently with specific category and features?
- [ ] Are outfits composed based on coherence (color, style, formality)—not just availability?
- [ ] Does every item appear in exactly one outfit (no omissions, no duplicates)?
- [ ] Are model images generated with specific item descriptions (not generic)?
- [ ] Did I visually verify that each model image matches its source clothing photos?
- [ ] Is the gender assignment consistent across items within each outfit?
- [ ] Are descriptions written in the required language with correct terminology?

## Technical Notes

- **Flat-lay vs model image gap**: Flat-lay photos show items without context. When generating model images, the AI must infer fit, drape, and proportion. Include fit descriptions (slim, relaxed, oversized) inferred from the garment cut to improve accuracy.
- **Consistency verification**: After generation, use a vision model to compare the model image against the source flat-lay photos. Ask: "Does the top in this model image match this flat-lay photo?" If any item fails verification, regenerate.
