# NFIP CRS Measurement

**Business question:** FEMA's Community Rating System (CRS) gives flood insurance discounts of 5% to 45% to communities that invest in floodplain management. Are those discounts in line with the loss reduction they actually buy, and how confident can we be?

This repo has two halves:

1. **A data pipeline** that pulls National Flood Insurance Program (NFIP) claims, policies, and community CRS status from OpenFEMA, turns about 75 million policy transactions into earned exposure, validates and reconciles every load, and builds a national community-year panel in DuckDB.
2. **A measurement analysis** on that panel: loss cost by CRS class against the discount schedule, a pooled class-step model with county-by-year fixed effects, and an entry event study. *In progress; see [analysis/](analysis/).*

## Where to start

**Reviewing the data engineering:** [ARCHITECTURE.md](ARCHITECTURE.md), then [pipeline/transform/sql/15_silver_policy_exposure.sql](pipeline/transform/sql/15_silver_policy_exposure.sql), [pipeline/ingest/](pipeline/ingest/), [pipeline/quality/checks.py](pipeline/quality/checks.py), and [TEST_PLAN.md](TEST_PLAN.md).

**Reviewing the measurement work:** [analysis/README.md](analysis/README.md) for the measurement plan and feasibility gates, then the panel definition in [pipeline/transform/sql/30_gold_community_year_panel.sql](pipeline/transform/sql/30_gold_community_year_panel.sql).

## Running it

Needs Linux or macOS, Python 3.11+, about 15 GB free disk, 16 GB RAM recommended.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,analysis]"
pytest -q                                          # 39 tests, no network needed

# 1. API sources: national claims (about 273 API pages) and the Community Status Book
python -m pipeline.cli ingest

# 2. Policies: about 75M rows, bulk Parquet
python -m pipeline.cli download nfip_policies
#    If fema.gov blocks the scripted download, open this URL in a browser:
#    https://www.fema.gov/about/reports-and-data/openfema/v3/NfipPoliciesV3.parquet
#    then register the saved file:
python -m pipeline.cli register nfip_policies --file ~/Downloads/NfipPoliciesV3.parquet

# 3. Build silver and gold, run quality checks
python -m pipeline.cli transform
python -m pipeline.cli quality

# 4. Feasibility gates 1 and 2, and the shareable panel
python -m pipeline.cli report
python -m pipeline.cli export      # writes data/export/community_year_panel.parquet
```

Every step is idempotent. Re-running `ingest` skips sources FEMA has not refreshed; `--force` overrides.

Inspect results directly:

```bash
duckdb data/warehouse.duckdb "select * from ops.run_log order by finished_at desc limit 20"
duckdb data/warehouse.duckdb "select * from ops.dq_results where not passed"
duckdb data/warehouse.duckdb "select year, sum(earned_policy_years) from gold.community_year_panel group by 1 order by 1"
```

## Layout

```
config/pipeline.yaml          datasets, scope, panel years, DuckDB limits
pipeline/ingest/              API client, pinned schemas, partition loader, bulk file register
pipeline/transform/sql/       silver and gold models, run in order, each declares its inputs
pipeline/quality/             quality and reconciliation checks, feasibility report
pipeline/cli.py               ingest | download | register | transform | quality | report | export
tests/                        unit and end-to-end tests against a fake API and synthetic policies
analysis/                     measurement plan, gates, and notebooks
dashboard/                    Power BI report on the panel
```

## Data sources

All from [OpenFEMA](https://www.fema.gov/about/openfema/data-sets). Not official federal reports; use is subject to the [OpenFEMA terms](https://www.fema.gov/about/openfema/terms-conditions).

| Dataset | Rows | Used for |
| --- | --- | --- |
| [NFIP Redacted Claims v3](https://www.fema.gov/openfema-data-page/nfip-redacted-claims-v3) | 2.7M | Losses, 1978 onward |
| [NFIP Redacted Policies v3](https://www.fema.gov/openfema-data-page/nfip-redacted-policies-v3) | 74.7M | Exposure, premium, and CRS class by year, 2009 onward |
| NFIP Community Status Book v1 | 25k | CRS entry date and current class per community |
