# Databricks notebook source
# MAGIC %md
# MAGIC # 01 Load silver
# MAGIC Loads the silver Parquet files from the landing Volume into Delta tables and reconciles
# MAGIC row counts. The silver layer is built locally (the 75M-row policy step needs more than
# MAGIC Free Edition quotas), exported with `python -m pipeline.cli export`, and uploaded with
# MAGIC `databricks/upload.sh`. Every load attempt is logged to `ops_load_log`.

# COMMAND ----------

import uuid
from datetime import UTC, datetime

from pyspark.sql import functions as F

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "nfip")
catalog, schema = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
prefix = f"{catalog}.{schema}."
landing = f"/Volumes/{catalog}/{schema}/landing"
run_id = str(uuid.uuid4())

SILVER = {  # landing file -> silver table, with its natural key for a uniqueness check
    "nfip_claims.parquet": ("silver_nfip_claims", ["claim_id"]),
    "community_status.parquet": ("silver_community_status", ["community_id"]),
    "policy_exposure_community_year.parquet": ("silver_policy_exposure_community_year", ["community_id", "year"]),
}

spark.sql(f"""create table if not exists {prefix}ops_load_log (
    run_id string, dataset string, source_file string, file_rows bigint, table_rows bigint,
    status string, message string, loaded_at timestamp)""")

# COMMAND ----------

failures = []
for file, (table, keys) in SILVER.items():
    path = f"{landing}/{file}"
    started = datetime.now(UTC)
    try:
        df = spark.read.parquet(path)
        file_rows = df.count()
        dupes = df.groupBy(*keys).count().filter("count > 1").count()
        if dupes:
            raise ValueError(f"{dupes} duplicate keys on {keys}")
        (df.withColumn("_source_file", F.lit(path)).withColumn("_loaded_at", F.lit(started))
           .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(prefix + table))
        table_rows = spark.table(prefix + table).count()
        status = "success" if table_rows == file_rows else "row_count_mismatch"
        message = "" if status == "success" else f"file {file_rows} vs table {table_rows}"
    except Exception as e:  # noqa: BLE001  log the failure, then fail the task below
        file_rows, table_rows, status, message = None, None, "failed", str(e)[:500]
    if status != "success":
        failures.append(f"{table}: {message}")
    spark.createDataFrame(
        [(run_id, table, path, file_rows, table_rows, status, message, started)],
        "run_id string, dataset string, source_file string, file_rows bigint, table_rows bigint, "
        "status string, message string, loaded_at timestamp",
    ).write.mode("append").saveAsTable(prefix + "ops_load_log")
    print(f"{table:42s} {status:20s} {table_rows}")

if failures:
    raise RuntimeError("Silver load failed: " + "; ".join(failures))
