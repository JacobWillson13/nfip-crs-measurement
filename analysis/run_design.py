"""Design checks by placebo simulation.

    python analysis/run_design.py data/export/community_year_panel.parquet [--reps-b 150] [--reps-a 100]

B (CRS entry). Assign fake CRS entry to never-CRS communities, stack them exactly as the
real analysis does, and estimate the post-entry effect under two designs:
  * year_fe_only:  community FE + stack-by-year FE (the original design)
  * within_county: entrant and control totals per county-stack-year, with county-by-year FE
                   (the design now used in run_analysis.py)
With no true effect, the spread of placebo estimates is each design's real sampling error,
its mean is its bias, and the share of placebos with |t| > 1.96 is its false-positive rate.
A second pass multiplies post-entry losses of the fake entrants by 0.8 to measure power
against a 20% reduction. The real estimates get a randomization-inference p-value from
the placebo draws at the actual number of entrants.

A (pricing adequacy). Reassign CRS classes among communities within each county-year,
holding each community's random rank fixed across years so a placebo "class" persists
like a real one, and re-estimate the per-class-step loss-cost effect. Checks whether A's
clustered standard errors match its real sampling error.

Writes analysis/results/c_*.csv and analysis/figures/c_power_mde.png.
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
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).parent))
from run_analysis import KNOWN, YEARS, build_stacks, collapse, drop_zero_groups, load  # noqa: E402

warnings.filterwarnings("ignore")
OUT = Path(__file__).parent / "results"
FIG = Path(__file__).parent / "figures"
COHORTS = range(2012, 2021)
DESIGNS = {"year_fe_only": ("unit + yr_stack", False), "within_county": ("grp + cy", True)}
OUTCOMES = {"paid_k": "loss_cost", "n_claims": "claim_frequency"}
INJECT = np.log(0.8)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def fit_post(d: pd.DataFrame, outcome: str, fe: str) -> tuple[float, float]:
    d = drop_zero_groups(d, outcome, fe.replace(" ", "").split("+"))
    try:
        f = pf.fepois(f"{outcome} ~ post | {fe}", data=d, offset="log_epy", vcov={"CRV1": "county_fips"},
                      fixef_maxiter=100_000)
        return float(f.coef()["post"]), float(f.se()["post"])
    except Exception:  # noqa: BLE001  non-convergence is recorded, not fatal
        return np.nan, np.nan


def all_designs(st: pd.DataFrame, outcomes=tuple(OUTCOMES)) -> dict:
    cs = collapse(st)
    return {(name, y): fit_post(cs if collapsed else st, y, fe)
            for name, (fe, collapsed) in DESIGNS.items() for y in outcomes}


def placebo_stacks(never: pd.DataFrame, ids: np.ndarray, rng, n_treat: int, pre=3, post=4) -> pd.DataFrame:
    """Give n_treat random never-CRS communities a fake entry year and stack them as in B,
    with the other never-CRS communities in their counties as controls."""
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


def design_b(df: pd.DataFrame, reps: int, sizes: list[int], seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    never = df[df.crs_entry_date.isna() & (df.crs_class_current.fillna(10) == 10)].copy()
    ids = never.community_id.unique()
    rng = np.random.default_rng(seed)
    rows = []
    for n in sizes:
        for r in range(reps):
            st = placebo_stacks(never, ids, rng, n)
            for (design, y), (b, se) in all_designs(st).items():
                rows.append(dict(n_entrants=n, rep=r, design=design, outcome=OUTCOMES[y], injected=0.0, b=b, se=se))
            inj = st.astype({"n_claims": float})
            inj.loc[inj.post == 1, ["paid_k", "n_claims"]] *= 0.8   # expected claims fall 20% too
            for (design, y), (b, se) in all_designs(inj).items():
                rows.append(dict(n_entrants=n, rep=r, design=design, outcome=OUTCOMES[y], injected=INJECT, b=b, se=se))
        log(f"B placebo: {n} entrants done")
    draws = pd.DataFrame(rows)

    null, inj = draws[draws.injected == 0].dropna(), draws[draws.injected != 0].dropna()
    keys = ["design", "outcome", "n_entrants"]
    s = null.groupby(keys).agg(reps=("b", "size"), placebo_mean=("b", "mean"), placebo_sd=("b", "std"),
                               median_reported_se=("se", "median")).reset_index()
    s["placebo_mean_pct"] = 100 * (np.exp(s.placebo_mean) - 1)
    s["false_positive_rate"] = null.assign(x=(null.b / null.se).abs() > 1.96).groupby(keys).x.mean().values
    s["mde_pct"] = 100 * (1 - np.exp(-2.8 * s.placebo_sd))
    s["mde_pct_if_reported_se_were_right"] = 100 * (1 - np.exp(-2.8 * s.median_reported_se))
    # Power against a 20% reduction, judged against the placebo distribution (randomization
    # inference), so it is not inflated by understated SEs or by bias.
    crit = null.groupby(keys).b.quantile(0.025).rename("crit")
    pw = inj.join(crit, on=keys).assign(x=lambda d: d.b < d.crit).groupby(keys).x.mean().rename("power_at_20pct")
    s = s.join(pw, on=keys)
    s["n_failed_fits"] = draws[draws.injected == 0].groupby(keys).b.apply(lambda v: v.isna().sum()).values
    return s, draws


def ri_pvalues(df: pd.DataFrame, draws: pd.DataFrame, n_actual: int) -> pd.DataFrame:
    """Real B estimates with p-values from the placebo distribution at the nearest size."""
    real = all_designs(build_stacks(df))
    null = draws[(draws.injected == 0) & (draws.n_entrants == n_actual)].dropna()
    rows = []
    for (design, y), (b, se) in real.items():
        nb = null[(null.design == design) & (null.outcome == OUTCOMES[y])].b
        centered = b - nb.mean()
        rows.append(dict(design=design, outcome=OUTCOMES[y], estimate_pct=100 * (np.exp(b) - 1),
                         analytic_p=float(2 * norm.sf(abs(b / se))),
                         bias_corrected_pct=100 * (np.exp(centered) - 1),
                         ri_p=float(((nb - nb.mean()).abs() >= abs(centered)).mean()), placebo_reps=len(nb)))
    return pd.DataFrame(rows)


def design_a(df: pd.DataFrame, reps: int, seed: int) -> pd.DataFrame:
    a = df[df.crs_status_basis.isin(KNOWN)].copy()
    a["class_steps"] = 10 - a.crs_class
    data = {y: drop_zero_groups(a, y, ["county_year"]) for y in OUTCOMES}

    def fit(d: pd.DataFrame, y: str) -> tuple[float, float]:
        try:
            f = pf.fepois(f"{y} ~ class_steps + sfha | county_year", data=d, offset="log_epy",
                          vcov={"CRV1": "county_fips"}, fixef_maxiter=100_000)
            return float(f.coef()["class_steps"]), float(f.se()["class_steps"])
        except Exception:  # noqa: BLE001
            return np.nan, np.nan

    rows = [dict(outcome=OUTCOMES[y], rep=-1, **dict(zip(("b", "se"), fit(d, y), strict=True)))
            for y, d in data.items()]
    # Each cell's classes, best first; communities in a cell, ordered by a random rank that is
    # fixed per community. Pairing the two keeps each cell's class mix and lets a placebo
    # class persist over years like a real one.
    classes = {y: d.sort_values(["county_year", "crs_class"], kind="stable").crs_class.values for y, d in data.items()}
    comms = a.community_id.unique()
    rng = np.random.default_rng(seed)
    for r in range(reps):
        u = pd.Series(rng.random(len(comms)), index=comms)
        for y, d in data.items():
            p = d.assign(u=d.community_id.map(u)).sort_values(["county_year", "u"], kind="stable")
            p["class_steps"] = 10 - classes[y]
            b, se = fit(p, y)
            rows.append(dict(outcome=OUTCOMES[y], rep=r, b=b, se=se))
        if (r + 1) % 25 == 0:
            log(f"A placebo: {r + 1}/{reps}")
    return pd.DataFrame(rows)


def summarize_a(d: pd.DataFrame) -> pd.DataFrame:
    """Inference on t-statistics, not raw coefficients: placebo classes land on random
    communities, which are smaller than real CRS communities, so placebo coefficients are
    noisier than the real one. The placebo t-distribution calibrates the critical value."""
    out = []
    for outcome, g in d.groupby("outcome"):
        real, null = g[g.rep == -1].iloc[0], g[g.rep >= 0].dropna()
        t, t_real = null.b / null.se, real.b / real.se
        q = float(t.abs().quantile(0.95))
        out.append({
            "outcome": outcome, "estimate_pct": 100 * (np.exp(real.b) - 1), "t_stat": t_real,
            "analytic_ci_low_pct": 100 * (np.exp(real.b - 1.96 * real.se) - 1),
            "analytic_ci_high_pct": 100 * (np.exp(real.b + 1.96 * real.se) - 1),
            "placebo_reps": len(null), "placebo_mean_pct": 100 * (np.exp(null.b.mean()) - 1),
            "placebo_t_sd": t.std(), "false_positive_rate": float((t.abs() > 1.96).mean()),
            "placebo_critical_value": q, "ri_p": float((t.abs() >= abs(t_real)).mean()),
            "placebo_ci_low_pct": 100 * (np.exp(real.b - q * real.se) - 1),
            "placebo_ci_high_pct": 100 * (np.exp(real.b + q * real.se) - 1),
        })
    return pd.DataFrame(out)


def figure(s: pd.DataFrame, n_actual: int) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ink, ink2, grid, surface = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
    style = {"within_county": ("#2a78d6", "-", "o", "Within county (current)"),
             "year_fe_only": ("#eb6834", "--", "s", "Year FE only (original)")}
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), sharey=True, facecolor=surface)
    for ax, outcome, title in zip(axes, ["claim_frequency", "loss_cost"], ["Claim frequency", "Loss cost"],
                                  strict=True):
        ax.set_facecolor(surface)
        for design, (color, ls, marker, label) in style.items():
            g = s[(s.design == design) & (s.outcome == outcome)].sort_values("n_entrants")
            ax.plot(g.n_entrants, g.mde_pct, color=color, ls=ls, lw=2, marker=marker, ms=8,
                    markeredgecolor=surface, markeredgewidth=2, label=label)
            ax.annotate(f"{g.mde_pct.iloc[-1]:.0f}%", (g.n_entrants.iloc[-1], g.mde_pct.iloc[-1]),
                        xytext=(8, 0), textcoords="offset points", va="center", fontsize=9, color=ink2)
        ax.axhline(20, color=ink2, ls=":", lw=1)
        ax.text(1000, 21.5, "20% reduction", fontsize=8, color=ink2, ha="center")
        ax.axvline(n_actual, color=grid, lw=1.5, zorder=0)
        ax.text(n_actual * 1.04, 4, f"actual entrants ({n_actual})", fontsize=8, color=ink2)
        ax.set_xscale("log")
        ax.set_xticks(sorted(s.n_entrants.unique()))
        ax.set_xticklabels([f"{n:,}" for n in sorted(s.n_entrants.unique())])
        ax.minorticks_off()
        ax.set_xlim(s.n_entrants.min() * 0.85, s.n_entrants.max() * 1.35)
        ax.set_ylim(0, 100)
        ax.set_title(title, loc="left", fontsize=11, color=ink)
        ax.set_xlabel("CRS entrants in the design", color=ink2)
        ax.grid(axis="y", color=grid, lw=0.8)
        ax.tick_params(colors=ink2)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color(grid)
    axes[0].set_ylabel("Minimum detectable reduction (%)\n80% power, from placebo spread", color=ink2)
    axes[0].legend(frameon=False, fontsize=9, loc="upper right")
    fig.suptitle("Smallest CRS entry effect each design can detect", x=0.06, ha="left", fontsize=13, color=ink)
    fig.tight_layout()
    FIG.mkdir(exist_ok=True)
    fig.savefig(FIG / "c_power_mde.png", dpi=160, facecolor=surface)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("panel", nargs="?", default="data/export/community_year_panel.parquet")
    ap.add_argument("--reps-b", type=int, default=150)
    ap.add_argument("--reps-a", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20261017)
    ap.add_argument("--only", default="AB")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    pd.set_option("display.width", 220, "display.max_columns", 20)
    df = load(args.panel)
    if "B" in args.only:
        n_actual = int(build_stacks(df).query("treated == 1").community_id.nunique())
        s, draws = design_b(df, args.reps_b, [n_actual, 1000, 2500], args.seed)
        ri = ri_pvalues(df, draws, n_actual)
        s.to_csv(OUT / "c_power_design.csv", index=False)
        draws.to_csv(OUT / "c_power_draws.csv", index=False)
        ri.to_csv(OUT / "c_entry_randomization_inference.csv", index=False)
        figure(s, n_actual)
        print(s.round(3).to_string(index=False), "\n\n", ri.round(3).to_string(index=False), "\n")
    if "A" in args.only:
        d = design_a(df, args.reps_a, args.seed)
        a = summarize_a(d)
        d.to_csv(OUT / "c_class_placebo_draws.csv", index=False)
        a.to_csv(OUT / "c_class_placebo.csv", index=False)
        print(a.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
