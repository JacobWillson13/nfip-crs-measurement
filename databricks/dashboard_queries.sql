-- Datasets for the AI/BI dashboard "NFIP CRS measurement". databricks/build_dashboard.py
-- reads this file (one dataset per "-- dataset:" block) and writes the dashboard
-- definition the bundle deploys, so the SQL lives here only. Assumes catalog "workspace"
-- and schema "nfip"; change both if the bundle variables differ.
--
-- Block format: "-- dataset: name", then one comment line with the description, then
-- optional "-- param: keyword TYPE [MULTI]" lines declaring dashboard parameters, then SQL.
-- A MULTI parameter arrives as an array; an empty selection means "all".
-- {check_tables} and {table_counts} are filled in by the generator from
-- pipeline/quality/checks.py, so failure shares stay in sync with the checks.
-- Widgets can only take a column or one aggregate of a column, so every ratio, label,
-- and format is computed here.

-- dataset: headline
-- One row of headline numbers for the overview counters (dollars in USD so the counters can format them).
select
  (select effect_pct from workspace.nfip.results_pricing_adequacy
    where model = 'A_claim_frequency_per_class_step' and coefficient = 'class_steps')   as claims_per_step_pct,
  (select effect_pct from workspace.nfip.results_pricing_adequacy
    where model = 'A_loss_cost_per_class_step' and coefficient = 'class_steps')        as loss_cost_per_step_pct,
  (select total_expected_abs_gap_usd from workspace.nfip.results_pooled_summary)       as expected_abs_gap_usd,
  (select total_evsi_half_variance_usd from workspace.nfip.results_pooled_summary)     as evsi_usd;

-- dataset: losses_by_year
-- Claims and paid losses by year, 2010 to 2024, filterable by state and CRS class.
-- param: state STRING MULTI
-- param: crs_group STRING MULTI
with p as (
  select year, state, earned_policy_years, n_claims, paid_total,
         case when crs_status_basis = 'not_in_crs'            then 'Not in CRS'
              when crs_status_basis = 'participation_unknown' then 'Participation unknown'
              when crs_class is null                          then 'In CRS, class unknown'
              when crs_class <= 5                             then 'Class 1-5'
              else concat('Class ', cast(crs_class as int)) end as crs_group
  from workspace.nfip.gold_community_year_panel
  where year between 2010 and 2024
)
select year,
       sum(earned_policy_years)                                   as policy_years,
       sum(n_claims)                                              as claims,
       1000 * sum(n_claims) / sum(earned_policy_years)            as claims_per_1k_policy_years,
       sum(paid_total) / 1e9                                      as paid_busd
from p
where (size(:state) = 0 or array_contains(:state, state))
  and (size(:crs_group) = 0 or array_contains(:crs_group, crs_group))
group by year
order by year;

-- dataset: state_options
-- States in the panel, for the state filter.
select distinct state from workspace.nfip.gold_community_year_panel order by state;

-- dataset: crs_group_options
-- CRS class groups, for the CRS class filter.
select crs_group from values
  ('Not in CRS'), ('Class 1-5'), ('Class 6'), ('Class 7'), ('Class 8'), ('Class 9'),
  ('In CRS, class unknown'), ('Participation unknown') as t(crs_group);

-- dataset: state_map
-- Share of NFIP policy-years in CRS communities by state, 2022 to 2024.
with names as (
  select * from values
    ('AL', 'Alabama'), ('AK', 'Alaska'), ('AZ', 'Arizona'), ('AR', 'Arkansas'), ('CA', 'California'),
    ('CO', 'Colorado'), ('CT', 'Connecticut'), ('DE', 'Delaware'), ('DC', 'District of Columbia'),
    ('FL', 'Florida'), ('GA', 'Georgia'), ('HI', 'Hawaii'), ('ID', 'Idaho'), ('IL', 'Illinois'),
    ('IN', 'Indiana'), ('IA', 'Iowa'), ('KS', 'Kansas'), ('KY', 'Kentucky'), ('LA', 'Louisiana'),
    ('ME', 'Maine'), ('MD', 'Maryland'), ('MA', 'Massachusetts'), ('MI', 'Michigan'), ('MN', 'Minnesota'),
    ('MS', 'Mississippi'), ('MO', 'Missouri'), ('MT', 'Montana'), ('NE', 'Nebraska'), ('NV', 'Nevada'),
    ('NH', 'New Hampshire'), ('NJ', 'New Jersey'), ('NM', 'New Mexico'), ('NY', 'New York'),
    ('NC', 'North Carolina'), ('ND', 'North Dakota'), ('OH', 'Ohio'), ('OK', 'Oklahoma'), ('OR', 'Oregon'),
    ('PA', 'Pennsylvania'), ('RI', 'Rhode Island'), ('SC', 'South Carolina'), ('SD', 'South Dakota'),
    ('TN', 'Tennessee'), ('TX', 'Texas'), ('UT', 'Utah'), ('VT', 'Vermont'), ('VA', 'Virginia'),
    ('WA', 'Washington'), ('WV', 'West Virginia'), ('WI', 'Wisconsin'), ('WY', 'Wyoming')
    as t(state, state_name)
)
select n.state_name,
       round(100 * sum(case when p.crs_status_basis in ('current_class', 'in_crs_class_unknown')
                            then p.earned_policy_years else 0 end) / sum(p.earned_policy_years), 1) as crs_share_pct
from workspace.nfip.gold_community_year_panel p
join names n on n.state = p.state
where p.year between 2022 and 2024
group by n.state_name;

-- dataset: class_decision
-- Pricing decision by CRS class: pooled relativity vs scheduled discount, and the value of information.
select concat('Class ', crs_bin)                                  as crs_class,
       round(pooled_effect_pct, 1)                                as pooled_loss_cost_effect_pct,
       round(pooled_low90_pct, 1)                                 as low90_pct,
       round(pooled_high90_pct, 1)                                as high90_pct,
       concat(format_number(pooled_effect_pct, 1), ' (', format_number(pooled_low90_pct, 1), ' to ',
              format_number(pooled_high90_pct, 1), ')')           as loss_cost_interval,
       round(scheduled_discount_pct, 1)                           as scheduled_discount_pct,
       round(-scheduled_discount_pct, 1)                          as break_even_pct,
       round(pooled_effect_pct + scheduled_discount_pct, 1)       as losses_above_discount_pts,
       round(100 * p_discount_too_generous, 0)                    as p_too_generous_pct,
       round(expected_gap_usd / 1e6, 1)                           as expected_gap_musd,
       round(expected_abs_gap_usd / 1e6, 1)                       as expected_abs_gap_musd,
       round(best_discount_pct, 1)                                as best_discount_pct,
       round(evpi_usd / 1e6, 1)                                   as evpi_musd,
       round(evsi_half_variance_usd / 1e6, 1)                     as evsi_half_variance_musd
from workspace.nfip.results_pricing_decision
order by crs_bin;

-- dataset: discount_compare
-- Scheduled discount vs the discount that best fits current evidence, by CRS class.
select concat('Class ', crs_bin) as crs_class, 'Scheduled' as discount_type,
       round(scheduled_discount_pct, 1) as discount_pct
from workspace.nfip.results_pricing_decision
union all
select concat('Class ', crs_bin), 'Best on current evidence', round(best_discount_pct, 1)
from workspace.nfip.results_pricing_decision;

-- dataset: entry_design
-- Placebo simulation of the CRS entry difference-in-differences: detectable effect by size, unbiased design only.
select concat('Within county, ', replace(outcome, '_', ' '))      as design_outcome,
       n_entrants,
       round(mde_pct, 1)                                          as mde_pct
from workspace.nfip.results_c_power_design
where design = 'within_county';

-- dataset: entry_design_check
-- Placebo checks at the actual number of CRS entrants: why the year-fixed-effects design was dropped.
select case design when 'within_county' then 'Within county and year' else 'Year fixed effects only (original)' end
                                                                  as design,
       replace(outcome, '_', ' ')                                 as outcome,
       round(placebo_mean_pct, 1)                                 as bias_under_no_effect_pct,
       round(100 * false_positive_rate, 0)                        as false_positive_rate_pct,
       round(mde_pct, 1)                                          as mde_pct,
       case when design = 'within_county' then 'Kept, with placebo-calibrated errors' else 'Dropped: biased' end as verdict
from workspace.nfip.results_c_power_design
where n_entrants = (select min(n_entrants) from workspace.nfip.results_c_power_design)
order by design desc, outcome;

-- dataset: experiment_power
-- Proposed randomized CRS recruitment trial: detectable drop in claims by design and size.
select concat(case assignment when 'within_county' then 'Within county' else 'Whole counties' end, ', ',
              replace(analysis, '_', ' '))                        as design,
       case size when 'all' then 'All eligible' else concat('~', size, ' pilot') end as trial_size,
       cast(n_communities as int)                                 as communities,
       round(itt_mde_pct, 1)                                      as itt_mde_pct,
       case when itt_mde_pct >= 90 then 'Not detectable' else format_number(itt_mde_pct, 1) end
                                                                  as itt_mde_label,
       case when complier_mde_pct_p50 >= 90 then 'Not detectable' else format_number(complier_mde_pct_p50, 1) end
                                                                  as complier_p50_label,
       case when complier_mde_pct_p25 >= 90 then 'Not detectable' else format_number(complier_mde_pct_p25, 1) end
                                                                  as complier_p25_label
from workspace.nfip.results_e_experiment_power
where outcome = 'n_claims'
order by cast(n_communities as int), design;

-- dataset: pipeline_health
-- Quality checks from the latest job run, failures first, with failing rows as a share of the checked table.
with latest as (
  select * from workspace.nfip.ops_dq_results
  where run_at = (select max(run_at) from workspace.nfip.ops_dq_results)
),
check_tables as (
  select * from values {check_tables} as t(check_name, table_name)
),
table_counts as (
  {table_counts}
)
select l.check_name, l.layer, l.severity, l.failing_rows,
       case when l.passed then 'Passed' when l.severity = 'error' then 'Blocking' else 'Warning' end as status,
       round(100 * l.failing_rows / c.n, 2)                       as failing_share_pct,
       case when l.passed then 1 else 0 end                       as passed_flag,
       case when l.passed then 0 when l.severity = 'error' then 1 else 0 end as blocking,
       case when not l.passed and l.severity = 'warn' then 1 else 0 end      as warning,
       round((unix_timestamp(current_timestamp()) - unix_timestamp(l.run_at)) / 86400, 1) as days_since_run,
       (select count(distinct run_at) from workspace.nfip.ops_dq_results) as runs_recorded
from latest l
left join check_tables t on t.check_name = l.check_name
left join table_counts c on c.table_name = t.table_name
order by case when l.passed then 2 when l.severity = 'error' then 0 else 1 end, l.failing_rows desc, l.check_name;

-- dataset: dq_history
-- Check outcomes per job run, for the run history chart.
select date_format(run_at, 'yyyy-MM-dd HH:mm')                    as run,
       case when passed then 'Passed' when severity = 'error' then 'Blocking' else 'Warning' end as status,
       count(*)                                                   as checks
from workspace.nfip.ops_dq_results
group by 1, 2
order by 1;

-- dataset: load_log
-- Latest silver load per dataset, with file vs table row counts.
select dataset, file_rows, table_rows, status, loaded_at
from workspace.nfip.ops_load_log
qualify row_number() over (partition by dataset order by loaded_at desc) = 1
order by dataset;
