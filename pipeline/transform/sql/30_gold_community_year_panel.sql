-- requires: silver_policy_exposure, silver_nfip_claims, silver_community_status
-- Gold: the analysis panel. One row per community and calendar year with any earned
-- exposure, 2009 onward. Exposure is the spine, so years with zero claims are present
-- (as zeros), which the claims-only table cannot provide.
--
-- CRS class for a year is the exposure-weighted modal class on that year's policies.
-- Null class on policies is treated as Class 10 (no discount).
-- Discount shown is the SFHA schedule: 5 percentage points per class below 10.
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
panel as (
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
        coalesce(e.crs_class_modal, 10)           as crs_class,
        e.crs_class_modal_share,
        e.share_exposure_crs_null,
        s.crs_entry_date,
        s.crs_entry_year,
        s.crs_class_current,
        coalesce(c.n_claims, 0)                   as n_claims,
        coalesce(c.n_paid_claims, 0)              as n_paid_claims,
        coalesce(c.paid_building, 0)              as paid_building,
        coalesce(c.paid_contents, 0)              as paid_contents,
        coalesce(c.paid_icc, 0)                   as paid_icc,
        coalesce(c.paid_total, 0)                 as paid_total
    from silver.policy_exposure_community_year e
    left join claims c using (community_id, year)
    left join silver.community_status s using (community_id)
    where e.earned_policy_years > 0
)
select
    *,
    crs_class <= 9                                          as in_crs,
    (10 - crs_class) * 5                                    as crs_discount_pct_sfha,
    paid_total / earned_policy_years                        as loss_cost_per_policy_year,
    1000.0 * n_claims / earned_policy_years                 as claims_per_1k_policy_years,
    paid_total / nullif(n_paid_claims, 0)                   as severity_per_paid_claim,
    1000.0 * paid_total / nullif(earned_coverage, 0)        as loss_per_1k_coverage,
    paid_total / nullif(earned_premium, 0)                  as loss_ratio,
    case when year < 2010 then 'warmup'
         when year <= 2021 then 'pre_rr2'
         else 'rr2' end                                     as regime
from panel;
