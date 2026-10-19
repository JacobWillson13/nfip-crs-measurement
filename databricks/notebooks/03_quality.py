# Databricks notebook source
# MAGIC %md
# MAGIC # 03 Quality checks
# MAGIC Runs the pipeline's quality checks (`pipeline/quality/checks.py`) against the Delta
# MAGIC tables. Each check returns a count of failing rows; results append to `ops_dq_results`.
# MAGIC A failing error-level check fails the task, which stops the analysis task from running
# MAGIC on bad data. Checks that need the local load log are skipped here.

# COMMAND ----------

import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.abspath("../.."))

from pipeline.spark_sql import spark_checks  # noqa: E402

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "nfip")
catalog, schema = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
prefix = f"{catalog}.{schema}."

available = {r.tableName for r in spark.sql(f"show tables in {catalog}.{schema}").collect()}
run_at = datetime.now(UTC)

# COMMAND ----------

rows = []
for chk, stmt in spark_checks(available, prefix):
    failing = int(spark.sql(stmt).collect()[0][0])
    rows.append((run_at, chk.name, chk.layer, chk.severity, failing, failing == 0))
    print(f"{chk.name:40s} {chk.severity:6s} {'PASS' if failing == 0 else 'FAIL'} ({failing} failing)")

schema_ddl = "run_at timestamp, check_name string, layer string, severity string, failing_rows bigint, passed boolean"
spark.createDataFrame(rows, schema_ddl).write.mode("append").saveAsTable(prefix + "ops_dq_results")

blocking = [r[1] for r in rows if r[3] == "error" and not r[5]]
if blocking:
    raise RuntimeError(f"Blocking quality failures: {', '.join(blocking)}")
print(f"{len(rows)} checks run, {sum(r[5] for r in rows)} passed, no blocking failures")
