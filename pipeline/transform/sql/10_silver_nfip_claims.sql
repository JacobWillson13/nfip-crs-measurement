-- requires: nfip_claims
-- Silver: one row per claim, renamed to snake_case, typed, with known source quirks handled.
-- Source quirks handled here (see ARCHITECTURE.md, "Data quirks"):
--   * originalConstructionDate uses 1492-10-12 as an "unknown" sentinel -> null + flag
--   * amountPaidOn* is null on closed-without-payment claims -> 0 for paid_* columns
create schema if not exists silver;

create or replace table silver.nfip_claims as
select
    id                                              as claim_id,
    state,
    countyCode                                      as county_fips,
    censusGeoid                                     as census_block_group,
    reportedZipCode                                 as zip_code,
    latitude,
    longitude,
    nfipRatedCommunityNumber                        as community_id,
    nfipCommunityNumberCurrent                      as community_id_current,
    nfipCommunityName                               as community_name,
    crsClassCode                                    as crs_class,
    ratedFloodZone                                  as rated_flood_zone,
    case
        when ratedFloodZone is null then null
        when upper(ratedFloodZone) like 'A%' or upper(ratedFloodZone) like 'V%' then true
        else false
    end                                             as in_sfha,
    cast(dateOfLoss as date)                        as date_of_loss,
    yearOfLoss                                      as year_of_loss,
    case when year(originalConstructionDate) < 1800 then null
         else cast(originalConstructionDate as date) end as construction_date,
    coalesce(year(originalConstructionDate) < 1800, false) as construction_date_is_sentinel,
    cast(originalNBDate as date)                    as policy_original_nb_date,
    elevationDifference                             as elevation_difference_ft,
    elevatedBuildingIndicator                       as is_elevated,
    postFIRMConstructionIndicator                   as is_post_firm,
    primaryResidenceIndicator                       as is_primary_residence,
    occupancyType                                   as occupancy_type,
    policyCount                                     as policy_count,
    floodEvent                                      as flood_event,
    eventDesignationNumber                          as event_id,
    causeOfDamage                                   as cause_of_damage,
    waterDepth                                      as water_depth,
    buildingDamageAmount                            as damage_building,
    contentsDamageAmount                            as damage_contents,
    coalesce(amountPaidOnBuildingClaim, 0)          as paid_building,
    coalesce(amountPaidOnContentsClaim, 0)          as paid_contents,
    coalesce(amountPaidOnIncreasedCostOfComplianceClaim, 0) as paid_icc,
    coalesce(amountPaidOnBuildingClaim, 0)
      + coalesce(amountPaidOnContentsClaim, 0)
      + coalesce(amountPaidOnIncreasedCostOfComplianceClaim, 0) as paid_total,
    totalBuildingInsuranceCoverage                  as coverage_building,
    totalContentsInsuranceCoverage                  as coverage_contents,
    rateMethod                                      as rate_method,
    asOfDate                                        as source_as_of,
    current_timestamp                               as loaded_at
from read_parquet('{bronze_nfip_claims}', hive_partitioning = false);
