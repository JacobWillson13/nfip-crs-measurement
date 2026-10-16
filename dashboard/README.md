# Power BI Prototype

Report on `gold.community_year_claims` for exploring claims by CRS class, state, and year before the formal analysis.

## Connecting

Power BI Desktop reads the gold layer most simply as Parquet. Export it with:

```bash
duckdb data/warehouse.duckdb "copy gold.community_year_claims to 'dashboard/community_year_claims.parquet' (format parquet)"
```

Then in Power BI Desktop: Get Data, Parquet, select the exported file.

## Planned pages

1. Paid claims over time by state, with major flood events annotated.
2. Average paid per paid claim by modal CRS class.
3. Data quality page from `ops.dq_results` and `ops.run_log`.

Add the `.pbix` and screenshots here once built.
