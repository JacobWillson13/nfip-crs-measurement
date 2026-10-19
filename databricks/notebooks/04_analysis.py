# Databricks notebook source
# MAGIC %md
# MAGIC # 04 Analysis
# MAGIC Runs the measurement analyses on the gold Delta panel with the same code as the local
# MAGIC run (`analysis/run_analysis.py`, `analysis/run_decision.py`), logs the run to MLflow,
# MAGIC and writes result tables for the dashboard. The placebo simulations
# MAGIC (`run_design.py`, `run_experiment.py`) take about 40 minutes and stay local; their
# MAGIC committed outputs are loaded here so the dashboard can show them.

# COMMAND ----------

# MAGIC %pip install --quiet "pyfixest>=0.30" "scipy>=1.11" "matplotlib>=3.8"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os
import re
import sys
import tempfile
from pathlib import Path

import mlflow
import pandas as pd

ROOT = os.path.abspath("../..")
sys.path.insert(0, os.path.join(ROOT, "analysis"))

import run_decision  # noqa: E402
from run_analysis import YEARS, analysis_a, analysis_b, prepare  # noqa: E402

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "nfip")
catalog, schema = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
prefix = f"{catalog}.{schema}."

panel = spark.table(prefix + "gold_community_year_panel").toPandas()
df = prepare(panel)
print(f"{len(panel):,} panel rows, {len(df):,} in the {YEARS[0]}-{YEARS[1]} analysis sample")

# COMMAND ----------

user = spark.sql("select current_user()").first()[0]
mlflow.set_experiment(f"/Users/{user}/nfip-crs-measurement")

with mlflow.start_run(run_name="crs_pricing_and_entry") as run:
    mlflow.log_params({"years": f"{YEARS[0]}-{YEARS[1]}", "panel_rows": len(panel), "sample_rows": len(df),
                       "estimator": "PPML, county-by-year FE, county-clustered SE"})
    a = analysis_a(df)
    b, sizes = analysis_b(df)
    d, pooled = run_decision.analysis_d(df, run_decision.placebo_critical_value())

    def metric(frame, model, coef):
        return float(frame[(frame.model == model) & (frame.Coefficient == coef)].effect_pct.iloc[0])

    mlflow.log_metrics({
        "a_claims_per_step_pct": metric(a, "A_claim_frequency_per_class_step", "class_steps"),
        "a_loss_cost_per_step_pct": metric(a, "A_loss_cost_per_class_step", "class_steps"),
        "b_claims_post_entry_pct": metric(b, "B_claim_frequency_post_entry", "post"),
        "b_loss_cost_post_entry_pct": metric(b, "B_loss_cost_post_entry", "post"),
        "d_total_expected_abs_gap_musd": float(pooled.total_expected_abs_gap_usd.iloc[0]) / 1e6,
        "d_total_evpi_musd": float(pooled.total_evpi_usd.iloc[0]) / 1e6,
        "d_total_evsi_half_variance_musd": float(pooled.total_evsi_half_variance_usd.iloc[0]) / 1e6,
    })
    with tempfile.TemporaryDirectory() as tmp:
        for name, frame in {"a_pricing_adequacy": a, "b_entry_did": b, "b_sample": sizes,
                            "d_pricing_decision": d, "d_pooled_summary": pooled}.items():
            frame.to_csv(f"{tmp}/{name}.csv", index=False)
        run_decision.FIG = Path(tmp)
        run_decision.figure(d)
        mlflow.log_artifacts(tmp, artifact_path="results")
    print(f"MLflow run {run.info.run_id}")

# COMMAND ----------

RENAME = {"Std. Error": "std_error", "Pr(>|t|)": "p_value"}


def save(frame: pd.DataFrame, name: str) -> None:
    """Write a result table to Delta, with column names Delta accepts."""
    clean = frame.rename(columns=lambda c: re.sub(r"[^0-9a-zA-Z_]+", "_", RENAME.get(c, c)).strip("_").lower())
    spark.createDataFrame(clean).write.mode("overwrite").option("overwriteSchema", "true") \
        .saveAsTable(prefix + "results_" + name)
    print(f"results_{name}: {len(clean)} rows")


save(a, "pricing_adequacy")
save(b, "entry_did")
save(d, "pricing_decision")
save(pooled, "pooled_summary")
# Placebo simulation outputs, computed locally and committed with the repo.
for name in ("c_power_design", "c_class_placebo", "e_experiment_power"):
    path = os.path.join(ROOT, "analysis", "results", f"{name}.csv")
    if os.path.exists(path):
        save(pd.read_csv(path, dtype={"size": str}), name)
