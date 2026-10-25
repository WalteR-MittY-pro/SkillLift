---
name: glm-calibration-and-exact-rmse
description: Calibrate permitted GLM namelist parameters and compute exact observation-to-model RMSE joins.
---

# glm-calibration-and-exact-rmse

## Workflow
1. Snapshot and diff the original namelist. Define an allowlist of calibration keys and their published ranges; edit only those keys. Preserve forcing inputs, initialization profile, and output settings.
2. Inspect GLM NetCDF dimensions, datetime origin/calendar, depth coordinates, and observation columns. Derive simulation timestamps in the same timezone basis as observations, then perform an exact datetime plus rounded-depth merge. Do not interpolate or use nearest-time matching for final metrics.
3. Run a baseline, calculate overall, deep, and summer-deep RMSE from matched pairs, and record pair counts. Search allowed parameter ranges with a coarse design followed by local refinement. Keep each trial configuration, metrics, and run status so the best valid candidate can be reproduced.
4. Re-run the chosen configuration from a clean output directory. Recompute exact metrics from the final NetCDF, verify thresholds and allowed-key diff, then serialize numeric metrics and counts.

## Example
    matched = obs.merge(sim, on=['datetime', 'rounded_depth'], how='inner')
    rmse = np.sqrt(np.mean((matched.obs_temp - matched.sim_temp) ** 2))

An empty subset must be treated as invalid, not as a passing RMSE.
