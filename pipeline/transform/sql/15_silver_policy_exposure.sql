-- requires: nfip_policies
-- Silver: earned exposure by community and calendar year, built from about 75M policy
-- transactions. This is the denominator for every loss rate in the panel.
--
-- Method (standard earned-exposure accounting):
--   * A policy record covers [effective date, end), where end is the termination date,
--     or the cancellation date if the policy was cancelled early.
--   * Each record is split across the calendar years its term overlaps.
--   * Earned policy-years = policyCount * (days covered in year / days in year).
--     A one-year policy from July to July earns 0.5 in each calendar year.
--   * Earned premium = term premium * (days covered in year / days in term).
--   * Coverage-years use total building + contents coverage, not multiplied by
--     policyCount (coverage is already the policy total).
-- Dates may arrive as timestamps or ISO strings; both are normalized to UTC dates.

create schema if not exists silver;

-- One streaming pass: parse terms, split by year, and aggregate to a small table.
-- Nothing at the 75M-row grain is materialized, which keeps memory bounded.
create or replace temp table _exposure_pieces as
with raw as (
    select
        nfipRatedCommunityNumber                                         as community_id,
        propertyState                                                    as state,
        substr(censusGeoid, 1, 5)                                        as county_fips,
        cast(try_cast(policyEffectiveDate as timestamptz) as date)       as eff,
        cast(try_cast(policyTerminationDate as timestamptz) as date)     as term,
        cast(try_cast(cancellationDateOfFloodPolicy as timestamptz) as date) as cancelled,
        coalesce(policyCount, 1)                                         as policy_count,
        crsClassCode                                                     as crs_class,
        upper(ratedFloodZone) like 'A%' or upper(ratedFloodZone) like 'V%' as in_sfha,
        coalesce(totalBuildingInsuranceCoverage, 0)
          + coalesce(totalContentsInsuranceCoverage, 0)                  as coverage,
        coalesce(totalInsurancePremiumOfThePolicy, 0)                    as premium
    from read_parquet('{bronze_nfip_policies}')
    where nfipRatedCommunityNumber is not null
),
terms as (
    select
        *,
        case when cancelled is not null and cancelled < term then cancelled else term end as cover_end,
        datediff('day', eff, term)                                       as term_days
    from raw
    where eff is not null and term is not null and term > eff
),
years as (
    select range::int as yr,
           make_date(range::int, 1, 1)     as y0,
           make_date(range::int + 1, 1, 1) as y1
    from range({year_min}, {year_max} + 1)
)
select
    t.community_id, t.state, t.county_fips, t.crs_class, t.in_sfha, y.yr as year,
    sum(t.policy_count * datediff('day', greatest(t.eff, y.y0), least(t.cover_end, y.y1))
        / datediff('day', y.y0, y.y1))                                   as earned_policy_years,
    sum(t.coverage * datediff('day', greatest(t.eff, y.y0), least(t.cover_end, y.y1))
        / datediff('day', y.y0, y.y1))                                   as earned_coverage,
    sum(t.premium * datediff('day', greatest(t.eff, y.y0), least(t.cover_end, y.y1))
        / t.term_days)                                                   as earned_premium,
    count(*)                                                             as n_policy_records
from terms t
join years y on t.eff < y.y1 and t.cover_end > y.y0
where t.cover_end > t.eff
group by all;

create or replace table silver.policy_exposure_community_year as
with totals as (
    select
        community_id,
        year,
        sum(earned_policy_years)                                         as earned_policy_years,
        sum(earned_coverage)                                             as earned_coverage,
        sum(earned_premium)                                              as earned_premium,
        sum(n_policy_records)                                            as n_policy_records,
        -- coalesce: a filtered sum over zero rows is NULL, which would read as "unknown"
        coalesce(sum(earned_policy_years) filter (where in_sfha), 0)
          / nullif(sum(earned_policy_years), 0)                          as share_exposure_sfha,
        coalesce(sum(earned_policy_years) filter (where crs_class is null), 0)
          / nullif(sum(earned_policy_years), 0)                          as share_exposure_crs_null
    from _exposure_pieces
    group by community_id, year
),
crs as (
    select community_id, year, arg_max(crs_class, w) as crs_class_modal,
           max(w) / sum(w) as crs_class_modal_share
    from (select community_id, year, crs_class, sum(earned_policy_years) as w
          from _exposure_pieces where crs_class is not null group by all)
    group by community_id, year
),
geo as (
    select community_id, year, arg_max(county_fips, w) as county_fips, arg_max(state, w) as state
    from (select community_id, year, county_fips, state, sum(earned_policy_years) as w
          from _exposure_pieces group by all)
    group by community_id, year
)
select t.community_id, t.year, g.state, g.county_fips,
       t.earned_policy_years, t.earned_coverage, t.earned_premium, t.n_policy_records,
       t.share_exposure_sfha, t.share_exposure_crs_null,
       c.crs_class_modal, c.crs_class_modal_share
from totals t
left join crs c using (community_id, year)
left join geo g using (community_id, year);

drop table _exposure_pieces;
