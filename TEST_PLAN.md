# Test Plan

Automated tests run with `pytest -q` and need no network. A fake OpenFEMA server in `tests/conftest.py` mimics filtering, paging, `$select`, `$orderby`, `$count`, and error responses. Claims fixtures are real v3 records; Status Book and policy fixtures are synthetic, built on the same community IDs so joins are tested end to end, with expected exposure worked out by hand.

## Ingestion

| ID | Case | Expected | Test |
| --- | --- | --- | --- |
| I-1 | Partition spans several pages | All pages fetched; paging stops on a short page | `test_pages_until_short_page` |
| I-2 | Paging order | Every request ordered by `id` so `$skip` is stable | `test_paging_is_ordered_by_id` |
| I-3 | Transient 503 and 429 responses | Retried with backoff, then succeeds | `test_retries_transient_errors_then_succeeds` |
| I-4 | Persistent server errors | Fails after max retries with a clear error | `test_gives_up_after_max_retries` |
| I-5 | 400 client error | Not retried; fails immediately | `test_client_errors_are_not_retried` |
| I-6 | Successful load | Landed rows equal API count; manifest written; run logged | `test_loads_partition_and_reconciles` |
| I-7 | Source unchanged since last load | Partition skipped and logged | `test_unchanged_source_is_skipped` |
| I-8 | FEMA publishes a new refresh | Partition reloaded | `test_new_source_refresh_triggers_reload` |
| I-9 | Count mismatch | Previous partition and watermark untouched | `test_count_mismatch_keeps_last_good_partition` |
| I-10 | Reload of same partition | Replaced, not appended | `test_reload_replaces_rather_than_appends` |

## Schema contract

| ID | Case | Expected | Test |
| --- | --- | --- | --- |
| S-1 | Real records | Types match the pinned schema; ids stay strings where needed | `test_round_trips_real_records_with_pinned_types` |
| S-2 | 1492 sentinel date | Preserved in bronze, not silently dropped | `test_sentinel_dates_survive_parsing` |
| S-3 | Page where a column is all null | Declared type kept | `test_all_null_page_keeps_declared_type` |
| S-4 | Value no longer fits its type | `SchemaDriftError` naming the field | `test_type_drift_names_the_field` |
| S-5 | Field dropped upstream | `SchemaDriftError` naming the field | `test_missing_field_is_detected` |

## Transform and quality

| ID | Case | Expected | Test |
| --- | --- | --- | --- |
| T-1 | Sentinel construction date | Null in silver, flagged | `test_silver_handles_source_quirks` |
| T-2 | Claim closed without payment | `paid_total` = 0, not null | `test_silver_handles_source_quirks` |
| T-3 | Flood zone to SFHA flag | A and V zones true; X and C false | `test_sfha_flag` |
| T-4 | Gold aggregation (Houston, Harvey 2017) | 2 claims, paid total matches hand calculation, modal CRS class 5 | `test_gold_aggregates_houston_harvey` |
| T-5 | Clean load | All error-level checks pass; sentinel warning surfaced | `test_all_blocking_checks_pass` |
| T-6 | Rows dropped between bronze and silver | Reconciliation check fails the run | `test_reconciliation_catches_dropped_rows` |

## Bulk policies file

| ID | Case | Expected | Test |
| --- | --- | --- | --- |
| B-1 | Valid file | Moved into bronze, manifest with row count and sha256, run logged | `test_register_promotes_file_with_manifest` |
| B-2 | Same file registered again | Skipped by hash | `test_reregistering_same_file_is_skipped` |
| B-3 | File and catalog counts differ | Recorded in run log, not blocking | `test_catalog_count_mismatch_is_recorded_not_blocking` |
| B-4 | Required column missing | Rejected with column list; run logged as failed | `test_missing_required_column_is_rejected` |
| B-5 | fema.gov blocks scripted download | Clear error with browser fallback command | `test_blocked_download_explains_browser_fallback` |

## Exposure and panel

| ID | Case | Expected | Test |
| --- | --- | --- | --- |
| E-1 | Policy spanning two calendar years | Exposure, premium, coverage split by days (leap year handled) | `test_policy_split_across_calendar_years` |
| E-2 | Early cancellation and policyCount > 1 | Exposure truncated at cancellation, multiplied by count | `test_cancellation_and_policy_count` |
| E-3 | Reversed term dates, missing community | Excluded | `test_bad_terms_and_missing_community_are_excluded` |
| E-4 | Community-year with no claims | Present with zero claims and zero loss cost | `test_panel_has_zero_claim_years` |
| E-5 | Houston 2017 | Loss cost = hand-computed paid / earned policy-years; CRS class 5, 25% discount | `test_panel_loss_metrics` |
| E-6 | Null CRS class on policies | Treated as Class 10, not in CRS | `test_null_policy_crs_class_means_class_10` |
| E-7 | County-year key for fixed effects | `county_fips-year` | `test_county_year_key` |
| E-8 | Status Book parsing | Entry year, class, discount percent parsed | `test_status_book_silver` |
| E-9 | Full build | All error-level checks pass, including join fan-out reconciliation | `test_checks_pass_on_full_build` |
| E-10 | Feasibility report | Gate tables printed and written to CSV | `test_feasibility_report_writes_csvs` |
| E-11 | Policies not loaded yet | Exposure and panel models skipped, claims still built | `test_build_skips_models_whose_inputs_are_missing` |

## Manual checks on first live run

1. `python -m pipeline.cli discover NfipClaims` and `discover NfipPolicies`: confirm v3 is current and not deprecated.
2. After `ingest`: `ops.run_log` shows `success` for every state with expected and landed rows equal.
3. After `register`: row count in the manifest is close to the catalog's 74.7M.
4. After `report` (Gate 1): national earned policy-years are roughly 5 million per year in the 2010s, with no unexplained jumps; claim rates spike in 2012 and 2017.
5. Review `select * from ops.dq_results where not passed`; record warn-level counts, especially `paid_claims_without_exposure`, as the baseline.
