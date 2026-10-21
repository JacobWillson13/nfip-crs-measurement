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

The AI/BI dashboard is defined in code and deploys with the bundle, so it is versioned and reviewed like everything else.

- [dashboard_queries.sql](dashboard_queries.sql) holds the SQL, one dataset per `-- dataset:` block.
- [build_dashboard.py](build_dashboard.py) adds the pages and widgets and writes [dashboards/nfip_crs.lvdash.json](dashboards/nfip_crs.lvdash.json).
- [tests/test_dashboard.py](../tests/test_dashboard.py) checks that the committed JSON matches the generator, that every widget points at a real dataset and field, and that widgets don't overlap.

Pages:

| Page | What it shows |
| --- | --- |
| Overview | The answer in one paragraph (numbers read from `analysis/results/`); loss cost per class step with its placebo-corrected interval, the expected pricing gap, and the value of better data; scheduled vs best-supported discount by class; a state map of CRS participation; paid losses and claim frequency by year, filterable by state, CRS class, and years |
| Pricing decision | Each class's pooled loss cost with its 90% interval against the break-even discount, the probability each discount is too generous, and the decision table (net and absolute gap, value of information) |
| Measurement designs | Detectable effect for the observational DiD (with the real number of entrants and a realistic effect marked), the placebo check that dropped the original design, and power for the proposed randomized trial |
| Pipeline health | Days since the last run, check counts, quality checks with failures first and failing rows as a share of the table, check outcomes across runs, and silver load reconciliation |

The state and CRS class filters are dataset parameters rather than field filters: the chart shows rates, which have to be recomputed in SQL after filtering. The interval chart on the Pricing decision page is a custom Vega-Lite widget; if a workspace does not support custom visualizations, `python databricks/build_dashboard.py --standard` swaps it for a standard bar chart. [tests/test_dashboard_sql.py](../tests/test_dashboard_sql.py) runs every dataset query on local Spark with its parameters bound.

To change it, edit the SQL or the generator, then:

```
python databricks/build_dashboard.py
databricks bundle deploy
```

Edits made in the UI are overwritten on the next deploy. Free Edition dashboards cannot be shared publicly, so screenshots of each page go in `databricks/screenshots/` for the README.

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
