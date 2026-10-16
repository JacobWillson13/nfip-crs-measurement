"""Data quality and reconciliation checks.

Each check is a SQL query returning the number of failing rows (0 = pass).
Results are written to ops.dq_results. Any failing 'error' check fails the run;
'warn' checks are recorded for review but do not block.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import duckdb

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Check:
    name: str
    layer: str
    severity: str  # error | warn
    sql: str


CHECKS: list[Check] = [
    # --- silver: keys and domains ---
    Check("claim_id_not_null", "silver", "error",
          "select count(*) from silver.nfip_claims where claim_id is null"),
    Check("claim_id_unique", "silver", "error",
          "select count(*) from (select claim_id from silver.nfip_claims group by 1 having count(*) > 1)"),
    Check("state_is_two_letters", "silver", "error",
          "select count(*) from silver.nfip_claims where state is null or length(state) <> 2"),
    Check("paid_amounts_non_negative", "silver", "error",
          "select count(*) from silver.nfip_claims where paid_building < 0 or paid_contents < 0 or paid_icc < 0"),
    Check("year_of_loss_in_range", "silver", "error",
          "select count(*) from silver.nfip_claims "
          "where year_of_loss < 1970 or year_of_loss > year(current_date)"),
    Check("crs_class_in_domain", "silver", "error",
          "select count(*) from silver.nfip_claims where crs_class is not null and crs_class not between 1 and 10"),
    Check("year_of_loss_matches_date", "silver", "warn",
          "select count(*) from silver.nfip_claims where year_of_loss <> year(date_of_loss)"),
    Check("community_id_six_chars", "silver", "warn",
          "select count(*) from silver.nfip_claims where community_id is not null and length(community_id) <> 6"),
    Check("missing_community_id", "silver", "warn",
          "select count(*) from silver.nfip_claims where community_id is null"),
    Check("construction_date_sentinel", "silver", "warn",
          "select count(*) from silver.nfip_claims where construction_date_is_sentinel"),

    # --- reconciliation: bronze landing -> silver -> gold ---
    Check("silver_rows_match_landed_rows", "reconciliation", "error", """
        with latest as (
            select landed_rows,
                   row_number() over (partition by dataset, partition_key order by finished_at desc) rn
            from ops.run_log
            where dataset = 'nfip_claims' and status = 'success'
        )
        select abs((select count(*) from silver.nfip_claims)
                   - (select coalesce(sum(landed_rows), 0) from latest where rn = 1))
    """),
    Check("gold_paid_total_matches_silver", "reconciliation", "error", """
        select case when abs(
            (select coalesce(sum(paid_total), 0) from gold.community_year_claims)
          - (select coalesce(sum(paid_total), 0) from silver.nfip_claims
             where community_id is not null and year_of_loss is not null)
        ) > 0.01 then 1 else 0 end
    """),
    Check("gold_claim_count_matches_silver", "reconciliation", "error", """
        select abs(
            (select coalesce(sum(n_claims), 0) from gold.community_year_claims)
          - (select count(*) from silver.nfip_claims
             where community_id is not null and year_of_loss is not null))
    """),

    # --- gold: analysis-readiness ---
    Check("gold_grain_unique", "gold", "error",
          "select count(*) from (select community_id, year_of_loss from gold.community_year_claims "
          "group by 1, 2 having count(*) > 1)"),
    Check("gold_mixed_crs_class_within_year", "gold", "warn",
          "select count(*) from gold.community_year_claims where n_distinct_crs_classes > 1"),
]


def run_checks(con: duckdb.DuckDBPyConnection, checks: list[Check] = CHECKS) -> list[dict]:
    run_at = datetime.now(UTC)
    results = []
    for chk in checks:
        failing = int(con.execute(chk.sql).fetchone()[0])
        passed = failing == 0
        results.append({"check": chk.name, "layer": chk.layer, "severity": chk.severity,
                        "failing_rows": failing, "passed": passed})
        con.execute("insert into ops.dq_results values (?, ?, ?, ?, ?, ?)",
                    [run_at, chk.name, chk.layer, chk.severity, failing, passed])
        level = logging.INFO if passed else (logging.ERROR if chk.severity == "error" else logging.WARNING)
        log.log(level, "%-36s %-6s %s (%d failing)", chk.name, chk.severity,
                "PASS" if passed else "FAIL", failing)
    return results


def has_blocking_failure(results: list[dict]) -> bool:
    return any(r["severity"] == "error" and not r["passed"] for r in results)
