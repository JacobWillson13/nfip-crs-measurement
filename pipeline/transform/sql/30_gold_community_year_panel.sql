-- requires: silver_policy_exposure, silver_nfip_claims, silver_community_status
-- Gold: the analysis panel. One row per community and calendar year with any earned
-- exposure, 2009 onward. Exposure is the spine, so years with zero claims are present
-- (as zeros), which the claims-only table cannot provide.
--
-- CRS history. FEMA's redacted policies carry each community's CURRENT CRS class on
-- every historical record (found on the first national load: 91% of community-years
-- before a community joined CRS were stamped as CRS). So class by year is rebuilt from
-- the Status Book instead:
--   * before the CRS entry year            -> not in CRS, class 10 (known)
--   * from the current class's effective year -> current class (known)
--   * between entry and that effective year -> in CRS, class unknown (null)
--   * entered CRS but no current class      -> participation unknown after entry (null)
-- A date counts toward the calendar year it falls in only if it takes effect by
-- about mid-year: panel year = year(date + 6 months). CRS ratings change on
-- April 1 or October 1, so an October change counts from the next year.
-- The policy-stamped class is kept as crs_class_policy_stamp for transparency.
create schema if not exists gold;

create or replace table gold.community_year_panel as
with claims as (
    select
        community_id,
        year_of_loss                              as year,
        count(*)                                  as n_claims,
        count(*) filter (where paid_total > 0)    as n_paid_claims,
        sum(paid_building)                        as paid_building,
        sum(paid_contents)                        as paid_contents,
        sum(paid_icc)                             as paid_icc,
        sum(paid_total)                           as paid_total
    from silver.nfip_claims
    where community_id is not null and year_of_loss is not null
    group by 1, 2
),
status as (
    select
        *,
        year(crs_entry_date + interval 6 month)            as crs_entry_panel_year,
        year(crs_class_effective_date + interval 6 month)  as crs_class_effective_panel_year
    from silver.community_status
),
joined as (
    select
        e.community_id,
        e.year,
        e.state,
        e.county_fips,
        e.county_fips || '-' || e.year            as county_year,
        s.community_name,
        e.earned_policy_years,
        e.earned_coverage,
        e.earned_premium,
        e.n_policy_records,
        e.share_exposure_sfha,
        coalesce(e.crs_class_modal, 10)           as crs_class_policy_stamp,
        s.crs_entry_date,
        s.crs_entry_year,
        s.crs_entry_panel_year,
        s.crs_class_current,
        s.crs_class_effective_date,
        s.crs_class_effective_panel_year,
        coalesce(c.n_claims, 0)                   as n_claims,
        coalesce(c.n_paid_claims, 0)              as n_paid_claims,
        coalesce(c.paid_building, 0)              as paid_building,
        coalesce(c.paid_contents, 0)              as paid_contents,
        coalesce(c.paid_icc, 0)                   as paid_icc,
        coalesce(c.paid_total, 0)                 as paid_total
    from silver.policy_exposure_community_year e
    left join claims c using (community_id, year)
    left join status s using (community_id)
    where e.earned_policy_years > 0
),
history as (
    select
        *,
        case
            when crs_entry_panel_year is null and coalesce(crs_class_current, 10) = 10 then false
            when crs_entry_panel_year is not null and year < crs_entry_panel_year      then false
            when crs_class_current between 1 and 9                                     then true
            else null
        end as in_crs
    from joined
),
classed as (
select
    *,
    case
        when in_crs = false                                                         then 10
        when in_crs and crs_class_effective_panel_year is not null
             and year >= crs_class_effective_panel_year                             then crs_class_current
        else null
    end                                                     as crs_class,
    case
        when in_crs = false then 'not_in_crs'
        when in_crs and year >= crs_class_effective_panel_year then 'current_class'
        when in_crs then 'in_crs_class_unknown'
        else 'participation_unknown'
    end                                                     as crs_status_basis,
    paid_total / earned_policy_years                        as loss_cost_per_policy_year,
    1000.0 * n_claims / earned_policy_years                 as claims_per_1k_policy_years,
    paid_total / nullif(n_paid_claims, 0)                   as severity_per_paid_claim,
    1000.0 * paid_total / nullif(earned_coverage, 0)        as loss_per_1k_coverage,
    paid_total / nullif(earned_premium, 0)                  as loss_ratio,
    case when year < 2010 then 'warmup'
         when year <= 2021 then 'pre_rr2'
         else 'rr2' end                                     as regime
from history
)
select
    *,
    (10 - crs_class) * 5                                    as crs_discount_pct_sfha
from classed;
