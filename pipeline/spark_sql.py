"""Run the pipeline's gold SQL and quality checks on Spark (Databricks).

The SQL files in pipeline/transform/sql and the checks in pipeline/quality/checks.py are
the single source of truth. They are written for DuckDB, and the dialect features they
use (FILTER clauses, any_value, :: casts, date + interval, ||) also run on Spark 4 and
Databricks SQL. The only translation needed is naming: DuckDB uses schemas
(silver.nfip_claims), while the Databricks port keeps every table in one Unity Catalog
schema with a layer prefix (workspace.nfip.silver_nfip_claims).

tests/test_spark_parity.py runs the translated SQL on local PySpark and checks the gold
tables and check results match DuckDB row for row.
"""

from __future__ import annotations

import re
from pathlib import Path

from pipeline.quality.checks import CHECKS, Check

SQL_DIR = Path(__file__).parent / "transform" / "sql"
# Silver tables are built locally (the 75M-row policy step needs more than Free Edition
# quotas) and land on Databricks as files; gold is rebuilt there from the same SQL.
GOLD_SQL = ["20_gold_community_year_claims.sql", "30_gold_community_year_panel.sql"]
SILVER_TABLES = ["nfip_claims", "community_status", "policy_exposure_community_year"]

_REF = re.compile(r"\b(silver|gold|ops)\.(\w+)\b")
_COMMENT = re.compile(r"--[^\n]*")
_CREATE_SCHEMA = re.compile(r"create\s+schema\s+if\s+not\s+exists\s+\w+\s*;", re.IGNORECASE)


def table_name(layer: str, table: str, prefix: str = "") -> str:
    """silver, nfip_claims -> {prefix}silver_nfip_claims"""
    return f"{prefix}{layer}_{table}"


def to_spark(sql: str, prefix: str = "", temp_views: bool = False) -> list[str]:
    """Translate one DuckDB SQL script into Spark SQL statements.

    prefix: catalog and schema for every table, e.g. "workspace.nfip.".
    temp_views: build temp views instead of tables (local tests without Delta).
    """
    sql = _COMMENT.sub("", sql)
    sql = _CREATE_SCHEMA.sub("", sql)
    sql = _REF.sub(lambda m: table_name(m.group(1), m.group(2), prefix), sql)
    if temp_views:
        sql = re.sub(r"create\s+or\s+replace\s+table", "create or replace temp view", sql, flags=re.IGNORECASE)
    return [s.strip() for s in sql.split(";") if s.strip()]


def gold_statements(prefix: str = "", temp_views: bool = False) -> list[str]:
    out: list[str] = []
    for name in GOLD_SQL:
        out += to_spark((SQL_DIR / name).read_text(), prefix, temp_views)
    return out


def tables_used(sql: str) -> set[str]:
    return {table_name(layer, t) for layer, t in _REF.findall(sql)}


def spark_checks(available: set[str], prefix: str = "") -> list[tuple[Check, str]]:
    """Quality checks whose tables exist on the platform, translated to Spark SQL.

    available: unprefixed table names, e.g. {"silver_nfip_claims", "gold_community_year_panel"}.
    Checks that need tables not ported (the local load log, ops.run_log) are skipped.
    """
    out = []
    for chk in CHECKS:
        if tables_used(chk.sql) <= available:
            (stmt,) = to_spark(chk.sql, prefix)
            out.append((chk, stmt))
    return out
