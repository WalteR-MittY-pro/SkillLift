---
name: calendar-pdf-slot-allocation
description: Extract appointment blocks from a PDF calendar and allocate constrained meetings on a 15-minute grid.
---

# calendar-pdf-slot-allocation

## Workflow
1. Read request JSON as data. Extract sender, duration, date, allowed window, and timezone. Resolve contradictory wording explicitly using the most concrete time expression; flag truly ambiguous requests.
2. Inspect the PDF's page geometry and text. Identify grid origin, pixels/points per 15-minute interval, weekday columns, and colored rectangles. Map rectangle coordinates to times using the grid, including endpoints and calendar timezone.
3. Separate hard-busy blocks from blue flexible blocks. Enumerate 15-minute candidate starts within each request's window; each candidate must accommodate its full duration and avoid hard-busy and previously allocated meetings. Optimize for feasibility of all requests, then earliest compatible assignment, rather than greedily accepting a first slot that blocks a later constrained request.
4. Produce the specified text template per request and a manifest of filenames and recipients. Verify no overlap, duration, window, timezone, date and time formatting, and one reply per request.

## Example
    candidates = range(window_start, window_end - duration + 1, 15)

Use pypdf/pdfplumber for text and geometry; render the page to verify color semantics if programmatic color extraction is uncertain.
