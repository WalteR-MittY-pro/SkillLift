---
name: regulated-verse-check
description: Compose and verify seven-character regulated verse with Mandarin rhyme and parallel middle couplets.
---

# regulated-verse-check

## Workflow
1. Draft eight lines of exactly seven Han characters, plus a separate title. Give the opening a concrete human viewpoint, develop the scene in the middle couplets, and resolve it in the final couplet.
2. Choose one modern Mandarin rhyme family before drafting. Check the final syllables of even-numbered lines for the same rhyme; treat first-line rhyme as optional unless specified. Verify pronunciation for polyphonic characters in context.
3. Check the second and third couplets for balanced grammar and imagery. Review tonal patterns if strict regulated-verse compliance is required; modern Mandarin rhyme alone does not establish historical pingze correctness.
4. Count characters excluding punctuation and whitespace, and inspect exactly nine nonempty output lines. Re-read for coherent narrative rather than mechanical rhyme.

## Example check
In Python, strip punctuation from each verse line and assert len(line) == 7 for eight lines. Maintain a small table of verified final-character pinyin readings; do not guess pronunciation from the glyph alone.
