# Databricks port

The gold, quality, and analysis layers run as a scheduled Databricks job, deployed from this repo as a Databricks Asset Bundle ([databricks.yml](../databricks.yml)). It runs on serverless compute and works on the free tier (Free Edition).

```
local (DuckDB)                                  Databricks (serverless, Unity Catalog: workspace.nfip)
  bronze -> silver                                landing Volume  /Volumes/workspace/nfip/landing
  (75M policy rows: too big for free quotas)          | 01_load_silver   Parquet -> Delta, row counts reconciled, ops_load_log
  python -m pipeline.cli export  --upload.sh-->       | 02_build_gold    same SQL files as local, translated (pipeline/spark_sql.py)
                                                      | 03_quality       same checks as local -> ops_dq_results; error-level failure stops the job
                                                      | 04_analysis      same analysis code -> results_* tables, MLflow run
                                                      v
                                                  AI/BI dashboard on gold, results, and ops tables
```

**One source of truth.** The notebooks import `pipeline/` and `analysis/` from the bundle, which syncs this repo to the workspace. Gold SQL and quality checks are the same files the local pipeline runs; `pipeline/spark_sql.py` only renames tables (`silver.nfip_claims` -> `workspace.nfip.silver_nfip_claims`). [tests/test_spark_parity.py](../tests/test_spark_parity.py) runs the translated SQL on local PySpark and checks that every gold column and every quality check matches DuckDB.

**What stays local, and why.** The silver build turns 75 million policy transactions into community-year exposure. That fits a laptop (31 seconds in DuckDB) but not Free Edition's daily serverless quota, so silver is built locally and landed as files. On a paid workspace the same SQL would run there too. The placebo simulations (`run_design.py`, `run_experiment.py`, about 40 minutes) also stay local; the job loads their committed results for the dashboard.

## Run it

Prerequisites: a workspace (Free Edition is enough), the Databricks CLI v0.250 or newer, and a personal access token (Settings -> Developer -> Access tokens; OAuth login does not work on Free Edition).

```
databricks configure                     # host without query parameters, e.g. https://dbc-xxxx.cloud.databricks.com
python -m pipeline.cli export            # writes the three silver Parquet files to data/export/
bash databricks/upload.sh                # creates workspace.nfip and the landing Volume, uploads the files
databricks bundle validate
databricks bundle deploy
databricks bundle run nfip_pipeline      # about 5 to 10 minutes on serverless
```

`deploy` uses development mode: the job is named `[dev <user>] nfip-crs-pipeline` and its monthly schedule stays paused. The run creates:

| Table | What it holds |
| --- | --- |
| `silver_nfip_claims`, `silver_community_status`, `silver_policy_exposure_community_year` | Landed silver, with `_source_file` and `_loaded_at` |
| `gold_community_year_claims`, `gold_community_year_panel` | Gold, rebuilt from the shared SQL; the panel is liquid-clustered by state and year |
| `ops_load_log`, `ops_dq_results` | Load attempts with file vs table row counts; quality check results per run |
| `results_pricing_adequacy`, `results_entry_did`, `results_pricing_decision`, `results_pooled_summary` | Analysis outputs |
| `results_c_power_design`, `results_c_class_placebo`, `results_e_experiment_power` | Placebo simulation outputs (computed locally) |

Check the run against the local pipeline (gold row counts, load reconciliation, quality checks, and the headline estimates):

```
python databricks/verify.py
```

The analysis task also logs an MLflow run to the experiment `/Users/<you>/nfip-crs-measurement`, with the headline estimates as metrics and the result tables and figure as artifacts.

## Dashboard

Build it once in the UI, then save its definition into the bundle so it deploys with everything else.

1. **Dashboards -> Create dashboard -> Data -> Create from SQL**, one dataset per query in [dashboard_queries.sql](dashboard_queries.sql).
2. Pages:
   - **Losses:** line charts of `claims_per_1k_policy_years` and `paid_musd` by year from `losses_by_year`, with a state filter.
   - **Pricing decision:** `class_decision` as a bar chart of `pooled_loss_cost_effect_pct` next to `scheduled_discount_pct` by class, plus a table with `p_too_generous_pct`, `expected_gap_musd`, `evpi_musd`, `evsi_half_variance_musd`.
   - **Designs:** `entry_design` as a line chart of `mde_pct` by `n_entrants`, colored by design; `experiment_power` as a table.
   - **Pipeline health:** counters of passed and failed checks and a table of failing checks from `pipeline_health`; the latest loads from `load_log`.
3. Publish it, then save the definition into the repo:
   ```
   databricks bundle generate dashboard --existing-id <dashboard id from the URL>
   ```
   This writes a `resources/*.yml` and a `.lvdash.json`. Add `include: [resources/*.yml]` to `databricks.yml` and commit both.
4. Screenshots of each page go in `databricks/screenshots/` for the README, since Free Edition dashboards cannot be shared publicly.

## Mapping to Microsoft Fabric

The design ports one to one to a Fabric lakehouse.

| Here | Fabric |
| --- | --- |
| Unity Catalog schema `workspace.nfip` | Lakehouse |
| Landing Volume | Lakehouse Files (OneLake) |
| Delta tables `silver_*`, `gold_*` | Lakehouse Delta tables |
| Notebook tasks in a job | Fabric notebooks in a Data Factory pipeline |
| `ops_dq_results` and a failing task | Same table; pipeline failure activity and alert |
| MLflow experiment | Fabric ML experiment (also MLflow) |
| AI/BI dashboard | Power BI report on the semantic model (Direct Lake) |
| Asset Bundle | Fabric Git integration and deployment pipelines |
