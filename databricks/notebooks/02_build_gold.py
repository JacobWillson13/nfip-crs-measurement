# Databricks notebook source
# MAGIC %md
# MAGIC # 02 Build gold
# MAGIC Runs the same gold SQL files the local DuckDB pipeline runs
# MAGIC (`pipeline/transform/sql/20_*` and `30_*`), translated by `pipeline/spark_sql.py`.
# MAGIC `tests/test_spark_parity.py` checks the Spark and DuckDB outputs match row for row.

# COMMAND ----------

import os
import re
import sys

sys.path.insert(0, os.path.abspath("../.."))  # bundle root, synced with the notebooks

from pipeline.spark_sql import gold_statements  # noqa: E402

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "nfip")
prefix = f"{dbutils.widgets.get('catalog')}.{dbutils.widgets.get('schema')}."

# COMMAND ----------

for stmt in gold_statements(prefix):
    target = re.match(r"create or replace table (\S+)", stmt, re.IGNORECASE).group(1)
    spark.sql(stmt)
    print(f"built {target}: {spark.table(target).count():,} rows")

# COMMAND ----------

# Liquid clustering on the keys the dashboard and analysis filter by. Optional: a
# failure here (for example, an older runtime) should not fail the pipeline.
try:
    spark.sql(f"alter table {prefix}gold_community_year_panel cluster by (state, year)")
    spark.sql(f"optimize {prefix}gold_community_year_panel")
except Exception as e:  # noqa: BLE001
    print(f"clustering skipped: {e}")
spark.sql(f"comment on table {prefix}gold_community_year_panel is "
          "'One row per NFIP community and calendar year with earned exposure, 2009 onward. "
          "CRS class rebuilt from Status Book dates; see crs_status_basis.'")
