# Test Plan

Automated tests run with `pytest -q` and need no network: a fake OpenFEMA server in `tests/conftest.py` serves real v3 claim records from `tests/fixtures/nfip_claims_sample.json` and mimics filtering, paging, `$select`, `$count`, and error responses.

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

## Manual checks on first live run

1. Run `python -m pipeline.cli discover NfipClaims` and confirm v3 is current and not deprecated.
2. Run `python -m pipeline.cli run --states VA` and confirm `ops.run_log` shows `success` with expected and landed rows equal.
3. Compare the VA claim count against the record count shown on FEMA's dataset page filtered to Virginia.
4. Review `select * from ops.dq_results where not passed` and record warn-level counts as the baseline.
