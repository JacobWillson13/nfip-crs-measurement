-- requires: nfip_community_status_book
-- Silver: one row per NFIP community from the Community Status Book.
-- This is a current snapshot: classRating is today's CRS class, and communities that
-- left CRS may show no entry date. Year-by-year class history comes from policies.
create schema if not exists silver;

create or replace table silver.community_status as
select
    communityIdNumber                          as community_id,
    communityName                              as community_name,
    state,
    county                                     as county_name,
    participatingInNFIP                        as participating_in_nfip,
    tribal                                     as is_tribal,
    cast(originalEntryDate as date)            as crs_entry_date,
    year(originalEntryDate)                    as crs_entry_year,
    classRating                                as crs_class_current,
    cast(classRatingEffectiveDate as date)     as crs_class_effective_date,
    try_cast(replace(sfhaDiscount, '%', '') as integer)    as sfha_discount_pct_current,
    try_cast(replace(nonSfhaDiscount, '%', '') as integer) as non_sfha_discount_pct_current,
    sanction                                   as is_sanctioned,
    lastRefresh                                as source_refreshed_at
from read_parquet('{bronze_nfip_community_status_book}', hive_partitioning = false);
