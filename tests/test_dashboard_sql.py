"""Every dashboard dataset query runs on Spark and returns the columns its widgets read.

Builds silver and gold from the synthetic fixtures (as in test_spark_parity.py), loads the
committed analysis results as the results_* tables, runs the quality checks to fill
ops_dq_results, then executes each dataset in the generated dashboard with its parameters
bound, both empty ("all") and set. Skipped without pyspark.
"""

from __future__ import annotations

import importlib.util
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("pyspark")
pd = pytest.importorskip("pandas")

from pipeline.spark_sql import SILVER_TABLES, gold_statements, spark_checks, table_name  # noqa: E402
from pipeline.transform.build import build  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_dashboard", ROOT / "databricks" / "build_dashboard.py")
bd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bd)

RESULTS = {"pricing_adequacy": "a_pricing_adequacy", "entry_did": "b_entry_did",
           "pricing_decision": "d_pricing_decision", "pooled_summary": "d_pooled_summary",
           "c_power_design": "c_power_design", "c_class_placebo": "c_class_placebo",
           "e_experiment_power": "e_experiment_power"}
RENAME = {"Std. Error": "std_error", "Pr(>|t|)": "p_value"}  # as in databricks/notebooks/04_analysis.py


@pytest.fixture(scope="module")
def spark():
    from pyspark.sql import SparkSession
    s = (SparkSession.builder.master("local[1]").appName("dashboard-sql")
         .config("spark.ui.enabled", "false").config("spark.sql.session.timeZone", "UTC").getOrCreate())
    yield s
    s.stop()


@pytest.fixture
def warehouse(con, loaded_lake, spark, tmp_path):
    status = build(con, loaded_lake, year_min=2009, year_max=2026)
    assert all(v == "built" for v in status.values()), status
    for t in SILVER_TABLES:
        path = (tmp_path / f"{t}.parquet").as_posix()
        con.execute(f"copy silver.{t} to '{path}' (format parquet)")
        spark.read.parquet(path).createOrReplaceTempView(table_name("silver", t))
    for stmt in gold_statements(temp_views=True):
        spark.sql(stmt)
    for table, csv in RESULTS.items():
        df = pd.read_csv(ROOT / "analysis" / "results" / f"{csv}.csv", dtype={"size": str})
        df = df.rename(columns=lambda c: re.sub(r"[^0-9a-zA-Z_]+", "_", RENAME.get(c, c)).strip("_").lower())
        spark.createDataFrame(df).createOrReplaceTempView(f"results_{table}")
    # Two job runs of the quality checks, as 03_quality.py appends them.
    available = {t.name for t in spark.catalog.listTables()}
    checks = spark_checks(available)
    rows = []
    for i, run_at in enumerate([datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 18, tzinfo=UTC)]):
        for chk, stmt in checks:
            failing = int(spark.sql(stmt).collect()[0][0]) + (1 if i == 0 and chk.severity == "warn" else 0)
            rows.append((run_at, chk.name, chk.layer, chk.severity, failing, failing == 0))
    spark.createDataFrame(rows, "run_at timestamp, check_name string, layer string, severity string, "
                                "failing_rows bigint, passed boolean").createOrReplaceTempView("ops_dq_results")
    loaded = datetime(2026, 10, 18, tzinfo=UTC)
    spark.createDataFrame(
        [("r1", t, f"/Volumes/{t}.parquet", 10, 10, "success", "", loaded - timedelta(days=d))
         for t in ("silver_nfip_claims", "silver_community_status") for d in (0, 5)],
        "run_id string, dataset string, source_file string, file_rows bigint, table_rows bigint, "
        "status string, message string, loaded_at timestamp").createOrReplaceTempView("ops_load_log")
    return spark


def _run(spark, ds: dict, values: list[str] | None):
    """Bind each MULTI parameter as the ARRAY<STRING> the dashboard sends (empty = nothing selected).
    Other declared parameters are left at an empty selection."""
    sql = "".join(ds["queryLines"]).replace(bd.TABLE_PREFIX, "")
    for i, p in enumerate(ds.get("parameters", [])):
        chosen = values if i == 0 and values else []
        literal = "cast(array(" + ", ".join(f"'{v}'" for v in chosen) + ") as array<string>)"
        sql = re.sub(rf":{p['keyword']}\b", literal, sql)
    assert not re.search(r"(?<![:\w]):[a-z_]+\b", sql), "undeclared parameter"
    return spark.sql(sql).toPandas()


def _widget_columns(d: dict) -> dict[str, set[str]]:
    """Dataset columns each dataset must return: every backticked column in widget field expressions."""
    need: dict[str, set[str]] = {}
    for p in d["pages"]:
        for item in p["layout"]:
            for q in item["widget"].get("queries", []):
                cols = need.setdefault(q["query"]["datasetName"], set())
                for f in q["query"].get("fields", []):
                    cols |= set(re.findall(r"`([^`]+)`", f["expression"]))
                cols |= {e["fieldName"] for e in item["widget"].get("spec", {}).get("encodings", {}).get("fields", [])
                         if item["widget"]["spec"]["widgetType"] == "range-slider"}
    return need


MAY_BE_EMPTY = {"state_map"}   # fixture years end in 2017; checked separately below


def test_every_dataset_runs_and_has_widget_columns(warehouse):
    d = bd.build()
    need = _widget_columns(d)
    for ds in d["datasets"]:
        out = _run(warehouse, ds, None)
        missing = need.get(ds["name"], set()) - set(out.columns)
        assert not missing, (ds["name"], missing)
        assert len(out) or ds["name"] in MAY_BE_EMPTY, ds["name"]


def test_state_map_names_and_shares(warehouse):
    ds = next(ds for ds in bd.build()["datasets"] if ds["name"] == "state_map")
    ds = {**ds, "queryLines": [ln.replace("between 2022 and 2024", "between 2009 and 2017") for ln in ds["queryLines"]]}
    out = _run(warehouse, ds, None)
    assert set(out.state_name) == {"Texas", "Virginia"}
    assert out.crs_share_pct.between(0, 100).all()


def test_parameters_filter_and_empty_means_all(warehouse):
    d = {ds["name"]: ds for ds in bd.build()["datasets"]}
    ds = d["losses_by_year"]
    everything = _run(warehouse, ds, None)
    state = _run(warehouse, d["state_options"], None).state.iloc[0]
    one = _run(warehouse, ds, [state])
    assert 0 < one.claims.sum() <= everything.claims.sum()
    assert _run(warehouse, ds, ["ZZ"]).empty


def test_pipeline_health_shares_and_order(warehouse):
    d = {ds["name"]: ds for ds in bd.build()["datasets"]}
    h = _run(warehouse, d["pipeline_health"], None)
    assert h.runs_recorded.iloc[0] == 2
    order = h.status.map({"Blocking": 0, "Warning": 1, "Passed": 2}).tolist()
    assert order == sorted(order), "failures first"
    row_level = {c for c, _ in re.findall(r"\('(\w+)', '(\w+)'\)", bd.check_table_sql()[0])}
    assert h[h.check_name.isin(row_level)].failing_share_pct.notna().all()
    assert h[~h.check_name.isin(row_level)].failing_share_pct.isna().all()
    hist = _run(warehouse, d["dq_history"], None)
    assert hist.run.nunique() == 2
