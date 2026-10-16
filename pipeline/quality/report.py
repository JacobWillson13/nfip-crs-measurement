"""Feasibility gates 1 and 2, computed from the warehouse.

Gate 1 (exposure is real): national earned policy-years by year should be close to
FEMA's published policies-in-force totals (roughly 5 million in the 2010s) and should
not jump around year to year. Claim rates should spike in known catastrophe years.

Gate 2 (enough variation to identify anything):
  * CRS entries by year, from the Status Book
  * community-years where the CRS class changed, from policies
  * counties with both CRS and non-CRS communities in the same year (needed for
    county-by-year fixed effects)

Writes each table to CSV next to the export so the numbers can be shared.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

QUERIES: dict[str, tuple[str, str]] = {
    "gate1_national_exposure_by_year": ("gold.community_year_panel", """
        select year,
               round(sum(earned_policy_years) / 1e6, 3)                  as earned_policy_years_millions,
               round(sum(earned_premium) / 1e9, 3)                       as earned_premium_billions,
               sum(n_claims)                                              as claims,
               round(1000.0 * sum(n_claims) / sum(earned_policy_years), 2) as claims_per_1k_policy_years,
               round(sum(paid_total) / 1e9, 3)                           as paid_billions
        from gold.community_year_panel group by year order by year
    """),
    "gate2_crs_entries_by_year": ("silver.community_status", """
        select crs_entry_year, count(*) as communities_entering
        from silver.community_status
        where crs_entry_year is not null
        group by 1 order by 1
    """),
    "gate2_policy_stamp_variation": ("gold.community_year_panel", """
        -- If policies carried historical CRS classes, many communities would change class.
        select count(*)                                 as communities,
               count(*) filter (where n_stamped_classes > 1) as communities_with_stamp_change
        from (select community_id, count(distinct crs_class_policy_stamp) as n_stamped_classes
              from gold.community_year_panel group by 1)
    """),
    "gate2_crs_history_coverage": ("gold.community_year_panel", """
        select year,
               count(*) filter (where crs_status_basis = 'not_in_crs')            as not_in_crs,
               count(*) filter (where crs_status_basis = 'current_class')         as crs_class_known,
               count(*) filter (where crs_status_basis = 'in_crs_class_unknown')  as crs_class_unknown,
               count(*) filter (where crs_status_basis = 'participation_unknown') as participation_unknown
        from gold.community_year_panel group by year order by year
    """),
    "gate2_claims_crs_variation": ("gold.community_year_claims", """
        -- Do claims carry the class at time of loss? If so, class history can be
        -- reconstructed for flood years from claims.
        select count(*)                                       as communities_with_crs_on_claims,
               count(*) filter (where n_classes > 1)          as communities_with_class_change
        from (select community_id, count(distinct crs_class_modal) as n_classes
              from gold.community_year_claims
              where crs_class_modal is not null and year_of_loss >= 1995
              group by 1)
    """),
    "gate2_mixed_county_years": ("gold.community_year_panel", """
        with c as (
            select county_fips, year,
                   count(distinct community_id)                         as communities,
                   count(distinct community_id) filter (where in_crs)   as crs_communities
            from gold.community_year_panel
            where county_fips is not null and year between 2010 and 2021
            group by 1, 2
        )
        select year,
               count(*)                                                          as county_years,
               count(*) filter (where crs_communities > 0
                                  and crs_communities < communities)             as mixed_county_years
        from c group by year order by year
    """),
}


def feasibility_report(con: duckdb.DuckDBPyConnection, out_dir: Path) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = {r[0] for r in con.execute(
        "select table_schema || '.' || table_name from information_schema.tables").fetchall()}
    sections = []
    for name, (table, sql) in QUERIES.items():
        if table not in existing:
            sections.append(f"== {name} ==\n(skipped: {table} not built yet)")
            continue
        rel = con.sql(sql)
        rel.write_csv((out_dir / f"{name}.csv").as_posix())
        sections.append(f"== {name} ==\n{_format(rel.columns, rel.fetchall())}")
    return "\n\n".join(sections)


def _format(columns: list[str], rows: list[tuple]) -> str:
    cells = [columns] + [["" if v is None else str(v) for v in r] for r in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(columns))]
    lines = ["  ".join(v.rjust(w) for v, w in zip(row, widths, strict=True)) for row in cells]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)
