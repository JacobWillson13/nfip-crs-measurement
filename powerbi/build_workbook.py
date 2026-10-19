"""Bundle the Power BI inputs into one Excel workbook (one sheet per table).

The Power BI service builds one semantic model per uploaded file, and a report uses
one model. A single workbook lets one report hold all three pages.

    python powerbi/build_workbook.py
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BI = ROOT / "data" / "export" / "bi"
OUT = ROOT / "data" / "export" / "nfip_crs_report.xlsx"


def main() -> None:
    results = pd.read_csv(ROOT / "analysis" / "results" / "a_pricing_adequacy.csv")
    by_class = results[results.model == "A_loss_cost_by_class"].assign(
        crs_class=lambda d: d.Coefficient.str.split("::").str[-1])
    sheets = {
        "community_year": pd.read_csv(BI / "community_year.csv", dtype={"community_id": str, "county_fips": str}),
        "class_effects": by_class[["crs_class", "effect_pct", "ci_low_pct", "ci_high_pct",
                                   "scheduled_discount_pct"]].round(1),
        "dq_results": pd.read_csv(BI / "dq_results.csv"),
        "run_log": pd.read_csv(BI / "run_log.csv"),
    }
    with pd.ExcelWriter(OUT, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False)
            print(f"{name}: {len(df):,} rows")
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
