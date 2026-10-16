# Measurement Plan

Written before any estimation, so the design is not shaped by the results.

## Question

Does CRS participation reduce NFIP claims in a community, and is the reduction at each CRS class roughly equal to the premium discount that class receives?

## Definitions

- **Unit:** NFIP community (`community_id`), by year.
- **Treatment:** CRS participation, and separately CRS class (1 to 9; class 10 means no discount).
- **Primary outcome:** claims paid per policy in force (requires the policies dataset for exposure).
- **Secondary outcomes:** claim frequency per policy; average paid per paid claim (severity).
- **Population:** communities in the pilot states with NFIP policies in force across the study window.

## Identification

1. **Naive comparison.** CRS versus non-CRS communities. Expected to be biased because high-risk communities select into CRS.
2. **Propensity weighting.** Weight on pre-period loss history, exposure, and flood zone mix.
3. **Staggered difference-in-differences.** Callaway and Sant'Anna estimator by year of CRS entry, with an event study and pre-trend checks.
4. **Bayesian hierarchical model.** Class-level effects with partial pooling across communities.

## Key assumptions and threats

- Parallel trends between joiners and not-yet-joiners, conditional on covariates.
- Flood losses are driven by rare events; years without a flood carry little information. Condition on event exposure or use event-level data.
- CRS class history must come from FEMA's published lists, not from claims, which only reveal class in years with claims.

## Status

Pipeline and claims panel built. Next: ingest policies for exposure, ingest CRS community history, then notebooks.
