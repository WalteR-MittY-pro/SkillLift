---
name: d3-stock-cluster-table
description: Build a D3 v6 force-cluster bubble chart linked to a complete stock table.
---

# d3-stock-cluster-table

## Workflow
1. Inspect CSV headers and normalize ticker, name, sector, and numeric market cap. Treat missing market cap as an ETF or unknown according to source data; use a uniform fallback radius and suppress ETF tooltip only when the requirement calls for it.
2. Map sectors to compact center coordinates within the SVG. Use forceX and forceY toward each sector center, forceCollide with radius plus padding, and a centering force. Clamp or adjust simulation positions so bubbles remain visible.
3. Bind one circle per stock and no decorative SVG circles. Put sector legend in HTML div elements. Render ticker labels as text centered over bubbles; use an accessible title or text label for small bubbles where in-circle text cannot fit.
4. Build the table from the same normalized data, format market caps with K/M/B/T suffixes, and use a shared selected-ticker state so clicking either chart or row highlights both. Keep all text readable without ellipsis or clipping; allow wrapping or scrolling.
5. Load D3 v6 locally and serve the page over HTTP so CSV fetches work. Inspect browser console and verify stock count, circle count, sector legend, collision, tooltip policy, and bidirectional selection.

## Example
    simulation.force('collision', d3.forceCollide(d => d.radius + 2))

Use event-handler signatures from D3 v6: (event, datum).
