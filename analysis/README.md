# Measurement Plan

Written before any estimation, so the design is not shaped by the results.

## Question

Are CRS discounts in line with the loss reduction they buy? Secondary: does a one-class improvement reduce loss cost, and by how much?

## Definitions

- **Unit:** NFIP community by calendar year (`gold.community_year_panel`).
- **Window:** 2010 to 2021 (`regime = 'pre_rr2'`). 2009 is warm-up because 2008 policies are missing. 2022 onward is Risk Rating 2.0, which changed how CRS discounts apply; reported separately.
- **Primary outcome:** loss cost, paid claims per earned policy-year.
- **Secondary outcomes:** claims per 1,000 policy-years (frequency), paid per paid claim (severity), paid per $1,000 of coverage.
- **Not a primary outcome:** loss ratio. Premiums already include the CRS discount, so CRS communities show higher loss ratios mechanically.
- **Treatment:** CRS participation and class by year, rebuilt from Status Book entry and effective dates (`crs_status_basis` on the panel). Policies cannot be used: their CRS class is today's class back-stamped onto history.

## Analyses

**A. Pricing adequacy.** Loss cost by CRS class relative to Class 10, adjusted for SFHA share and county-by-year fixed effects, compared against the discount schedule (5 points per class). Benchmark: Gourevitch and Pinter found reductions roughly matching discounts nationally.

**B. Entry event study (main causal estimate).** About 465 communities entered CRS between 2011 and 2020, and 424 of them share a county with never-CRS communities. Callaway and Sant'Anna estimator with never-CRS communities in the same county as controls. A first raw look shows entrants had roughly twice the loss cost of same-county controls in the two years before entry, which is the flood-driven entry pattern. The design has to account for it: match or condition on pre-entry losses, and report the pre-trend.

**C. Class-step effect (dropped).** Claims are back-stamped the same way as policies (0 of 1,667 communities change class on claims), so no public source has year-by-year class history. A carries the class question.

## Why county-by-year fixed effects

Flood losses are dominated by rare events. Comparing communities within the same county in the same year means both experienced the same storm, which removes most of the weather noise that would otherwise swamp a 5% effect.

## Feasibility gates (run before estimating; `python -m pipeline.cli report`)

1. **Exposure is real.** National earned policy-years by year are close to FEMA's published policies-in-force totals (about 5 million in the 2010s) with no unexplained swings. Claim rates spike in known catastrophe years such as 2012 and 2017. If this fails, stop and fix the pipeline.
2. **There is variation to use.** Count CRS entries by year, class changes by year, and county-years with both CRS and non-CRS communities. If mixed county-years are rare, drop the county-by-year design.
3. **Power.** Simulate the minimum detectable effect for loss cost given observed variance. If the per-step MDE is above about 15%, report C as a pooled slope with the MDE stated rather than per-class effects.

## Status

A and B done; see [RESULTS.md](RESULTS.md). The estimation design was written before results, and the changes after the first load (B as the main causal design, C dropped) are recorded above with their reasons.

## Out of scope for this version

Propensity weighting and the Bayesian hierarchical model are natural next steps if the class-step estimate is too noisy.
