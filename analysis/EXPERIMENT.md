# Experiment design: randomized CRS recruitment

A pre-registration-style plan for the experiment that would measure what the observational data cannot: whether joining CRS reduces flood claims. It is written as a proposal to FEMA's CRS program. Power numbers come from replaying the real panel in [run_experiment.py](run_experiment.py); outputs are in `results/e_experiment_power.csv` and [figures/e_experiment_power.png](figures/e_experiment_power.png).

**Bottom line: run the experiment to measure recruitment, not claims.** A randomized offer of CRS application support would measure, precisely, how much outreach raises participation (a 1.5-point increase is detectable). It cannot measure the effect of participation on claims at any feasible scale: even offering support to every eligible community, joining would have to cut claims by about 37% (if half of offered communities join) to be detected, and analysis A suggests a few percent per class. Randomization removes the bias that broke the observational design, but it does nothing about catastrophe noise in claims.

## The decision it informs

There are two separate decisions in this project, and they need different evidence.

| Decision | Who | Quantity needed | Source |
| --- | --- | --- | --- |
| Are CRS discounts priced right? | NFIP pricing | Predictive relativity: expected loss of a class-c community vs a non-CRS neighbor | Analysis A and the decision analysis (RESULTS.md, D). Causality is not required for pricing. |
| Is recruiting more communities into CRS worth it? | CRS program | Causal effect of joining on losses, and the cost of recruiting one more community | This experiment |

## Design

- **Population.** NFIP communities that have never been in CRS, average at least 25 policy-years a year, and appear in at least 13 of 15 panel years: 5,612 communities in 1,948 counties, holding 92% of never-CRS claims. About 3.9% of comparable communities join CRS on their own within five years.
- **Treatment.** An offer of CRS application support: a CRS specialist visit, help documenting creditable activities, and help with the verification visit. Control communities get the status quo.
- **Assignment.** Randomized within county: half of each county's eligible communities are offered (an odd community is assigned by coin flip). Blocking on county means offered and control communities face the same storms.
- **Follow-up.** Five years from the offer.
- **Estimands.**
  - *Uptake (first stage):* the increase in the share of communities that join CRS.
  - *ITT:* the effect of the offer on claims per policy-year.
  - *Complier effect (CACE):* the effect of joining for communities that join because of the offer. ITT divided by the uptake difference.
- **Primary outcome.** Uptake. **Secondary:** claims per policy-year (PPML with policy-year offset). **Exploratory:** loss cost.

## Analysis plan

- Uptake: difference in proportions, standard errors clustered by county.
- Claims: offered and control communities summed within each county-year, PPML with county-by-year fixed effects (the within-county design from analysis B), errors clustered by county. The simulation found that adding a pre-period (difference-in-differences, a CUPED-style adjustment) did not reduce variance, so the follow-up-only comparison is primary.
- **Inference by randomization.** Analytic standard errors flagged 7% to 12% of placebo assignments as significant instead of 5%. p-values come from re-randomizing assignment within counties, holding the outcome data fixed.
- **Checks before reading results.** Balance of pre-period claims and exposure by arm. Sample ratio within each county. Attrition (communities leaving the NFIP) by arm.

## Power

Detectable effects at 80% power and 5% significance, from the spread of 120 placebo assignments per configuration, replayed over three historical windows (follow-up starting 2013, 2017, and 2020):

| Design | Communities | Offer effect on claims (ITT) | Effect of joining, 50% uptake | Effect of joining, 25% uptake |
| --- | --- | --- | --- | --- |
| Within county, follow-up only | 5,612 (all eligible) | 21% | 37% | 61% |
| Within county, follow-up only | ~2,500 pilot | 29% | 50% | 75% |
| Within county, follow-up only | ~1,000 pilot | 37% | 60% | 84% |
| Within county, pre/post | 5,612 | 23% | 40% | 64% |
| Whole counties, pre/post | 5,612 | 71% | 92% | 99% |

Against a true 20% effect of joining, power with every eligible community is 22% at 50% uptake and 6% at 25% uptake.

**What drives this:**

- **Dilution.** Only communities that join because of the offer carry the effect, so the ITT is roughly the uptake share times the effect of joining. At realistic uptake, the ITT is a few percent.
- **Catastrophe noise.** Five years of claims in a county is dominated by whether a hurricane hit it. Within-county blocking removes the shared part, but storms still hit communities in a county unevenly.
- **Randomizing whole counties** would protect against spillovers (neighboring communities copying each other's floodplain rules) but leaves county storm shocks in the comparison. It is not viable for a claims outcome.

## Recommendation

1. **Run the trial with uptake as the primary outcome.** It answers a decision FEMA controls (is outreach worth its cost per community recruited?) and is well powered: a 1.5-point increase over the 3.9% baseline is detectable with every eligible community (before allowing for clustering).
2. **Do not make claims the primary outcome.** Report the ITT on claims as secondary, with its minimum detectable effect stated in advance, so a null is not read as "CRS doesn't work".
3. **For the effect on losses, change the outcome, not the sample.** A modeled outcome, such as catastrophe-model average annual loss for the offered and control communities' building stock, removes year-to-year weather noise and would make the complier effect measurable. Counting the mitigation actions that CRS credits (elevations, buyouts, freeboard ordinances) is a cheaper intermediate outcome.

## Threats

- **Spillovers within county.** If offered communities' activity reaches their neighbors (shared county floodplain managers), the within-county ITT is biased toward zero. Measure uptake among control communities in counties with high vs low offered uptake.
- **Control communities joining anyway.** Expected (3.9% over five years); it lowers the uptake difference and is handled by the CACE.
- **Program changes during follow-up.** NFIP pricing and CRS crediting rules change over five years for both arms equally; randomization protects the comparison, not the generalization.
