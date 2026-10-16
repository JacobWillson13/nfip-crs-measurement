"""Data quality and reconciliation checks.

Each check is a SQL query returning the number of failing rows (0 = pass).
Results are written to ops.dq_results. Any failing 'error' check fails the run;
'warn' checks are recorded for review but do not block.
"""

from __future__ import annotations

import logging
import re
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

    # --- status book ---
    Check("status_book_community_unique", "silver", "error",
          "select count(*) from (select community_id from silver.community_status group by 1 having count(*) > 1)"),
    Check("status_book_crs_class_in_domain", "silver", "error",
          "select count(*) from silver.community_status "
          "where crs_class_current is not null and crs_class_current not between 1 and 10"),

    # --- exposure ---
    Check("exposure_grain_unique", "silver", "error",
          "select count(*) from (select community_id, year from silver.policy_exposure_community_year "
          "group by 1, 2 having count(*) > 1)"),
    Check("exposure_non_negative", "silver", "error",
          "select count(*) from silver.policy_exposure_community_year "
          "where earned_policy_years < 0 or earned_premium < 0 or earned_coverage < 0"),
    Check("exposure_national_swing_over_20pct", "silver", "warn", """
        with y as (select year, sum(earned_policy_years) epy from silver.policy_exposure_community_year group by 1),
             d as (select year, epy / lag(epy) over (order by year) - 1 as chg from y)
        select count(*) from d
        where year between 2011 and year(current_date) - 1 and abs(chg) > 0.20
    """),

    # --- analysis panel ---
    Check("panel_grain_unique", "gold", "error",
          "select count(*) from (select community_id, year from gold.community_year_panel "
          "group by 1, 2 having count(*) > 1)"),
    Check("panel_crs_class_in_domain", "gold", "error",
          "select count(*) from gold.community_year_panel where crs_class not between 1 and 10"),
    Check("panel_join_no_fanout", "reconciliation", "error", """
        select case when abs(
            (select coalesce(sum(paid_total), 0) from gold.community_year_panel)
          - (select coalesce(sum(c.paid_total), 0)
             from silver.nfip_claims c
             join (select distinct community_id, year from gold.community_year_panel) p
               on p.community_id = c.community_id and p.year = c.year_of_loss)
        ) > 0.01 then 1 else 0 end
    """),
    # Claims whose community-year has no policy exposure: ID mismatches or exposure gaps.
    Check("paid_claims_without_exposure", "reconciliation", "warn", """
        select count(*) from silver.nfip_claims c
        where c.paid_total > 0
          and c.year_of_loss between 2010 and year(current_date)
          and not exists (select 1 from gold.community_year_panel p
                          where p.community_id = c.community_id and p.year = c.year_of_loss)
    """),
]

_TABLE_REF = re.compile(r"\b(silver|gold|ops)\.(\w+)")


def _existing_tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute("select table_schema || '.' || table_name from information_schema.tables").fetchall()
    return {r[0] for r in rows}


def run_checks(con: duckdb.DuckDBPyConnection, checks: list[Check] = CHECKS) -> list[dict]:
    """Run every check whose tables exist; checks on tables not built yet are skipped."""
    run_at = datetime.now(UTC)
    existing = _existing_tables(con)
    results = []
    for chk in checks:
        needed = {f"{s}.{t}" for s, t in _TABLE_REF.findall(chk.sql)}
        if not needed <= existing:
            log.info("%-36s skipped (tables not built: %s)", chk.name, ", ".join(sorted(needed - existing)))
            continue
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
