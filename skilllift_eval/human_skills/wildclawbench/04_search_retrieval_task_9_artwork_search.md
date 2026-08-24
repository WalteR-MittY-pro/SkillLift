---
name: 04-search-retrieval-task-9-artwork-search
description: Use when identifying an artwork from an image and determining its current exhibition location. Focuses on artwork identification, temporal-spatial tracking of traveling exhibitions, and current-location verification.
---

# Artwork Identification and Exhibition Tracking

## Core Challenge

Identifying an artwork from a photo is only the first step; the real challenge is determining where that artwork is physically located at a specific point in time. Artworks travel between museums for special exhibitions, loans, and tours. The "permanent collection location" is often wrong because the piece is currently on loan or touring.

## Solution Strategy

1. **Identify the artwork with maximum specificity**: Determine the title, artist, and if relevant, the specific version or edition. Multiple versions of the same subject exist (studies, copies, different editions). Use visual analysis and image search to pin down the exact work. → Identifying the right subject but wrong version leads to tracking a different painting's location.

2. **Search for current exhibition status, not permanent home**: After identifying the work, search specifically for its current or recent exhibition history. Use terms like "exhibition 2025," "on view," "on loan," or the specific museum name plus the artwork title. → Defaulting to the permanent collection location ignores traveling exhibitions and temporary loans.

3. **Cross-reference multiple sources for current location**: Museum websites, exhibition announcement pages, art news outlets, and social media all provide location data. Cross-check at least two sources, especially for time-sensitive exhibition information. → Trusting a single source that may be outdated (e.g., a museum's permanent collection page that hasn't been updated for a loan).

4. **Account for exhibition timelines**: Exhibitions have opening and closing dates. Verify the specific date window asked about falls within an active exhibition period. A piece listed for "Exhibition June-September 2025" is present in mid-July; a piece for "Spring 2025 only" may have already returned. → Recommending an exhibition venue without checking whether the exhibition is still active at the query date.

5. **Distinguish between venues in the same city**: Major cities have multiple museums. "In Shanghai" is insufficient — specify the exact museum. Satellite branches or affiliated venues within the same organization may confuse the search. → Providing the right city but wrong specific museum or venue.

## Decision Points

- **Image search vs visual analysis**: If the artwork is distinctive or well-known, a reverse image search or search with descriptive terms will identify it quickly. For lesser-known works, detailed visual analysis (style, subject, technique) narrows the field before searching.

- **When multiple locations appear in search results**: If the artwork has a permanent home listed but also appears in a current exhibition announcement, the exhibition location takes precedence for the query date. Museums loan pieces temporarily.

- **When the museum name is ambiguous**: Some museums have multiple branches or similar names. Use the full official name and verify the specific venue address or branch.

## Common Failure Patterns

- **Defaulting to permanent collection**: Agents identify the artwork, find its owning museum, and report that location. They never check whether the piece is currently on tour or loaned elsewhere. → Wrong location — the piece is at a temporary exhibition venue, not its home museum.

- **Outdated exhibition information**: Agents find a 2024 exhibition listing and report it as current for 2025. Exhibition information changes frequently. → Recommending a venue where the exhibition has already closed.

- **Stopping at identification**: Agents correctly identify the painting but don't complete the location search, reporting only the artwork's name and artist. → Incomplete answer that doesn't address the actual question.

- **Confusing similar venues**: Agents report the right city but confuse two museums with similar names or relationships (e.g., a main museum vs. its satellite branch). → Right city, wrong venue.

## Self-Check Questions

- [ ] Did I identify the exact artwork (title, artist, version) before searching for location?
- [ ] Did I search for CURRENT exhibition status, not just permanent collection ownership?
- [ ] Did I verify the exhibition is active during the specific time period asked about?
- [ ] Did I cross-reference at least two sources for the current location?
- [ ] Did I specify the exact museum/venue name, not just the city?
- [ ] Did I check whether the venue is a temporary exhibition or the permanent home?

## Technical Notes

- **Exhibition databases**: Museum websites' "current exhibitions" or "on view" pages are more reliable than collection database pages for determining current physical location.
- **Art news timing**: Major loan exhibitions are typically announced months in advance via art news outlets. Search for the artwork title plus the year to find recent exhibition announcements.
- **Vision model limitations**: Vision models can identify well-known artworks but may struggle with lesser-known pieces or specific versions. Always verify the identification via search before proceeding to location tracking.
