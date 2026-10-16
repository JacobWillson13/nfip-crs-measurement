-- requires: silver_nfip_claims
-- Gold: claims aggregated by community and year of loss, over the full claims history
-- (1978 onward). Grain: one row per (community_id, year_of_loss) with at least one claim.
-- Not a loss rate on its own; gold.community_year_panel adds exposure from 2009 onward.
create schema if not exists gold;

create or replace table gold.community_year_claims as
with base as (
    select *
    from silver.nfip_claims
    where community_id is not null
      and year_of_loss is not null
),
crs_counts as (
    select
        community_id,
        year_of_loss,
        crs_class,
        count(*) as n,
        row_number() over (
            partition by community_id, year_of_loss
            order by count(*) desc, crs_class
        ) as rn
    from base
    where crs_class is not null
    group by community_id, year_of_loss, crs_class
)
select
    b.community_id,
    b.year_of_loss,
    any_value(b.state)                                         as state,
    any_value(b.community_name)                                as community_name,
    count(*)                                                   as n_claims,
    count(*) filter (where b.paid_total > 0)                   as n_paid_claims,
    sum(b.paid_building)                                       as paid_building,
    sum(b.paid_contents)                                       as paid_contents,
    sum(b.paid_icc)                                            as paid_icc,
    sum(b.paid_total)                                          as paid_total,
    sum(b.paid_total) / nullif(count(*) filter (where b.paid_total > 0), 0)
                                                               as avg_paid_per_paid_claim,
    avg(case when b.in_sfha then 1.0 when not b.in_sfha then 0.0 end)
                                                               as share_claims_in_sfha,
    c.crs_class                                                as crs_class_modal,
    count(distinct b.crs_class)                                as n_distinct_crs_classes,
    count(*) filter (where b.crs_class is not null)::double / count(*)
                                                               as share_claims_with_crs
from base b
left join crs_counts c
    on  c.community_id = b.community_id
    and c.year_of_loss = b.year_of_loss
    and c.rn = 1
group by b.community_id, b.year_of_loss, c.crs_class;
