---
name: 04-search-retrieval-task-7-location-search
description: Use when identifying a real-world location from a photograph. Focuses on visual clue extraction, systematic geographic search, and coordinate verification.
---

# Multimodal Image-Based Location Identification

## Core Challenge

Identifying a precise geographic location from a photograph requires extracting every visible clue (text, architecture, signage, vegetation, infrastructure) and using them as search anchors. The challenge is moving from "this looks like X" to verified coordinates, requiring multiple search rounds and cross-referencing visual evidence against geographic databases.

## Solution Strategy

1. **Extract ALL visual clues before searching**: Analyze the image systematically — text on signs/buildings, architectural style, language scripts, vehicle license plate formats, vegetation types, street infrastructure, landmarks, sky/weather indicators. Document each clue. → Starting searches based on one or two obvious clues misses disambiguating evidence and returns many false candidates.

2. **Prioritize text-based clues for search**: Any visible text (signage, billboards, shop names, transit signage) is the highest-value clue because it is directly searchable. Extract and use text verbatim in queries. → Searching on visual style ("modern Asian city") returns generic results, while specific text ("building name on sign") returns the exact location.

3. **Use landmarks as geographic anchors**: Distinctive buildings, monuments, or geographic features visible in the image can be identified via image search or landmark databases. Once identified, they fix the city or region. → Ignoring prominent landmarks and focusing only on street-level details misses the fastest identification path.

4. **Verify coordinates match the visual evidence**: After identifying a candidate location, cross-check: Do the coordinates place the viewpoint in a position consistent with the image's perspective? Are the landmarks in the right relative positions? → Reporting coordinates for the right city but wrong viewpoint, or confusing north/south coordinates.

5. **Provide coordinates at appropriate precision**: Match the required precision (typically 2 decimal places, ~1km accuracy). Over-precise coordinates imply false confidence; under-precise coordinates may not distinguish nearby locations. → Giving 6 decimal places when the identification is only certain to neighborhood level, or rounding too aggressively.

## Decision Points

- **Image search vs text search**: If the image contains readable text or distinctive landmarks, text-based search is more reliable. If the image is a natural landscape without text, reverse image search or searching distinctive geographic features is the path.

- **When to use vision API vs direct analysis**: For images with clear text and landmarks, direct analysis suffices. For images with subtle or ambiguous visual cues, a vision model can extract clues that might be missed.

- **City vs neighborhood precision**: If the question asks for city-level location, city identification is sufficient. If landmarks narrow it further, provide the more precise location but ensure coordinates reflect the actual viewpoint.

## Common Failure Patterns

- **Superficial visual analysis**: Agents glance at the image, identify a general category ("Chinese city street"), and search generically. → Identifying the wrong city within the same country or region.

- **Ignoring small text**: Agents focus on large landmarks and miss small signage, transit maps, or shop names that contain the exact location identifier. → Missing the most direct search anchor available.

- **Confusing similar locations**: Cities with similar architecture or in the same region get confused. Agents conclude "Beijing" when the image shows Shanghai because both have modern Chinese urban architecture. → Wrong city, wrong coordinates.

- **Unverified coordinate accuracy**: Agents identify the right city but look up generic city-center coordinates rather than the specific viewpoint coordinates. → Correct city but coordinates don't match the actual photo location.

## Self-Check Questions

- [ ] Did I extract ALL visible text, landmarks, and environmental clues before searching?
- [ ] Did I use the most specific, searchable clue (text, landmark name) as the primary query?
- [ ] Did I verify the identified location against the image (do landmarks match perspective)?
- [ ] Did I provide coordinates at the precision level requested?
- [ ] Did I cross-check city and country for consistency (does the city exist in the stated country)?
- [ ] Did I confirm coordinates using a geographic database rather than estimating?

## Technical Notes

- **Coordinate precision**: Two decimal places ≈ 1.1 km at the equator. Five decimal places ≈ 1.1 meters. Match the required precision and don't overstate accuracy.
- **Multilingual signage**: Signs may contain multiple languages. Text in a non-Latin script (Chinese, Arabic, Cyrillic) is a strong geographic indicator. Use the exact script in search queries for best results.
- **Vision API usage**: When using a vision model, ask specifically for "all visible text," "architectural style," "identifiable landmarks," and "geographic indicators" to maximize clue extraction.
