# Walkthrough

A 5-minute script for presenting this project, plus the questions each interviewer is likely to ask.

## The 5-minute version

**1. The question (30 seconds)**
FEMA's Community Rating System gives flood insurance discounts of 5% per class, up to 45%, to communities that invest in floodplain management. About 1,500 communities participate. The pricing question: do those communities actually lose 5% less per class? If not, the discount is mispriced.

**2. The data problem (60 seconds)**
Answering it needs loss cost, which is claims divided by exposure. FEMA publishes 2.7 million claims and 75 million policy transactions, but they don't link, and policies are transactions, not counts. I built a pipeline that:
- pulls claims from the API by state, reconciling every partition against the server's own count
- converts 75 million policy transactions into earned policy-years per community per year, split by days covered and cut off at cancellation
- builds a national panel of 335,000 community-years, with zero-claim years kept as zeros

Exposure matches the published national picture: about 5.5 million policy-years in 2010 declining to 4.7 million, with claim spikes in 2012 (Sandy) and 2017 (Harvey and Irma).

**3. The finding that changed the design (60 seconds)**
My feasibility gates checked that CRS class actually varies over time before I used it. It didn't. No community ever changed class in 18 years, and 91% of community-years before a community joined CRS were already labeled CRS. FEMA stamps today's class onto every historical record, in both policies and claims. I rebuilt class history from the Community Status Book's entry and effective dates, and every row records which rule assigned its class. That killed one planned analysis, the effect of class upgrades, and I documented why instead of running it on bad data.

**4. The result (60 seconds)**
Comparing communities in the same county and year, so they faced the same storms, each CRS class step goes with about 6% lower loss cost (95% CI: 0.2% to 11%) and 5% fewer claims per policy-year (2% to 8%). The schedule is 5% per step, so discounts look priced about right on average. That holds when I drop catastrophe years or any of the four largest states, though Florida carries much of the precision. It's an association: communities that earn better classes also differ in capacity and income.

**5. What I could not measure (30 seconds)**
I also tried to measure the causal effect of joining CRS with a stacked difference-in-differences: 395 entrants against 2,307 never-CRS neighbors in the same counties. The estimate is -12%, but the interval runs from -57% to +81%, and the design could only detect effects over 64%. Flood losses are too lumpy for 400 entrants. I report that as the result rather than reaching for a significant number.

**6. Close (30 seconds)**
If I had another week: recover historical CRS classes from FEMA's semiannual community lists, which would unlock the class-upgrade design, and add a Bayesian hierarchical model to stabilize the class-by-class estimates.

## Emphasis by interview

**Senior Data Engineer:** spend most of the time on sections 2 and 3. Open `ARCHITECTURE.md` and `pipeline/transform/sql/15_silver_policy_exposure.sql`, and show `ops.run_log` and `ops.dq_results`.

**Measurement Science:** spend most of the time on sections 3 to 5. Open `analysis/RESULTS.md`.

## Likely questions

**Data engineering**

*Why replace whole state partitions instead of upserting?*
FEMA revises old claims, and v3 has no reliable per-record change timestamp. Replacing a state at a time is idempotent and can't leave stale versions. At this volume it costs seconds.

*How do you know a load is complete?*
Each API partition is written to staging, its row count is compared to the API's server-side count for the same filter, and only then swapped in. A mismatch keeps the last good partition and its watermark. Every attempt is in `ops.run_log`.

*Why not load the policies through the API?*
It's 75 million rows, about 7,500 requests. The bulk Parquet file is one download. I keep it untouched as bronze with a hash and row count in a manifest, and enforce the column contract when registering it.

*How does the 75-million-row step perform?*
One streaming pass in DuckDB that never materializes the row-level grain: 31 seconds on a laptop.

*What would change in production?*
Orchestration (a scheduled monthly run), alerting on error-level checks, dbt for the models once there are more of them, and the same bronze, silver, and gold layers in a Fabric lakehouse or warehouse.

*What broke on the first real run?*
Three things the tests didn't cover. The CRS back-stamping. Shares coming out NULL instead of 0 because a filtered sum over zero rows is NULL in SQL. And 125 claims with negative payments, which turned out to be legitimate adjustments, so that check became a warning.

**Measurement**

*Why county-by-year fixed effects?*
Losses are dominated by a few storms. Comparing within county and year means both communities faced the same weather, which removes most of the noise that would swamp a 5% effect.

*Why Poisson (PPML) instead of OLS on log losses?*
Most community-years have zero claims. PPML keeps the zeros, takes exposure as an offset, and its coefficients read as percent changes in loss cost.

*Why not use loss ratio?*
Premiums already include the CRS discount, so CRS communities show higher loss ratios mechanically. Loss cost per policy-year isolates the losses.

*Is the 6% causal?*
No. It's the right comparison for checking whether the discount schedule tracks observed losses, but better-rated communities differ in other ways. The causal design (B) was too underpowered to settle it.

*Why is Class 9 worse than non-CRS?*
Class 9 is the entry tier, and communities tend to join after flooding. That fits selection, not CRS raising losses.

*What's the minimum detectable effect for B, and what would fix it?*
About 64% for loss cost. Fixes: more entrants (a longer window once history is recovered), a less volatile outcome such as claim frequency in non-catastrophe years, or pooling entry cohorts with a hierarchical model.

## Resume bullets

- Built a national flood-insurance data pipeline (Python, SQL, DuckDB) that turns 75M FEMA policy transactions and 2.7M claims into an analysis-ready panel of 335K community-years, with row-count reconciliation, schema contracts, and 20+ automated quality checks
- Caught a silent source-data defect through feasibility gates (historical CRS classes back-stamped onto 91% of pre-entry records) and rebuilt treatment history from a second source
- Estimated that each FEMA CRS class step is associated with about 6% lower flood loss cost (95% CI 0.2% to 11%) against a 5% scheduled discount, using Poisson regression with county-by-year fixed effects; quantified a 64% minimum detectable effect for the entry design and reported it as underpowered
