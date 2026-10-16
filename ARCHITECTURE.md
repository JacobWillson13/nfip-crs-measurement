# Architecture

## Flow

```
OpenFEMA API                                   OpenFEMA bulk file
  NfipClaims v3 (by state)                       NfipPoliciesV3.parquet (~75M rows)
  NfipCommunityStatusBook v1                        │ download or browser + register
        │ paged, retried, reconciled                │ column contract, sha256, manifest
        ▼                                           ▼
bronze  data/lake/bronze/<dataset>/...  (API pages as pinned-schema Parquet; bulk file untouched)
        │
        ▼
silver  silver.nfip_claims                    one row per claim
        silver.community_status               one row per NFIP community (current snapshot)
        silver.policy_exposure_community_year earned exposure, premium, CRS class by year
        │
        ▼
gold    gold.community_year_claims            claims by community-year, 1978 onward
        gold.community_year_panel             the analysis panel, 2009 onward
        │
        ▼
ops     ops.run_log, ops.watermarks, ops.dq_results
```

Everything runs locally on DuckDB and Parquet. The layers map one to one onto a Fabric lakehouse (bronze files in OneLake, silver and gold as Delta tables) if ported later.

## Design decisions

**API for small sources, bulk file for policies.** Claims (2.7M rows) and the Status Book (25k) come through the API, where per-state partitions, server-side counts, and refresh metadata make loads verifiable and incremental. Policies (75M rows) would take about 7,500 API requests, so they come from FEMA's bulk Parquet file. fema.gov sometimes refuses scripted bulk downloads; the `download` command detects that and falls back to a browser download plus `register`.

**Bulk bronze is the untouched source file.** Rewriting 75M rows into our own Parquet would cost time and disk for no gain. Instead, `register` enforces the column contract, records row count and sha256 in a manifest, cross-checks the catalog record count, and moves the file into bronze. Re-registering an identical file is a no-op.

**Earned exposure, not a point-in-time count.** Policies in force on one date misstates exposure for policies that start or end mid-year. Each policy transaction is split across the calendar years its term overlaps, weighted by days covered, truncated at cancellation, and multiplied by `policyCount`. Premium is earned over the term the same way. This is standard actuarial earned-exposure accounting and gives the denominator for loss cost, frequency, and loss ratio.

**Exposure is the panel spine.** The panel starts from community-years with earned exposure and left-joins claims, so years with no claims appear as zeros. A claims-only panel silently drops good years, which biases every rate upward.

**CRS history rebuilt from Status Book dates, not policies.** The original design took class by year from `crsClassCode` on policies. The first national load showed that field is today's class stamped onto every historical record: no community ever changed class across 18 years, and 91% of community-years before a community joined CRS were stamped as CRS. History now comes from the Status Book: not in CRS before the entry date, the current class from its effective date onward, and explicitly unknown in between. Every panel row carries `crs_status_basis` saying which rule applied, and the policy stamp is kept as `crs_class_policy_stamp` so the discrepancy stays visible. Dates count toward a year only if they take effect by about mid-year, since ratings change on April 1 or October 1.

**Streaming aggregation for policies.** The exposure model parses, splits, and aggregates in one pass and never materializes the 75M-row grain. Benchmarked at 10M synthetic rows in 14 seconds on 2 cores with a 3 GB memory cap; DuckDB spills to `data/tmp` if needed.

**Partition-level full replace for API data.** FEMA revises historical claims after the fact. Replacing one state at a time is idempotent and avoids stale versions, which an id-based append would keep.

**Dataset-level watermark.** API loads skip a partition if it was already loaded from the current `lastDataSetRefresh`. Bulk loads skip if the file hash matches. `--force` overrides both.

**Stage, reconcile, then swap.** Each API partition is written to staging, its row count is compared to the API's server-side `$count`, and only then is it swapped in. A failed or mismatched load leaves the last good partition and watermark untouched.

**Pinned schema for API data.** Types are declared, not inferred, so an all-null page cannot change a column type and community numbers keep leading zeros. Upstream renames or type changes raise `SchemaDriftError` naming the field.

**Models declare their inputs.** Each SQL file starts with `-- requires:`. The build skips models whose inputs are not loaded yet, so sources can be added one at a time. Quality checks likewise skip tables that do not exist yet.

**Plain SQL instead of dbt (for now).** Five models run in order. If the graph grows, they port directly to dbt-duckdb, and the checks map to dbt tests.

## Data quirks found in the source

| Quirk | Handling |
| --- | --- |
| Claims v2 (`FimaNfipClaims`) and policies v2 retired 2026-10-15; v3 drops the FIMA prefix | Pipeline targets v3; `discover` checks the catalog before adding datasets |
| Policies start in 2009; claims go back to 1978 | Panel starts 2009; 2009 is warm-up because policies written in 2008 are missing |
| No key links a claim to a policy | Both are aggregated to community-year before joining |
| Policies use `propertyState`; claims use `state` | Mapped separately in each silver model |
| Policy and claims `crsClassCode` is the community's current class, back-stamped onto historical records (0 of 20,668 communities change class on policies; 0 of 1,667 on claims since 1995) | Not used for history; rebuilt from Status Book entry and effective dates |
| 125 claims have negative paid amounts (adjustments and recoveries, net -$86k) | Kept; warn-level check |
| Filtered sums over zero rows return NULL in SQL, so shares read as unknown instead of 0 | Coalesced to 0 (caught on first load) |
| `originalConstructionDate` = 1492-10-12 as an "unknown" placeholder | Parsed at microsecond precision; nulled in silver with a flag |
| `amountPaidOn*` is null on claims closed without payment | `paid_*` columns coalesce to 0 |
| `nfipCommunityNumberCurrent` often null on older records | Community key is `nfipRatedCommunityNumber` on both sides |
| Status Book map dates arrive as `MM/DD/YY` strings | Kept as strings; only CRS dates are parsed |
| Coordinates truncated to one decimal place | Geography comes from community ID and county FIPS only |

## Known limitations

- Status Book is a current snapshot. A community's class between CRS entry and its current class's effective date is unknown, and communities that entered then left CRS are marked `participation_unknown` after entry.
- Claims from the most recent year or two are still developing, so loss cost in those years is understated.
- A community spanning counties is assigned its exposure-weighted modal county.
- `$count` reconciliation assumes the count is stable during a pull; a refresh mid-run fails as a count mismatch and the previous partition is kept.
