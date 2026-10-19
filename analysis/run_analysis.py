"""Analyses A (pricing adequacy) and B (CRS entry, stacked DiD) on the exported panel.

    python analysis/run_analysis.py data/export/community_year_panel.parquet

Writes CSV tables to analysis/results/. Estimation is Poisson pseudo-maximum likelihood
(PPML) with log earned policy-years as the offset, so coefficients read as proportional
changes in loss cost (paid per policy-year) and zero-loss years are kept. Standard
errors are clustered by county, because communities in one county share the same storms.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyfixest as pf

YEARS = (2010, 2024)          # 2009 is warm-up; 2025-26 claims are still developing
KNOWN = ("not_in_crs", "current_class")
OUT = Path(__file__).parent / "results"


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Analysis sample from the gold panel (a DataFrame, so Databricks can pass a Spark table)."""
    df = df[(df.year >= YEARS[0]) & (df.year <= YEARS[1]) & df.county_fips.notna()].copy()
    df["paid_k"] = df.paid_total.astype(float).clip(lower=0) / 1000.0   # 5 community-years net negative
    df["log_epy"] = np.log(df.earned_policy_years.astype(float))
    df["sfha"] = df.share_exposure_sfha.astype(float).fillna(0)
    return df


def load(path: str) -> pd.DataFrame:
    return prepare(pd.read_parquet(path))


def drop_zero_groups(d: pd.DataFrame, outcome: str, groups: list[str]) -> pd.DataFrame:
    """Drop fixed-effect groups whose outcome is all zero. They carry no information in
    PPML and stall the demeaning; removing them changes no estimate."""
    while True:
        n = len(d)
        for g in groups:
            d = d[d.groupby(g)[outcome].transform("sum") > 0]
        if len(d) == n:
            return d


def ppml(fml: str, d: pd.DataFrame, outcome: str, groups: list[str]):
    d = drop_zero_groups(d, outcome, groups)
    return pf.fepois(fml, data=d, offset="log_epy", vcov={"CRV1": "county_fips"}, fixef_maxiter=100_000)


def pct(b: float) -> float:
    return 100 * (np.exp(b) - 1)


def tidy(fit, keep: str, label: str) -> pd.DataFrame:
    t = fit.tidy().reset_index()
    t = t[t.Coefficient.str.contains(keep, regex=False)].copy()
    t["model"] = label
    t["effect_pct"] = pct(t.Estimate)
    t["ci_low_pct"] = pct(t.Estimate - 1.96 * t["Std. Error"])
    t["ci_high_pct"] = pct(t.Estimate + 1.96 * t["Std. Error"])
    t["mde_pct"] = 100 * (1 - np.exp(-2.8 * t["Std. Error"]))   # 80% power, 5% two-sided
    return t[["model", "Coefficient", "Estimate", "Std. Error", "effect_pct", "ci_low_pct", "ci_high_pct",
              "mde_pct", "Pr(>|t|)"]]


# ---------------------------------------------------------------- A: pricing adequacy
def analysis_a(df: pd.DataFrame) -> pd.DataFrame:
    a = df[df.crs_status_basis.isin(KNOWN)].copy()
    a["crs_bin"] = np.where(a.crs_class <= 5, "1-5", a.crs_class.astype(int).astype(str))
    a["class_steps"] = 10 - a.crs_class
    out = []
    for outcome, label in (("paid_k", "loss_cost"), ("n_claims", "claim_frequency")):
        f1 = ppml(f"{outcome} ~ i(crs_bin, ref='10') + sfha | county_year", a, outcome, ["county_year"])
        out.append(tidy(f1, "crs_bin", f"A_{label}_by_class"))
        f2 = ppml(f"{outcome} ~ class_steps + sfha | county_year", a, outcome, ["county_year"])
        out.append(tidy(f2, "class_steps", f"A_{label}_per_class_step"))
    for regime in ("pre_rr2", "rr2"):
        f3 = ppml("paid_k ~ class_steps + sfha | county_year", a[a.regime == regime], "paid_k", ["county_year"])
        out.append(tidy(f3, "class_steps", f"A_loss_cost_per_class_step_{regime}"))
    res = pd.concat(out, ignore_index=True)
    # Discount the schedule gives for comparison: 5 points per class step; bin 1-5 shown at Class 5.
    disc = {"1-5": 25, "6": 20, "7": 15, "8": 10, "9": 5}
    res["scheduled_discount_pct"] = [
        -disc[c.split("::")[-1]] if "crs_bin" in c else (-5 if "class_steps" in c else None)
        for c in res.Coefficient]
    return res


# ---------------------------------------------------------------- B: entry, stacked DiD
def build_stacks(df: pd.DataFrame, cohorts=range(2012, 2021), pre=3, post=4) -> pd.DataFrame:
    never = (df.crs_entry_date.isna()) & (df.crs_class_current.fillna(10) == 10)
    stacks = []
    for g in cohorts:
        lo, hi = max(g - pre, YEARS[0]), min(g + post, YEARS[1])
        treated_ids = df.loc[(df.crs_entry_panel_year == g) & (df.crs_class_current.between(1, 9)),
                             "community_id"].unique()
        if len(treated_ids) == 0:
            continue
        counties = df.loc[df.community_id.isin(treated_ids), "county_fips"].unique()
        win = df[(df.year >= lo) & (df.year <= hi) & df.county_fips.isin(counties)]
        s = win[win.community_id.isin(treated_ids) | never.loc[win.index]].copy()
        s["cohort"] = g
        s["treated"] = s.community_id.isin(treated_ids).astype(int)
        s["rel"] = np.where(s.treated == 1, s.year - g, -99)       # -99 = control, no event time
        s["post"] = ((s.year >= g) & (s.treated == 1)).astype(int)
        stacks.append(s)
    st = pd.concat(stacks, ignore_index=True)
    st["unit"] = st.community_id + "_" + st.cohort.astype(str)
    st["cy_stack"] = st.county_year + "_" + st.cohort.astype(str)
    st["yr_stack"] = st.year.astype(str) + "_" + st.cohort.astype(str)
    return st


def collapse(st: pd.DataFrame) -> pd.DataFrame:
    """Sum entrants and controls separately within each county, stack, and year.

    Each county-stack-year then has one treated row and one control row, so county-by-year
    fixed effects compare entrants only with never-CRS neighbors hit by the same storms.
    (County-by-year FE on the community-level stack fail to converge in PPML: too many
    small cells.) Group fixed effects (county x stack x treated) replace community FE."""
    g = st.groupby(["county_fips", "cohort", "treated", "year"], as_index=False).agg(
        paid_k=("paid_k", "sum"), n_claims=("n_claims", "sum"),
        earned_policy_years=("earned_policy_years", "sum"), post=("post", "max"))
    g["log_epy"] = np.log(g.earned_policy_years)
    key = g.county_fips + "_" + g.cohort.astype(str)
    g["grp"] = key + "_" + g.treated.astype(str)
    g["cy"] = key + "_" + g.year.astype(str)
    g["rel"] = np.where(g.treated == 1, g.year - g.cohort, -99)
    return g


def analysis_b(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    st = build_stacks(df)
    cs = collapse(st)
    out = []
    for outcome, label in (("paid_k", "loss_cost"), ("n_claims", "claim_frequency")):
        # Primary: within county and year (collapsed). Placebo tests in run_design.py show
        # the year-FE-only version below is biased and its standard errors are too small.
        fit = ppml(f"{outcome} ~ post | grp + cy", cs, outcome, ["grp", "cy"])
        out.append(tidy(fit, "post", f"B_{label}_post_entry"))
        es = ppml(f"{outcome} ~ i(rel, ref=-1) | grp + cy", cs, outcome, ["grp", "cy"])
        e = tidy(es, "rel", f"B_{label}_event_study")
        out.append(e[~e.Coefficient.str.contains("-99")])
        # Original design, kept for comparison: community FE and stack-by-year FE only.
        fit = ppml(f"{outcome} ~ post | unit + yr_stack", st, outcome, ["unit", "yr_stack"])
        out.append(tidy(fit, "post", f"B_{label}_post_entry_year_fe_only"))
    # Pre-entry losses relative to same-county controls, which drive the regression-to-the-mean concern.
    pre = st[(st.rel >= -3) & (st.rel <= -1) | (st.rel == -99) & (st.year < st.cohort)]
    fit = ppml("paid_k ~ treated | cy_stack", pre, "paid_k", ["cy_stack"])
    out.append(tidy(fit, "treated", "B_pre_entry_loss_gap"))
    sizes = pd.DataFrame([{
        "cohorts": f"{st.cohort.min()}-{st.cohort.max()}",
        "treated_communities": st.loc[st.treated == 1, "community_id"].nunique(),
        "control_communities": st.loc[st.treated == 0, "community_id"].nunique(),
        "counties": st.county_fips.nunique(),
        "rows": len(st),
    }])
    return pd.concat(out, ignore_index=True), sizes


def main(path: str) -> None:
    OUT.mkdir(exist_ok=True)
    df = load(path)
    a = analysis_a(df)
    b, sizes = analysis_b(df)
    a.to_csv(OUT / "a_pricing_adequacy.csv", index=False)
    b.to_csv(OUT / "b_entry_did.csv", index=False)
    sizes.to_csv(OUT / "b_sample.csv", index=False)
    pd.set_option("display.width", 200, "display.max_columns", 20)
    cols = ["model", "Coefficient", "effect_pct", "ci_low_pct", "ci_high_pct", "mde_pct", "Pr(>|t|)"]
    print(a[cols + ["scheduled_discount_pct"]].round(2).to_string(index=False))
    print()
    print(sizes.to_string(index=False))
    print(b[cols].round(2).to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/export/community_year_panel.parquet")
