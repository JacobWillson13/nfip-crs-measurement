-- Datasets for the AI/BI dashboard "NFIP CRS measurement". databricks/build_dashboard.py
-- reads this file (one dataset per "-- dataset:" block) and writes the dashboard
-- definition the bundle deploys, so the SQL lives here only. Assumes catalog "workspace"
-- and schema "nfip"; change both if the bundle variables differ.

-- dataset: headline
-- One row of headline numbers for the overview counters.
select
  (select effect_pct from workspace.nfip.results_pricing_adequacy
    where model = 'A_claim_frequency_per_class_step' and coefficient = 'class_steps')   as claims_per_step_pct,
  (select effect_pct from workspace.nfip.results_pricing_adequacy
    where model = 'A_loss_cost_per_class_step' and coefficient = 'class_steps')        as loss_cost_per_step_pct,
  (select total_expected_abs_gap_usd / 1e6 from workspace.nfip.results_pooled_summary) as expected_abs_gap_musd,
  (select total_evsi_half_variance_usd / 1e6 from workspace.nfip.results_pooled_summary) as evsi_musd,
  (select mde_pct from workspace.nfip.results_c_power_design
    where design = 'within_county' and outcome = 'claim_frequency'
    order by n_entrants limit 1)                                                       as entry_mde_claims_pct;

-- dataset: losses_by_year
-- National totals and rates by year, 2010 to 2024. Rates are computed here: dashboard widgets only take plain aggregates.
select year,
       sum(earned_policy_years)                                   as policy_years,
       sum(n_claims)                                              as claims,
       1000 * sum(n_claims) / sum(earned_policy_years)            as claims_per_1k_policy_years,
       sum(paid_total) / 1e9                                      as paid_busd
from workspace.nfip.gold_community_year_panel
where year between 2010 and 2024
group by year;

-- dataset: class_decision
-- Pricing decision by CRS class: pooled relativity vs scheduled discount, and the value of information.
select concat('Class ', crs_bin)                                  as crs_class,
       round(pooled_effect_pct, 1)                                as pooled_loss_cost_effect_pct,
       round(pooled_low90_pct, 1)                                 as low90_pct,
       round(pooled_high90_pct, 1)                                as high90_pct,
       round(scheduled_discount_pct, 1)                           as scheduled_discount_pct,
       round(pooled_effect_pct + scheduled_discount_pct, 1)       as losses_above_discount_pts,
       round(100 * p_discount_too_generous, 0)                    as p_too_generous_pct,
       round(expected_gap_usd / 1e6, 1)                           as expected_gap_musd,
       round(best_discount_pct, 1)                                as best_discount_pct,
       round(evpi_usd / 1e6, 1)                                   as evpi_musd,
       round(evsi_half_variance_usd / 1e6, 1)                     as evsi_half_variance_musd
from workspace.nfip.results_pricing_decision;

-- dataset: entry_design
-- Placebo simulation of the CRS entry difference-in-differences: detectable effect by design and size.
select concat(case design when 'within_county' then 'Within county' else 'Year FE only' end, ', ',
              replace(outcome, '_', ' '))                         as design_outcome,
       n_entrants,
       round(mde_pct, 1)                                          as mde_pct,
       round(100 * false_positive_rate, 0)                        as false_positive_rate_pct,
       round(placebo_mean_pct, 1)                                 as bias_under_no_effect_pct
from workspace.nfip.results_c_power_design;

-- dataset: experiment_power
-- Proposed randomized CRS recruitment trial: detectable drop in claims by design and size.
select concat(case assignment when 'within_county' then 'Within county' else 'Whole counties' end, ', ',
              replace(analysis, '_', ' '))                        as design,
       case size when 'all' then 'All eligible' else concat('~', size, ' pilot') end as trial_size,
       cast(n_communities as int)                                 as communities,
       round(itt_mde_pct, 1)                                      as itt_mde_pct,
       round(complier_mde_pct_p50, 1)                             as complier_mde_pct_at_50pct_uptake,
       round(complier_mde_pct_p25, 1)                             as complier_mde_pct_at_25pct_uptake
from workspace.nfip.results_e_experiment_power
where outcome = 'n_claims';

-- dataset: pipeline_health
-- Quality checks from the latest job run.
select check_name, layer, severity, failing_rows,
       case when passed then 'yes' else 'no' end                  as passed,
       case when passed then 1 else 0 end                         as passed_flag,
       case when passed then 0 when severity = 'error' then 1 else 0 end as blocking
from workspace.nfip.ops_dq_results
where run_at = (select max(run_at) from workspace.nfip.ops_dq_results);

-- dataset: load_log
-- Latest silver load per dataset, with file vs table row counts.
select dataset, file_rows, table_rows, status, loaded_at
from workspace.nfip.ops_load_log
qualify row_number() over (partition by dataset order by loaded_at desc) = 1;
