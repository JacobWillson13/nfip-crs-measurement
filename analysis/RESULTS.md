# Results

Data: national NFIP panel, 2010 to 2024, 20,668 communities, 334,656 community-years (OpenFEMA, loaded 2026-10-15). Code: [run_analysis.py](run_analysis.py). Tables: [results/](results/).

## Answer

**On average, CRS discounts look priced about right, but the data cannot pin it down tightly.** Within the same county and year, each CRS class step is associated with about **6% lower loss cost** (95% CI: 0.2% to 11% lower) and **5% fewer claims per policy-year** (2% to 8% fewer). The discount schedule gives 5% per step. The estimate is consistent with the schedule. It narrowly rules out no effect, and rules out effects above about 11% per step.

**The causal effect of joining CRS cannot be measured precisely with this data.** Communities that entered CRS between 2012 and 2020 show an estimated 12% lower loss cost afterward, but the confidence interval runs from 57% lower to 81% higher. The design could only detect an effect of about 64% or more, roughly ten times the size of the effect A suggests.

## A. Loss cost by CRS class (pricing adequacy)

Poisson (PPML) regression of paid claims on CRS class, with log earned policy-years as the offset and county-by-year fixed effects, controlling for SFHA share. Standard errors clustered by county. The sample is community-years whose CRS class is known (see "CRS history" below). Identification comes from the 5,170 county-years with communities in different classes.

| Model | Effect | 95% CI | Schedule |
| --- | --- | --- | --- |
| Loss cost, per class step | -5.9% | -11.2% to -0.2% | -5% |
| Claim frequency, per class step | -5.1% | -8.0% to -2.1% | -5% |
| Loss cost per step, 2010 to 2021 | -8.0% | -18.1% to +3.2% | -5% |
| Loss cost per step, 2022 to 2024 (Risk Rating 2.0) | -0.8% | -5.5% to +4.1% | -5% |

By class, relative to non-CRS communities in the same county and year:

| Class | Loss cost | 95% CI | Claim frequency | 95% CI | Schedule |
| --- | --- | --- | --- | --- | --- |
| 1 to 5 | -33.5% | -57.4% to +3.7% | -29.1% | -42.5% to -12.5% | -25% (Class 5) |
| 6 | -23.9% | -53.3% to +23.8% | -25.8% | -43.5% to -2.5% | -20% |
| 7 | +4.1% | -21.7% to +38.5% | -1.1% | -14.9% to +15.0% | -15% |
| 8 | -21.5% | -42.6% to +7.2% | -17.0% | -30.9% to -0.3% | -10% |
| 9 | +47.2% | +10.1% to +96.7% | +20.2% | -1.6% to +46.8% | -5% |

The pattern runs in the expected direction from Class 8 up. Class 9 communities have *higher* losses than their non-CRS neighbors. Class 9 is the entry tier, and communities often join CRS after flooding, so that reading is consistent with selection rather than with CRS raising losses.

**Robustness (loss cost per class step):**

| Variant | Effect | 95% CI |
| --- | --- | --- |
| Base | -5.9% | -11.2% to -0.2% |
| Drop 2017 (Harvey, Irma) | -6.3% | -11.7% to -0.5% |
| Drop 2012, 2017, 2024 | -9.7% | -21.5% to +3.9% |
| Drop Texas | -7.2% | -13.0% to -1.1% |
| Drop Florida | -3.6% | -10.2% to +3.5% |
| Drop Louisiana | -5.6% | -11.0% to +0.1% |
| Drop New Jersey | -6.0% | -11.7% to 0.0% |
| Drop Class 9 | -6.4% | -11.9% to -0.6% |
| Cap community-year losses at the 99th percentile | -19.9% | -25.1% to -14.3% |

The point estimate stays between -4% and -10% across sample changes, and Florida carries much of the precision. Capping extreme losses makes the association much stronger, so the relationship is clearest in ordinary years and noisiest in catastrophes. Capping is shown for diagnosis only: catastrophe losses are exactly what premiums must pay for.

**What A is and is not.** A compares communities in different classes in the same county and year. It is the right comparison for asking whether the discount schedule tracks observed losses. It is not a causal effect of CRS: communities that earn better classes also differ in capacity and income (Li and Landry, 2018), and some of that may lower losses independently.

## B. Effect of entering CRS (stacked difference-in-differences)

Each entry cohort from 2012 to 2020 is stacked with never-CRS communities **in the entrants' own counties**, over a window of three years before to four years after entry. PPML with community and stack-by-year fixed effects; errors clustered by county. 395 entrants, 2,307 controls, 241 counties.

| Model | Effect | 95% CI | Minimum detectable effect |
| --- | --- | --- | --- |
| Loss cost after entry | -11.7% | -57.0% to +81.2% | 64% |
| Claim frequency after entry | -1.3% | -43.6% to +72.5% | 55% |
| Pre-entry loss gap vs same-county controls | -1.0% | -43.8% to +74.4% | 55% |

The event-study coefficients (in `results/b_entry_did.csv`) swing from -94% to +22% from year to year, which is catastrophe noise, not a pattern.

**Two findings from B:**

1. **It is underpowered.** With about 400 entrants and catastrophe-driven losses, an entry effect has to be over 50% to be detected. A suggests the true effect is closer to 5% to 25%. Measuring it would need many more entrants or a less volatile outcome.
2. **The "join after a flood" worry is mostly a cross-county effect.** In the raw data, entrants had about twice the loss cost of never-CRS communities in the two years before joining. Compared within the same county and year, the gap is near zero (-1%, with a wide CI). Entrants tend to be in flood-prone counties, not to be the hardest-hit community in their county. That supports using same-county comparisons.

## CRS history: why class changes cannot be studied

FEMA's redacted policies and claims both carry each community's **current** CRS class on every historical record. No community ever changes class in either dataset, and 91% of community-years before a community joined CRS are stamped as CRS. Class history is rebuilt from Community Status Book dates instead:

- Before the CRS entry date, a community is not in CRS (Class 10).
- From the current class's effective date onward, it has the current class.
- In between, it is in CRS with an unknown class, and excluded from A.

So A uses 9,413 CRS community-years with a known class, weighted toward recent years (224 CRS communities have a known class in 2010, 1,249 in 2024). The planned class-step design (C) needs year-by-year class history and is not feasible with public data.

## Limitations

- A is an association within county and year, not a causal effect.
- Known-class years skew recent. That is also why the 2010 to 2021 estimate is less precise than the full window.
- Communities that entered CRS and later left are excluded after entry (3,746 community-years), which keeps the data clean but may favor successful participants.
- The most recent claim years are still developing, so 2025 and 2026 are excluded.
- Loss cost uses NFIP paid amounts only, not uninsured damage.

## Next steps

- Recover historical CRS classes from FEMA's semiannual CRS community lists. That would unlock the class-step design and expand A's 2010 to 2021 sample.
- A Bayesian hierarchical model of class effects, to stabilize the noisy class-by-class estimates through partial pooling.
- Separate building from contents losses, and SFHA from non-SFHA exposure.
