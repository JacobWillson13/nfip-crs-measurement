-- Datasets for the AI/BI dashboard "NFIP CRS measurement". In the workspace: Dashboards ->
-- Create dashboard -> Data -> "Create from SQL", one dataset per query below (name in the
-- header comment). Page layout and widgets: databricks/README.md. Assumes catalog
-- "workspace" and schema "nfip"; change both if the bundle variables differ.

-- dataset: losses_by_year (page 1: line charts of claims per 1,000 policy-years and paid losses; filter on state)
select year, state,
       sum(earned_policy_years)                                   as policy_years,
       sum(n_claims)                                              as claims,
       1000 * sum(n_claims) / sum(earned_policy_years)            as claims_per_1k_policy_years,
       sum(paid_total) / 1e6                                      as paid_musd
from workspace.nfip.gold_community_year_panel
where year between 2010 and 2024
group by year, state;

-- dataset: class_decision (page 2: bar or point chart of pooled effect vs scheduled discount; table with
-- probability the discount is too generous, expected gap, and value of information)
select concat('Class ', crs_bin)                                  as crs_class,
       round(pooled_effect_pct, 1)                                as pooled_loss_cost_effect_pct,
       round(pooled_low90_pct, 1)                                 as low90_pct,
       round(pooled_high90_pct, 1)                                as high90_pct,
       round(-scheduled_discount_pct, 1)                          as scheduled_discount_pct,
       round(100 * p_discount_too_generous, 0)                    as p_too_generous_pct,
       round(expected_gap_usd / 1e6, 1)                           as expected_gap_musd,
       round(evpi_usd / 1e6, 1)                                   as evpi_musd,
       round(evsi_half_variance_usd / 1e6, 1)                     as evsi_half_variance_musd
from workspace.nfip.results_pricing_decision;

-- dataset: entry_design (page 3: minimum detectable effect by entrants, one line per design and outcome)
select design, outcome, n_entrants,
       round(mde_pct, 1)                                          as mde_pct,
       round(100 * false_positive_rate, 0)                        as false_positive_rate_pct,
       round(placebo_mean_pct, 1)                                 as bias_under_no_effect_pct
from workspace.nfip.results_c_power_design;

-- dataset: experiment_power (page 3: detectable drop in claims for the proposed randomized pilot)
select size, assignment, analysis,
       cast(n_communities as int)                                 as communities,
       round(itt_mde_pct, 1)                                      as itt_mde_pct,
       round(complier_mde_pct_p25, 1)                             as complier_mde_pct_at_25pct_uptake,
       round(complier_mde_pct_p50, 1)                             as complier_mde_pct_at_50pct_uptake
from workspace.nfip.results_e_experiment_power
where outcome = 'n_claims';

-- dataset: pipeline_health (page 4: counters for checks passed and failed; table of failing checks)
select check_name, layer, severity, failing_rows, passed, run_at
from workspace.nfip.ops_dq_results
where run_at = (select max(run_at) from workspace.nfip.ops_dq_results);

-- dataset: load_log (page 4: table of the latest silver loads with row-count reconciliation)
select dataset, file_rows, table_rows, status, loaded_at
from workspace.nfip.ops_load_log
qualify row_number() over (partition by dataset order by loaded_at desc) = 1;
