"""The Databricks port runs the same gold SQL and quality checks as DuckDB, with the same results.

Builds the warehouse from the synthetic fixtures in DuckDB, lands its silver tables as
Parquet (as the Databricks job does), rebuilds gold on local PySpark from the translated
SQL, and compares every gold column and every quality check. Skipped without pyspark.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

# Skip before importing anything optional: the default CI job installs only [dev].
pytest.importorskip("pyspark")
np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

from pipeline.quality.checks import run_checks  # noqa: E402
from pipeline.spark_sql import SILVER_TABLES, gold_statements, spark_checks, table_name, to_spark  # noqa: E402
from pipeline.transform.build import build  # noqa: E402

GOLD = {"community_year_claims": ["community_id", "year_of_loss"],
        "community_year_panel": ["community_id", "year"]}


@pytest.fixture(scope="module")
def spark():
    from pyspark.sql import SparkSession
    s = (SparkSession.builder.master("local[1]").appName("parity")
         .config("spark.ui.enabled", "false").config("spark.sql.session.timeZone", "UTC").getOrCreate())
    yield s
    s.stop()


@pytest.fixture
def both(con, loaded_lake, spark, tmp_path):
    status = build(con, loaded_lake, year_min=2009, year_max=2026)
    assert all(v == "built" for v in status.values()), status
    for t in SILVER_TABLES:
        path = (tmp_path / f"{t}.parquet").as_posix()
        con.execute(f"copy silver.{t} to '{path}' (format parquet)")
        spark.read.parquet(path).createOrReplaceTempView(table_name("silver", t))
    for stmt in gold_statements(temp_views=True):
        spark.sql(stmt)
    return con, spark


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in df.columns:
        s = df[c]
        # Spark types literals like 1.0 as DECIMAL where DuckDB uses DOUBLE (e.g. the avg in
        # share_claims_in_sfha); the values are the same, so compare them as numbers.
        if s.dtype == object and s.notna().any() and s.dropna().map(lambda v: isinstance(v, Decimal)).all():
            df[c] = s.astype(float)
        elif s.dtype == object and s.dropna().map(lambda v: hasattr(v, "year") and hasattr(v, "month")).all() \
                and s.notna().any():
            df[c] = pd.to_datetime(s)
        elif str(s.dtype).startswith(("datetime", "date")):
            df[c] = pd.to_datetime(s)
    return df


@pytest.mark.parametrize("table", list(GOLD))
def test_gold_tables_match(both, table):
    con, spark = both
    keys = GOLD[table]
    duck = _normalize(con.execute(f"select * from gold.{table}").df().sort_values(keys).reset_index(drop=True))
    sprk = _normalize(spark.table(table_name("gold", table)).toPandas().sort_values(keys).reset_index(drop=True))
    assert list(duck.columns) == list(sprk.columns)
    assert len(duck) == len(sprk) > 0
    for c in duck.columns:
        a, b = duck[c], sprk[c]
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b) and not pd.api.types.is_bool_dtype(a):
            assert np.allclose(a.astype(float), b.astype(float), equal_nan=True, rtol=1e-9), c
        else:
            assert a.astype(str).tolist() == b.astype(str).tolist(), c


def test_quality_checks_match(both):
    con, spark = both
    duck = {r["check"]: r["failing_rows"] for r in run_checks(con)}
    available = {table_name("silver", t) for t in SILVER_TABLES} | {table_name("gold", t) for t in GOLD}
    ported = spark_checks(available)
    assert len(ported) >= 15
    for chk, stmt in ported:
        assert int(spark.sql(stmt).collect()[0][0]) == duck[chk.name], chk.name


def test_translation_names_tables_and_drops_schema_ddl():
    (stmt,) = to_spark("-- note; with a semicolon\ncreate schema if not exists gold;\n"
                       "create or replace table gold.x as select * from silver.y join ops.z using (k);",
                       prefix="workspace.nfip.")
    assert stmt == ("create or replace table workspace.nfip.gold_x as select * from workspace.nfip.silver_y "
                    "join workspace.nfip.ops_z using (k)")
