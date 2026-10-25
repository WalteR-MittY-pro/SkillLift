---
name: dataset-grounded-itinerary
description: Construct a dated, budgeted itinerary using only supplied travel, lodging, restaurant, and attraction datasets.
---

# dataset-grounded-itinerary

## Workflow
1. Parse hard constraints: date count, origin, destination region and distinct-city count, group size, pet, transport exclusions, budget, meal preferences, and output schema.
2. Inspect dataset headers and filter candidate destination cities using the supplied city/state mapping. Derive drivable legs from the distance matrix; do not substitute remembered distances. Reject missing or implausible legs rather than treating blanks as zero.
3. Filter accommodation by occupancy, stay length, house rules, and explicit pet evidence. Absence of a No pets rule alone may not establish pet-friendliness; record uncertainty when the dataset has no affirmative field.
4. Choose meals and attractions from dataset rows in the relevant city. Preserve source spellings, avoid impossible travel on a single day, and estimate costs with explicit assumptions for two travelers. Sum nights, meals, and transport against the budget.
5. Emit exactly the requested days and fields. Validate dates, three-city coverage, no flight strings, attraction delimiter syntax, pet policy evidence, and that every named POI exists in cited source files.

## Example
    assert len(plan) == required_days
    assert len(set(visited_destinations)) == required_city_count

List only dataset files actually used in the output's data_sources field.
