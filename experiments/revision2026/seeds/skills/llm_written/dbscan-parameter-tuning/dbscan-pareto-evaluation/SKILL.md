---
name: dbscan-pareto-evaluation
description: Evaluate anisotropic DBSCAN grids with image-level matching, missing-image rules, and Pareto filtering.
---

# dbscan-pareto-evaluation

## Workflow
1. Group annotations by image key. Iterate the union or authoritative expert-image set specified by the task; include expert images with zero crowd points in the F1 denominator.
2. For shape weight w, transform coordinates to (w*x, (2-w)*y) before Euclidean DBSCAN. Do not use the transformed metric for expert matching.
3. For each nonnoise cluster, calculate the centroid in original x/y. Compute all centroid-to-expert Euclidean distances, sort ascending, and greedily take pairs no farther than the stated cutoff while enforcing one-to-one use.
4. Per image, precision = matches/clusters and recall = matches/experts, with F1 = 0 for missing or unmatched cases. Delta is mean matched distance; use NaN when no matches. Average F1 across every expert image and delta across finite per-image deltas only.
5. Filter on the stated F1 threshold, then retain a point if no other candidate has at least as high F1 and at most as low delta with one strict improvement. Round only when serializing, never before dominance comparisons.

## Example
    points_scaled = points * [w, 2 - w]
    labels = DBSCAN(eps=eps, min_samples=k).fit_predict(points_scaled)

Cache image grouping and consider parallelizing parameter combinations; compare a few tiny synthetic cases for matching and dominance semantics.
