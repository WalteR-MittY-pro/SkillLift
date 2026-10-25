---
name: projected-plate-distance
description: Find events inside a plate polygon and measure distance to its boundary in a suitable projected CRS.
---

# projected-plate-distance

## Workflow
1. Inspect GeoJSON geometry and properties; select the target plate by an explicit plate identifier or name, not by row position. Parse event Point coordinates as longitude, latitude in EPSG:4326.
2. Repair invalid polygon geometry if needed and handle antimeridian wrapping before containment. Decide whether a boundary point counts as inside according to the task.
3. Use GeoPandas to project candidate event points and the plate boundary to a suitable meter-based CRS. For a plate spanning a very large area, test projection distortion at representative locations; one global projection may bias rankings. A local azimuthal projection centered on each candidate or segment can provide a robustness cross-check.
4. Compute point-to-boundary geometry distance in meters, convert to kilometers, and rank before rounding. Convert millisecond epoch times to UTC ISO 8601.

## Example
    events = gpd.GeoDataFrame(records, geometry=gpd.points_from_xy(lon, lat), crs='EPSG:4326')
    inside = events[events.geometry.apply(plate.covers)]
    projected = inside.to_crs(chosen_crs)

Inspect the top few candidates under an independent projection choice before selecting the maximum.
