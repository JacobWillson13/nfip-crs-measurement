# Power BI Report

A three-page report on the panel, built in the Power BI service (browser), so it works from Linux. Power BI Desktop is Windows-only.

## Inputs

`python -m pipeline.cli export` writes these to `data/export/bi/`:

| File | Rows | Use |
| --- | --- | --- |
| `community_year.csv` | ~280K | Community-year panel, 2010 to 2024 |
| `dq_results.csv` | ~25 | Latest quality check results |
| `run_log.csv` | ~60 | Every load attempt |

Then `python dashboard/build_workbook.py` bundles them, plus the class effects from `analysis/results/`, into one workbook, `data/export/nfip_crs_report.xlsx` (about 22 MB). Upload that single file so one report can hold all three pages.

## Pages

1. **Losses over time:** paid claims and claims per 1,000 policy-years by year, sliced by state. Shows catastrophe years (2012, 2017, 2024).
2. **Loss cost by CRS class:** the `class_effects` sheet: estimated effect by class next to the scheduled discount, with the 95% interval.
3. **Pipeline health:** quality checks by severity and pass or fail, plus load attempts by status.

## Files in this folder

- `report.pbix` or a share link, once built
- `screenshots/` with one PNG per page, linked from the main README
