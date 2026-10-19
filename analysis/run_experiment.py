"""Power for the proposed CRS recruitment experiment (see EXPERIMENT.md).

    python analysis/run_experiment.py data/export/community_year_panel.parquet [--reps 120]

Design: offer CRS application support to a random half of eligible never-CRS communities
(randomized encouragement) and follow claims for five years. The observational design in
run_design.py can only detect a ~50% drop in claims; this asks what randomization buys.

Each replicate replays history: pick a 3-year pre-period and 5-year follow-up from the real
panel, randomly assign eligible communities to offer or control, and estimate the
intent-to-treat (ITT) effect of the offer. With no effect injected, the spread of estimates
is the design's real sampling error. Injected replicates then cut post-period claims by 20%
for a random share p of offered communities (the compliers, who join CRS because of the
offer), and power is judged against the placebo distribution.

Compared:
  assignment:  within_county (half of each county offered; county-year fixed effects)
               whole_county  (entire counties offered; guards against spillovers, costs power)
  analysis:    post_only (follow-up years only) vs pre_post (adds the pre-period, which
               removes persistent differences between communities, like CUPED)
  size:        pilots of about 1,000 and 2,500 communities (whole counties sampled), and
               every eligible community
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
from run_analysis import drop_zero_groups, load  # noqa: E402

warnings.filterwarnings("ignore")
OUT = Path(__file__).parent / "results"
FIG = Path(__file__).parent / "figures"
MIN_POLICY_YEARS = 25          # communities with a real NFIP book; CRS entry is plausible
WINDOWS = [((2010, 2012), (2013, 2017)), ((2014, 2016), (2017, 2021)), ((2017, 2019), (2020, 2024))]
COMPLIANCE = [0.10, 0.25, 0.50]
EFFECT = np.log(0.8)           # complier effect: 20% fewer claims


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def eligible(df: pd.DataFrame) -> pd.DataFrame:
    never = df[df.crs_entry_date.isna() & (df.crs_class_current.fillna(10) == 10)]
    g = never.groupby("community_id").agg(epy=("earned_policy_years", "mean"), yrs=("year", "nunique"))
    ok = g[(g.epy >= MIN_POLICY_YEARS) & (g.yrs >= 13)].index
    e = never[never.community_id.isin(ok)].copy()
    e["n_claims"] = e.n_claims.astype(float)
    return e[["community_id", "county_fips", "year", "n_claims", "paid_k", "earned_policy_years"]]


def sample_counties(e: pd.DataFrame, n: int | None, rng) -> pd.DataFrame:
    """Whole counties at random until about n communities (None = everyone)."""
    if n is None:
        return e
    sizes = e.groupby("county_fips").community_id.nunique().sample(frac=1, random_state=rng.integers(1 << 31))
    keep = sizes.index[np.cumsum(sizes.values) <= n]
    return e[e.county_fips.isin(keep)]


def assign(e: pd.DataFrame, how: str, rng) -> pd.Series:
    comms = e.drop_duplicates("community_id")[["community_id", "county_fips"]]
    if how == "whole_county":
        counties = comms.county_fips.unique()
        offered = set(rng.choice(counties, len(counties) // 2, replace=False))
        arm = comms.county_fips.isin(offered)
    else:
        u = pd.Series(rng.random(len(comms)), index=comms.index)
        rank = u.groupby(comms.county_fips).rank(method="first")
        size = comms.groupby("county_fips").community_id.transform("size")
        # Half of each county; an odd community goes to either arm with equal chance.
        cut = size / 2 + np.where(size % 2 == 1, rng.random(len(comms)) < 0.5, 0) * 0.5
        arm = rank <= cut
    return pd.Series(arm.values.astype(int), index=comms.community_id.values)


def fit(e: pd.DataFrame, arm: pd.Series, window, how: str, analysis: str, y: str) -> tuple[float, float]:
    (p0, p1), (f0, f1) = window
    d = e[e.year.between(p0 if analysis == "pre_post" else f0, f1)].copy()
    d["offer"] = d.community_id.map(arm)
    d["post"] = (d.year >= f0).astype(int)
    d["treat"] = d.offer * d.post
    try:
        if how == "within_county":
            # Collapse to offer and control totals per county-year (as in run_analysis.collapse).
            g = d.groupby(["county_fips", "offer", "year"], as_index=False).agg(
                y=(y, "sum"), epy=("earned_policy_years", "sum"), treat=("treat", "max"))
            g["log_epy"] = np.log(g.epy)
            g["grp"] = g.county_fips + "_" + g.offer.astype(str)
            g["cy"] = g.county_fips + "_" + g.year.astype(str)
            fe = "grp + cy" if analysis == "pre_post" else "cy"
            g = drop_zero_groups(g, "y", fe.replace(" ", "").split("+"))
            f = pf.fepois(f"y ~ treat | {fe}", data=g, offset="log_epy", vcov={"CRV1": "county_fips"},
                          fixef_maxiter=100_000)
        else:
            d["log_epy"] = np.log(d.earned_policy_years)
            fe = "community_id + year" if analysis == "pre_post" else "year"
            d = drop_zero_groups(d, y, fe.replace(" ", "").split("+"))
            f = pf.fepois(f"{y} ~ treat | {fe}", data=d, offset="log_epy", vcov={"CRV1": "county_fips"},
                          fixef_maxiter=100_000)
        return float(f.coef()["treat"]), float(f.se()["treat"])
    except Exception:  # noqa: BLE001
        return np.nan, np.nan


def inject(e: pd.DataFrame, arm: pd.Series, window, p: float, rng) -> pd.DataFrame:
    offered = arm.index[arm.values == 1]
    compliers = set(rng.choice(offered, int(round(p * len(offered))), replace=False))
    f0 = window[1][0]
    out = e.copy()
    m = out.community_id.isin(compliers) & (out.year >= f0)
    out.loc[m, ["n_claims", "paid_k"]] *= np.exp(EFFECT)
    return out


DESIGNS = [("within_county", "pre_post"), ("within_county", "post_only"), ("whole_county", "pre_post")]


def simulate(e: pd.DataFrame, reps: int, sizes: list[int | None], seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for n in sizes:
        for r in range(reps):
            window = WINDOWS[r % len(WINDOWS)]
            s = sample_counties(e, n, rng)
            n_comm = s.community_id.nunique()
            for how, analysis in DESIGNS:
                arm = assign(s, how, rng)
                for y in ("n_claims", "paid_k"):
                    b, se = fit(s, arm, window, how, analysis, y)
                    rows.append(dict(size=n or "all", n_communities=n_comm, rep=r, window=window[1][0],
                                     assignment=how, analysis=analysis, outcome=y, compliance=0.0, b=b, se=se))
                for p in COMPLIANCE:
                    b, se = fit(inject(s, arm, window, p, rng), arm, window, how, analysis, "n_claims")
                    rows.append(dict(size=n or "all", n_communities=n_comm, rep=r, window=window[1][0],
                                     assignment=how, analysis=analysis, outcome="n_claims", compliance=p,
                                     b=b, se=se))
            if (r + 1) % 20 == 0:
                log(f"size {n or 'all'}: {r + 1}/{reps}")
    return pd.DataFrame(rows)


def summarize(d: pd.DataFrame) -> pd.DataFrame:
    keys = ["size", "assignment", "analysis", "outcome"]
    null = d[d.compliance == 0].dropna()
    s = null.groupby(keys).agg(n_communities=("n_communities", "median"), reps=("b", "size"),
                               placebo_mean=("b", "mean"), placebo_sd=("b", "std")).reset_index()
    s["false_positive_rate"] = null.assign(x=(null.b / null.se).abs() > 1.96).groupby(keys).x.mean().values
    s["itt_mde_pct"] = 100 * (1 - np.exp(-2.8 * s.placebo_sd))
    lo = null.groupby(keys).b.quantile(0.025).rename("crit")
    inj = d[d.compliance > 0].dropna().join(lo, on=keys)
    pw = inj.assign(x=lambda t: t.b < t.crit).groupby(keys + ["compliance"]).x.mean().unstack()
    pw.columns = [f"power_20pct_complier_effect_p{int(c * 100)}" for c in pw.columns]
    s = s.join(pw, on=keys)
    # Complier (CACE) MDE: ITT effect on the log scale is about p x the complier effect.
    for p in COMPLIANCE:
        s[f"complier_mde_pct_p{int(p * 100)}"] = 100 * (1 - np.exp(-2.8 * s.placebo_sd / p))
    return s


def figure(s: pd.DataFrame) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ink, ink2, grid, surface = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
    c = s[s.outcome == "n_claims"].copy()
    order = {"1000": 0, "2500": 1, "all": 2}
    c["x"] = c["size"].astype(str).map(order)
    style = {("within_county", "pre_post"): ("#2a78d6", "-", "o", "Within county, pre/post", 7),
             ("within_county", "post_only"): ("#1baf7a", "--", "D", "Within county, follow-up only", -7),
             ("whole_county", "pre_post"): ("#eb6834", ":", "s", "Whole counties, pre/post", 0)}
    fig, ax = plt.subplots(figsize=(7.5, 4.4), facecolor=surface)
    ax.set_facecolor(surface)
    for key, (color, ls, marker, label, dy) in style.items():
        g = c[(c.assignment == key[0]) & (c.analysis == key[1])].sort_values("x")
        ax.plot(g.x, g.itt_mde_pct, color=color, ls=ls, lw=2, marker=marker, ms=8,
                markeredgecolor=surface, markeredgewidth=2, label=label)
        ax.annotate(f"{g.itt_mde_pct.iloc[-1]:.0f}%", (g.x.iloc[-1], g.itt_mde_pct.iloc[-1]), xytext=(10, dy),
                    textcoords="offset points", va="center", fontsize=9, color=ink2)
    n_all = int(c[c["size"].astype(str) == "all"].n_communities.median())
    ax.set_xticks([0, 1, 2], ["~1,000 pilot", "~2,500 pilot", f"All eligible ({n_all:,})"])
    ax.set_xlim(-0.25, 2.45)
    ax.set_ylim(0, max(40, float(c.itt_mde_pct.max()) * 1.1))
    ax.set_ylabel("Detectable drop in claims from the offer (%)\n80% power, from placebo spread", color=ink2)
    ax.set_title("Randomized CRS recruitment: what each design can detect", loc="left", fontsize=12, color=ink)
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    ax.grid(axis="y", color=grid, lw=0.8)
    ax.tick_params(colors=ink2)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(grid)
    fig.tight_layout()
    FIG.mkdir(exist_ok=True)
    fig.savefig(FIG / "e_experiment_power.png", dpi=160, facecolor=surface)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("panel", nargs="?", default="data/export/community_year_panel.parquet")
    ap.add_argument("--reps", type=int, default=120)
    ap.add_argument("--seed", type=int, default=20261019)
    args = ap.parse_args()
    e = eligible(load(args.panel))
    log(f"eligible: {e.community_id.nunique():,} communities in {e.county_fips.nunique():,} counties")
    d = simulate(e, args.reps, [1000, 2500, None], args.seed)
    s = summarize(d)
    OUT.mkdir(exist_ok=True)
    d.to_csv(OUT / "e_experiment_draws.csv", index=False)
    s.to_csv(OUT / "e_experiment_power.csv", index=False)
    figure(s)
    pd.set_option("display.width", 250, "display.max_columns", 30)
    print(s.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
