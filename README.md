# NFIP CRS Measurement

**Business question:** FEMA's Community Rating System (CRS) gives flood insurance discounts of 5% to 45% to communities that invest in floodplain management. Do participating communities actually produce lower claims, and do the discounts match the loss reduction?

This repo has two halves:

1. **A data pipeline** that pulls National Flood Insurance Program (NFIP) claims from the OpenFEMA API, lands them in a Parquet lake, validates and reconciles every load, and builds an analysis-ready community-year panel in DuckDB.
2. **A causal analysis** on that panel (staggered difference-in-differences, propensity weighting, Bayesian hierarchical model) estimating the effect of CRS participation on claims. *In progress; see [analysis/](analysis/).*

## Where to start

**Reviewing the data engineering:** [ARCHITECTURE.md](ARCHITECTURE.md) for design decisions, then [pipeline/ingest/loader.py](pipeline/ingest/loader.py), [pipeline/quality/checks.py](pipeline/quality/checks.py), and [TEST_PLAN.md](TEST_PLAN.md).

**Reviewing the measurement work:** [analysis/README.md](analysis/README.md) for the measurement plan, then the gold table definition in [pipeline/transform/sql/20_gold_community_year_claims.sql](pipeline/transform/sql/20_gold_community_year_claims.sql).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,analysis]"

pytest -q                                   # 23 tests, no network needed
python -m pipeline.cli run                  # ingest pilot states, build tables, run checks
python -m pipeline.cli run --states LA TX   # different scope
python -m pipeline.cli run --force          # reload even if FEMA has not refreshed
```

Then query the warehouse:

```bash
duckdb data/warehouse.duckdb "select * from gold.community_year_claims order by paid_total desc limit 10"
duckdb data/warehouse.duckdb "select * from ops.run_log order by finished_at desc"
duckdb data/warehouse.duckdb "select * from ops.dq_results where not passed"
```

## Layout

```
config/pipeline.yaml          datasets, scope, API settings
pipeline/ingest/              API client, pinned schema, partition loader
pipeline/transform/sql/       silver and gold models (plain SQL, run in order)
pipeline/quality/             data quality and reconciliation checks
pipeline/cli.py               ingest | transform | quality | run | discover
tests/                        unit and integration tests against a fake API
analysis/                     measurement plan and causal notebooks
dashboard/                    Power BI report on the gold tables
```

## Data source

OpenFEMA [NFIP Redacted Claims v3](https://www.fema.gov/openfema-data-page/nfip-redacted-claims-v3) (`NfipClaims`), about 2.7 million claim transactions, refreshed roughly monthly. Not an official federal report. Use is subject to the [OpenFEMA terms](https://www.fema.gov/about/openfema/terms-conditions).
