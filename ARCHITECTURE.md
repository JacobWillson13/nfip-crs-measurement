# Architecture

## Flow

```
OpenFEMA API (NfipClaims v3)
   │  paged by state, ordered by id, retried with backoff
   ▼
bronze   data/lake/bronze/nfip_claims/state=XX/part-*.parquet + _manifest.json
   │  pinned schema, raw field names, one partition per state
   ▼
silver   silver.nfip_claims        one row per claim, typed, renamed, quirks handled
   ▼
gold     gold.community_year_claims   one row per community and year of loss
   ▼
ops      ops.run_log, ops.watermarks, ops.dq_results
```

Everything runs locally on DuckDB and Parquet. The same layers map one to one onto a Fabric lakehouse (bronze files in OneLake, silver and gold as Delta tables) if the pipeline is ported later.

## Design decisions

**Partition-level full replace, not append.** FEMA revises historical claims after the fact: payments, recoveries, and reclassified fields change on records that already exist. An id-based append would keep stale versions; an upsert needs a reliable per-record change timestamp, and v3 only exposes `asOfDate`, which is a snapshot date for the whole extract. Replacing one state at a time is idempotent, simple to reason about, and cheap at this volume.

**Dataset-level watermark.** Before loading, the loader reads `lastDataSetRefresh` from the OpenFEMA catalog. If a partition was already loaded from that refresh, it is skipped. FEMA refreshes about monthly, so a scheduled monthly run does real work once and is a no-op otherwise. `--force` overrides.

**Stage, reconcile, then swap.** Each load writes to `_staging/<run_id>`, compares the landed row count to the API's server-side `$count` for the same filter, and only then swaps into place. A failed or mismatched load leaves the last good partition and its watermark untouched. Every attempt, including skips and failures, is recorded in `ops.run_log`.

**Pinned schema.** Column types are declared in `pipeline/ingest/schema.py` rather than inferred. Inference breaks on real API data: a page where every `crsClassCode` is null would infer the wrong type, and community numbers like `060173` would lose their leading zero if read as integers. A field that disappears upstream or stops fitting its type raises `SchemaDriftError` naming the field.

**Plain SQL instead of dbt (for now).** The transform layer is two SQL files run in order. That keeps the dependency surface small while the model count is low. If the model graph grows past a handful of tables, these files port directly to dbt-duckdb models, and the checks in `pipeline/quality/` map to dbt tests.

**Checks as data.** Quality checks are SQL queries that return a count of failing rows, tagged `error` or `warn`. Results land in `ops.dq_results` so trends are queryable. Errors fail the run; warnings are surfaced for review. Reconciliation checks tie each layer to the one before it: landed rows to silver rows, and silver claim counts and paid totals to gold.

## Data quirks found in the source

| Quirk | Handling |
| --- | --- |
| v2 endpoint (`FimaNfipClaims`) retired 2026-10-15; v3 renamed to `NfipClaims` | Pipeline targets v3; `discover` command checks the catalog before adding datasets |
| `originalConstructionDate` = 1492-10-12 used as an "unknown" placeholder | Parsed at microsecond precision so it survives; nulled in silver with `construction_date_is_sentinel` flag |
| `amountPaidOn*` is null on claims closed without payment, while `net*PaymentAmount` is 0 | `paid_*` columns coalesce to 0 |
| `nfipCommunityNumberCurrent` is often null on older claims | Gold uses `nfipRatedCommunityNumber`, the community the policy was rated in |
| Community numbers and county codes have leading zeros | Stored as strings |
| `crsClassCode` can differ across claims in the same community and year | Gold records the modal class and `n_distinct_crs_classes`; a warn-level check counts these |

## Known limitations

- **Gold is claims only, not a loss rate.** Claims per policy needs exposure from the policies dataset, which is the next ingestion target. Community-years with zero claims are also absent until exposure is joined.
- **CRS history is inferred from claims.** A community's class is only visible in years it had claims. The complete history should come from FEMA's published CRS community lists.
- **`$count` reconciliation assumes the count is stable during a pull.** If FEMA refreshes mid-run, the load fails as a count mismatch and the previous partition is kept, which is the intended failure mode.
