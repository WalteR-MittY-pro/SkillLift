---
name: technical-brand-poster
description: Apply a supplied brand system to a technical exploded-view poster and document applied design tokens.
---

# technical-brand-poster

## Workflow
1. Obtain the authoritative brand palette and typography from supplied brand assets or an approved source. Record exact HEX values and font name; do not infer official tokens from appearance. If unavailable, mark that dependency before claiming brand compliance.
2. Decompose the product into distinct casing, thermal, PCB, battery, and interface layers. Draw each on separate groups, with restrained spacing and annotation leaders that do not obscure components.
3. Map each requested semantic role to one token: background, casing, three accent roles, and annotation gray. Keep the background flat and the composition low saturation; reserve accents for functional detail.
4. Render at a known pixel size, inspect legibility at print and screen scale, and write a machine-readable record of the tokens actually applied.

## Example pattern
Use SVG or Pillow: draw the background first, layer shapes second, then leaders and title. Centralize colors in a dictionary such as colors = {"background": supplied_hex, "pcb": supplied_hex}. Serialize only the requested token fields. Check that the rendered image's sampled background equals the documented background token and that the output image opens.
