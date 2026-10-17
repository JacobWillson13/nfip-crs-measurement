"""Extended analyses on the exported panel: power and design (C), cohort ATTs (D),
pre-trend sensitivity (E), heterogeneity of the class-step association (F), and a
predictive loss-cost model (G).

    python analysis/run_extended.py data/export/community_year_panel.parquet [--reps 100]

Writes CSV tables to analysis/results/ and figures to analysis/figures/.
"""

from __future__ import annotations

import argparse
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pyfixest as pf

sys.path.insert(0, str(Path(__file__).parent))
from run_analysis import KNOWN, YEARS, build_stacks, drop_zero_groups, load, pct, tidy  # noqa: E402

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*multicollinearity.*")
OUT = Path(__file__).parent / "results"
FIG = Path(__file__).parent / "figures"
COHORTS = range(2012, 2021)
REGION = {  # Census regions
    "Northeast": "CT ME MA NH RI VT NJ NY PA",
    "Midwest": "IL IN MI OH WI IA KS MN MO NE ND SD",
    "South": "DE DC FL GA MD NC SC VA WV AL KY MS TN AR LA OK TX PR VI",
    "West": "AZ CO ID MT NV NM UT WY AK CA HI OR WA GU AS MP",
}
REGION = {s: r for r, ss in REGION.items() for s in ss.split()}
GULF_ATLANTIC = set("TX LA MS AL FL GA SC NC VA MD DE NJ NY CT RI MA NH ME PR".split())


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def add_capped(df: pd.DataFrame, q: float = 0.99) -> pd.DataFrame:
    lc = df.paid_k / df.earned_policy_years
    cap = lc[lc > 0].quantile(q)
    df["paid_k_cap"] = np.minimum(df.paid_k, cap * df.earned_policy_years)
    return df


def beta_se(fit, name: str) -> tuple[float, float]:
    t = fit.tidy()
    return float(t.loc[name, "Estimate"]), float(t.loc[name, "Std. Error"])


# ---------------------------------------------------------------- C: power and design
def placebo_stacks(never: pd.DataFrame, ids: np.ndarray, rng, n_treat: int, pre=3, post=4) -> pd.DataFrame:
    """Assign pseudo CRS entry to n_treat never-CRS communities and stack them, as in B,
    with the remaining never-CRS communities in their counties as controls."""
    pick = rng.choice(ids, n_treat, replace=False)
    cohort = pd.Series(rng.choice(list(COHORTS), n_treat), index=pick)
    stacks = []
    for g, tids in cohort.groupby(cohort).groups.items():
        lo, hi = max(g - pre, YEARS[0]), min(g + post, YEARS[1])
        tids = np.asarray(tids)
        counties = never.loc[never.community_id.isin(tids), "county_fips"].unique()
        win = never[(never.year >= lo) & (never.year <= hi) & never.county_fips.isin(counties)]
        s = win[win.community_id.isin(tids) | ~win.community_id.isin(pick)].copy()
        s["cohort"] = g
        s["treated"] = s.community_id.isin(tids).astype(int)
        s["post"] = ((s.year >= g) & (s.treated == 1)).astype(int)
        stacks.append(s)
    st = pd.concat(stacks, ignore_index=True)
    st["unit"] = st.community_id + "_" + st.cohort.astype(str)
    st["yr_stack"] = st.year.astype(str) + "_" + st.cohort.astype(str)
    return st


def fit_post(st: pd.DataFrame, outcome: str) -> tuple[float, float]:
    d = drop_zero_groups(st, outcome, ["unit", "yr_stack"])
    fit = pf.fepois(f"{outcome} ~ post | unit + yr_stack", data=d, offset="log_epy",
                    vcov={"CRV1": "county_fips"}, fixef_maxiter=100_000)
    return beta_se(fit, "post")


def analysis_c(df: pd.DataFrame, reps: int, seed: int = 20261016) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Placebo-based power. With no true effect, the spread of placebo estimates is the
    design's real sampling error, including catastrophe-year clustering that analytic
    SEs can miss. MDE (80% power, 5% two-sided) = 2.8 x that spread. A second pass injects
    a known 20% reduction to check that power at that size matches the MDE curve."""
    never = df[df.crs_entry_date.isna() & (df.crs_class_current.fillna(10) == 10)].copy()
    ids = never.community_id.unique()
    rng = np.random.default_rng(seed)
    designs = [("loss_cost", "paid_k"), ("loss_cost_capped_p99", "paid_k_cap"), ("claim_frequency", "n_claims")]
    sizes = [400, 1000, 2500]
    draws = []
    for n_treat in sizes:
        for r in range(reps):
            st = placebo_stacks(never, ids, rng, n_treat)
            for label, y in designs:
                b, se = fit_post(st, y)
                draws.append({"n_treated": n_treat, "rep": r, "design": label, "estimate": b, "se": se,
                              "injected": 0.0})
            # Injected 20% reduction in post-entry losses for treated units.
            inj = st.copy()
            m = inj.post == 1
            for y in ("paid_k", "paid_k_cap"):
                inj.loc[m, y] = inj.loc[m, y] * 0.8
            for label, y in designs[:2]:
                b, se = fit_post(inj, y)
                draws.append({"n_treated": n_treat, "rep": r, "design": label, "estimate": b, "se": se,
                              "injected": np.log(0.8)})
        log(f"C: n_treated={n_treat} done")
    dr = pd.DataFrame(draws)
    null = dr[dr.injected == 0]
    summ = null.groupby(["design", "n_treated"]).agg(
        placebo_sd=("estimate", "std"), mean_reported_se=("se", "mean"),
        false_positive_rate=("estimate", lambda s: np.nan)).reset_index()
    fp = null.assign(rej=lambda d: (d.estimate / d.se).abs() > 1.96).groupby(["design", "n_treated"]).rej.mean()
    summ["false_positive_rate"] = fp.values
    summ["mde_pct_empirical"] = 100 * (1 - np.exp(-2.8 * summ.placebo_sd))
    summ["mde_pct_reported_se"] = 100 * (1 - np.exp(-2.8 * summ.mean_reported_se))
    inj = dr[dr.injected != 0].assign(rej=lambda d: (d.estimate / d.se).abs() > 1.96)
    pw = inj.groupby(["design", "n_treated"]).agg(power_at_20pct=("rej", "mean"),
                                                  mean_est_pct=("estimate", lambda s: pct(s.mean()))).reset_index()
    summ = summ.merge(pw, on=["design", "n_treated"], how="left")
    # Treated count needed for a 20% MDE, from sd ~ k / sqrt(n).
    k = (summ.placebo_sd * np.sqrt(summ.n_treated)).groupby(summ.design).mean()
    need = (2.8 * k / -np.log(0.8)) ** 2
    summ["n_needed_for_20pct_mde"] = summ.design.map(need).round(-1)
    return summ, dr


# ---------------------------------------------------------------- D: cohort ATTs
def analysis_d(df: pd.DataFrame) -> pd.DataFrame:
    """ATT by entry cohort from one stacked PPML with cohort x post terms, so the county-
    clustered covariance spans cohorts. Aggregated with treated-community weights, in the
    spirit of Callaway and Sant'Anna's ATT(g) aggregation. The stacked design already
    uses only clean (never-CRS) controls, so this mainly shows heterogeneity by cohort."""
    st = build_stacks(df)
    out = []
    for outcome, label in (("paid_k", "loss_cost"), ("n_claims", "claim_frequency")):
        d = drop_zero_groups(st, outcome, ["unit", "yr_stack"])
        fit = pf.fepois(f"{outcome} ~ i(cohort, post) | unit + yr_stack", data=d, offset="log_epy",
                        vcov={"CRV1": "county_fips"}, fixef_maxiter=100_000)
        t = fit.tidy()
        names = [n for n in t.index if "post" in n]
        V = pd.DataFrame(fit._vcov, index=fit._coefnames, columns=fit._coefnames).loc[names, names]
        n_tr = d[d.treated == 1].groupby("cohort").community_id.nunique()
        coh = [int(n.split("::")[1].split(":")[0]) for n in names]
        w = np.array([n_tr.get(c, 0) for c in coh], float)
        w /= w.sum()
        b = t.loc[names, "Estimate"].values
        se = t.loc[names, "Std. Error"].values
        for c, bi, si, n_i in zip(coh, b, se, [n_tr.get(c, 0) for c in coh], strict=True):
            out.append({"model": f"D_{label}_att_by_cohort", "cohort": c, "treated": n_i,
                        "effect_pct": pct(bi), "ci_low_pct": pct(bi - 1.96 * si), "ci_high_pct": pct(bi + 1.96 * si)})
        agg, agg_se = float(w @ b), float(np.sqrt(w @ V.values @ w))
        out.append({"model": f"D_{label}_att_aggregate", "cohort": "all", "treated": int(n_tr.sum()),
                    "effect_pct": pct(agg), "ci_low_pct": pct(agg - 1.96 * agg_se),
                    "ci_high_pct": pct(agg + 1.96 * agg_se)})
    return pd.DataFrame(out)


# ---------------------------------------------------------------- E: pre-trend sensitivity
def analysis_e(df: pd.DataFrame) -> pd.DataFrame:
    """Joint test of the pre-entry leads, and a relative-magnitudes bound in the spirit of
    Rambachan and Roth (2023): allow the post-period violation of parallel trends to be up
    to Mbar times the largest pre-period change, and widen the CI on the average post
    effect by that bias. A simplified version, not their full conditional inference."""
    st = build_stacks(df)
    out = []
    for outcome, label in (("paid_k", "loss_cost"), ("n_claims", "claim_frequency")):
        d = drop_zero_groups(st, outcome, ["unit", "yr_stack"])
        es = pf.fepois(f"{outcome} ~ i(rel, ref=-1) | unit + yr_stack", data=d, offset="log_epy",
                       vcov={"CRV1": "county_fips"}, fixef_maxiter=100_000)
        t = es.tidy()
        V = pd.DataFrame(es._vcov, index=es._coefnames, columns=es._coefnames)
        rel = {n: int(n.split("::")[1]) for n in t.index if n.startswith("rel::") and "-99" not in n}
        leads = [n for n, r in rel.items() if r < -1]
        lags = [n for n, r in rel.items() if r >= 0]
        bl = t.loc[leads, "Estimate"].values
        wald = float(bl @ np.linalg.solve(V.loc[leads, leads].values, bl))
        from scipy.stats import chi2
        p_pre = float(chi2.sf(wald, len(leads)))
        w = np.ones(len(lags)) / len(lags)
        theta = float(w @ t.loc[lags, "Estimate"].values)
        se = float(np.sqrt(w @ V.loc[lags, lags].values @ w))
        pre_b = np.array([0.0] + [t.loc[n, "Estimate"] for n in sorted(leads, key=rel.get, reverse=True)])
        max_step = float(np.max(np.abs(np.diff(pre_b)))) if len(pre_b) > 1 else 0.0
        for mbar in (0, 0.5, 1.0):
            bias = mbar * max_step
            out.append({"model": f"E_{label}", "pre_trend_wald_p": p_pre, "max_pre_step_pct": pct(max_step),
                        "mbar": mbar, "avg_post_effect_pct": pct(theta),
                        "ci_low_pct": pct(theta - bias - 1.96 * se), "ci_high_pct": pct(theta + bias + 1.96 * se)})
    return pd.DataFrame(out)


# ---------------------------------------------------------------- F: heterogeneity
def analysis_f(df: pd.DataFrame) -> pd.DataFrame:
    """Does the loss-cost gap per CRS class step differ by SFHA exposure, region, or coast?
    Same PPML and county-by-year fixed effects as A, with class_steps interacted by group.
    A causal forest or DML would need to absorb ~5,000 county-year effects nonparametrically
    and would be dominated by catastrophe outliers; interactions keep A's identification."""
    a = df[df.crs_status_basis.isin(KNOWN)].copy()
    a["class_steps"] = 10 - a.crs_class
    a["sfha_tercile"] = pd.qcut(a.sfha.rank(method="first"), 3, labels=["low", "mid", "high"]).astype(str)
    a["region"] = a.state.map(REGION).fillna("Other")
    a["coast"] = np.where(a.state.isin(GULF_ATLANTIC), "gulf_atlantic_state", "other_state")
    out = []
    for g in ("sfha_tercile", "region", "coast"):
        d = drop_zero_groups(a, "paid_k", ["county_year"])
        fit = pf.fepois(f"paid_k ~ i({g}, class_steps) + sfha | county_year", data=d, offset="log_epy",
                        vcov={"CRV1": "county_fips"}, fixef_maxiter=100_000)
        t = tidy(fit, "class_steps", f"F_loss_cost_per_step_by_{g}")
        crs_cy = d[d.crs_class < 10].groupby(g).county_year.nunique()
        t["group"] = t.Coefficient.str.split("::").str[1].str.split(":").str[0]
        t["crs_county_years"] = t.group.map(crs_cy)
        out.append(t)
    return pd.concat(out, ignore_index=True)


# ---------------------------------------------------------------- G: predictive model
def features(path: str) -> pd.DataFrame:
    raw = pd.read_parquet(path)
    raw = raw[raw.county_fips.notna()].sort_values(["community_id", "year"]).copy()
    raw["paid_k"] = raw.paid_total.clip(lower=0) / 1000.0
    lc = raw.paid_k / raw.earned_policy_years
    fq = raw.n_claims / raw.earned_policy_years
    raw["lag1_lc"] = lc.groupby(raw.community_id).shift(1)
    raw["lag3_lc"] = lc.groupby(raw.community_id).transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean())
    raw["lag3_freq"] = fq.groupby(raw.community_id).transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean())
    raw["lag3_epy"] = raw.earned_policy_years.groupby(raw.community_id).transform(
        lambda s: s.shift(1).rolling(3, min_periods=1).sum())
    raw["log_prem_rate"] = np.log((raw.earned_premium / raw.earned_policy_years).clip(lower=1))
    raw["log_cov_rate"] = np.log((raw.earned_coverage / raw.earned_policy_years).clip(lower=1))
    raw["log_size"] = np.log(raw.earned_policy_years)
    raw["log_epy"] = raw.log_size
    raw["sfha"] = raw.share_exposure_sfha.fillna(0)
    raw["class_steps"] = np.where(raw.crs_class.notna(), 10 - raw.crs_class, np.nan)
    raw["in_crs_num"] = raw.in_crs.astype(float) if "in_crs" in raw else (raw.crs_class < 10).astype(float)
    return raw[(raw.year >= 2012) & (raw.year <= YEARS[1])].copy()


def tweedie_deviance(y, mu, p=1.5):
    mu = np.clip(mu, 1e-9, None)
    t1 = np.where(y > 0, y ** (2 - p) / ((1 - p) * (2 - p)), 0)
    return float(np.sum(2 * (t1 - y * mu ** (1 - p) / (1 - p) + mu ** (2 - p) / (2 - p))))


def gini(y, rate, w):
    """Exposure-weighted normalized Gini: how well a predicted rate ranks actual losses.
    0 = no better than random, 1 = the ordering by actual loss cost."""
    def g(order):
        o = np.argsort(-order, kind="stable")
        cw = np.concatenate([[0], np.cumsum(w[o]) / w.sum()])
        cy = np.concatenate([[0], np.cumsum(y[o]) / y.sum()])
        return 2 * np.trapezoid(cy, cw) - 1
    return float(g(rate) / g(y / w))


def analysis_g(path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Out-of-time loss-cost model. Train 2012-2019, validate 2020-2021, test 2022-2024
    (the Risk Rating 2.0 years, so the premium feature shifts meaning, as it would in
    production). Compared against FEMA's own premium as a ranking of risk."""
    import lightgbm as lgb
    import statsmodels.api as sm

    d = features(path)
    d["state_c"] = d.state.astype("category")
    num = ["lag1_lc", "lag3_lc", "lag3_freq", "lag3_epy", "log_prem_rate", "log_cov_rate", "log_size", "sfha",
           "class_steps", "in_crs_num", "year"]
    tr, va, te = d[d.year <= 2019], d[d.year.between(2020, 2021)], d[d.year >= 2022]

    # GLM: Tweedie (p=1.5), log link, exposure offset. Missing lags/class handled with indicators.
    def glm_X(x: pd.DataFrame) -> pd.DataFrame:
        X = pd.DataFrame(index=x.index)
        for c in ["lag3_lc", "lag3_freq"]:
            X[f"log1p_{c}"] = np.log1p(x[c].fillna(0) * 1000)
            X[f"miss_{c}"] = x[c].isna().astype(float)
        X["log_prem_rate"] = x.log_prem_rate
        X["log_size"] = x.log_size
        X["sfha"] = x.sfha
        X["class_steps"] = x.class_steps.fillna(0)
        X["class_unknown"] = x.class_steps.isna().astype(float)
        X = X.join(pd.get_dummies(x.state, prefix="st", drop_first=True, dtype=float))
        return sm.add_constant(X, has_constant="add")

    Xtr = glm_X(tr)
    cols = Xtr.columns
    glm = sm.GLM(tr.paid_k, Xtr, family=sm.families.Tweedie(var_power=1.5, link=sm.families.links.Log()),
                 offset=tr.log_epy).fit(maxiter=200, cov_type="cluster",
                                         cov_kwds={"groups": pd.factorize(tr.county_fips)[0]})
    def glm_pred(x):
        return glm.predict(glm_X(x).reindex(columns=cols, fill_value=0.0), offset=x.log_epy)

    params = dict(objective="tweedie", tweedie_variance_power=1.5, learning_rate=0.03, num_leaves=31,
                  min_data_in_leaf=200, feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
                  lambda_l2=10, verbose=-1, seed=7)
    feats = num + ["state_c"]
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    dtr = lgb.Dataset(tr[feats], tr.paid_k, init_score=tr.log_epy, categorical_feature=["state_c"])
    dva = lgb.Dataset(va[feats], va.paid_k, init_score=va.log_epy, reference=dtr)
    gbm = lgb.train(params, dtr, 3000, valid_sets=[dva], callbacks=[lgb.early_stopping(100, verbose=False)])
    def gbm_pred(x):
        return np.exp(gbm.predict(x[feats], raw_score=True) + x.log_epy.values)

    rows = []
    base_rate = tr.paid_k.sum() / tr.earned_policy_years.sum()
    lr = tr.paid_k.sum() / (tr.earned_premium.sum() / 1000)
    for split, x in (("validate_2020_2021", va), ("test_2022_2024", te)):
        y, w = x.paid_k.values, x.earned_policy_years.values
        preds = {
            "exposure_only": base_rate * w,
            "fema_premium_x_loss_ratio": lr * x.earned_premium.values / 1000,
            "tweedie_glm": glm_pred(x).values,
            "lightgbm_tweedie": gbm_pred(x),
        }
        for m, mu in preds.items():
            rows.append({"split": split, "model": m, "tweedie_deviance": tweedie_deviance(y, mu),
                         "gini": gini(y, mu / w, w), "actual_over_predicted": y.sum() / mu.sum(),
                         "top_decile_lift": top_decile_lift(y, mu, w)})
    res = pd.DataFrame(rows)
    base = res[res.model == "exposure_only"].set_index("split").tweedie_deviance
    res["deviance_explained_pct"] = 100 * (1 - res.tweedie_deviance / res.split.map(base))

    # Implied CRS relativity per class step, model vs schedule.
    b, se = glm.params["class_steps"], glm.bse["class_steps"]
    rel = [{"model": "tweedie_glm", "metric": "loss_cost_per_class_step", "effect_pct": pct(b),
            "ci_low_pct": pct(b - 1.96 * se), "ci_high_pct": pct(b + 1.96 * se), "scheduled_discount_pct": -5}]
    known = te[te.class_steps.notna()].copy()
    pdp = []
    for s in range(0, 6):
        z = known.assign(class_steps=float(s), in_crs_num=float(s > 0))
        pdp.append(np.log(gbm_pred(z).sum()))
    pdp = np.array(pdp)
    slope = np.polyfit(np.arange(6), pdp, 1)[0]
    rel.append({"model": "lightgbm_tweedie", "metric": "loss_cost_per_class_step_pdp", "effect_pct": pct(slope),
                "ci_low_pct": np.nan, "ci_high_pct": np.nan, "scheduled_discount_pct": -5})
    imp = pd.Series(gbm.feature_importance("gain"), index=feats).sort_values(ascending=False)
    res.attrs["importance"] = imp
    return res, pd.DataFrame(rel), imp


def top_decile_lift(y, mu, w):
    rate = mu / w
    o = np.argsort(-rate)
    cw = np.cumsum(w[o]) / w.sum()
    top = o[cw <= 0.1]
    return float((y[top].sum() / w[top].sum()) / (y.sum() / w.sum()))


# ---------------------------------------------------------------- figures
def figures(c: pd.DataFrame, e_es: pd.DataFrame | None) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    FIG.mkdir(exist_ok=True)
    fig, ax = plt.subplots(figsize=(6.5, 4))
    for design, g in c.groupby("design"):
        ax.plot(g.n_treated, g.mde_pct_empirical, marker="o", label=design.replace("_", " "))
    ax.axhline(20, color="grey", ls=":", lw=1)
    ax.axvline(395, color="grey", ls="--", lw=1)
    ax.text(410, ax.get_ylim()[1] * 0.92, "actual entrants (395)", fontsize=8, color="grey")
    ax.set_xlabel("CRS entrants in the design")
    ax.set_ylabel("Minimum detectable reduction (%)")
    ax.set_title("What it would take to measure the CRS entry effect")
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG / "c_power_mde.png", dpi=160)
    plt.close(fig)
    if e_es is not None:
        fig, ax = plt.subplots(figsize=(6.5, 4))
        ax.errorbar(e_es.rel, e_es.effect_pct, yerr=[e_es.effect_pct - e_es.ci_low_pct,
                    e_es.ci_high_pct - e_es.effect_pct], fmt="o", capsize=3)
        ax.axhline(0, color="grey", lw=1)
        ax.axvline(-0.5, color="grey", ls="--", lw=1)
        ax.set_xlabel("Years relative to CRS entry")
        ax.set_ylabel("Loss cost vs same-county controls (%)")
        ax.set_title("Event study: CRS entry (stacked PPML, 95% CI)")
        ax.set_yscale("symlog", linthresh=100)
        fig.tight_layout()
        fig.savefig(FIG / "b_event_study.png", dpi=160)
        plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("panel", nargs="?", default="data/export/community_year_panel.parquet")
    ap.add_argument("--reps", type=int, default=100)
    ap.add_argument("--only", default="CDEFG")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    pd.set_option("display.width", 220, "display.max_columns", 20)
    df = add_capped(load(args.panel))
    if "F" in args.only:
        f = analysis_f(df)
        f.to_csv(OUT / "f_heterogeneity.csv", index=False)
        print(f[["model", "group", "crs_county_years", "effect_pct", "ci_low_pct", "ci_high_pct"]].round(1)
              .to_string(index=False), "\n")
        log("F done")
    if "D" in args.only:
        d = analysis_d(df)
        d.to_csv(OUT / "d_cohort_att.csv", index=False)
        print(d.round(1).to_string(index=False), "\n")
        log("D done")
    if "E" in args.only:
        e = analysis_e(df)
        e.to_csv(OUT / "e_pretrend_sensitivity.csv", index=False)
        print(e.round(3).to_string(index=False), "\n")
        log("E done")
    if "G" in args.only:
        g, rel, imp = analysis_g(args.panel)
        g.to_csv(OUT / "g_model_performance.csv", index=False)
        rel.to_csv(OUT / "g_implied_crs_relativity.csv", index=False)
        imp.rename("gain").to_csv(OUT / "g_feature_importance.csv")
        print(g.round(3).to_string(index=False), "\n", rel.round(1).to_string(index=False), "\n",
              (imp / imp.sum()).round(3).to_string(), "\n")
        log("G done")
    if "C" in args.only:
        c, draws = analysis_c(df, args.reps)
        c.to_csv(OUT / "c_power_design.csv", index=False)
        draws.to_csv(OUT / "c_power_draws.csv", index=False)
        print(c.round(3).to_string(index=False), "\n")
        b = pd.read_csv(OUT / "b_entry_did.csv")
        es = b[b.model == "B_loss_cost_event_study"].copy()
        es["rel"] = es.Coefficient.str.split("::").str[1].astype(int)
        es = pd.concat([es, pd.DataFrame([{"rel": -1, "effect_pct": 0, "ci_low_pct": 0, "ci_high_pct": 0}])])
        figures(c, es.sort_values("rel"))
        log("C done")


if __name__ == "__main__":
    main()
