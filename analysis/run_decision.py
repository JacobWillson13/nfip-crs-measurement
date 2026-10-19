"""Pricing decision analysis: is each CRS discount set right, and is more data worth getting?

    python analysis/run_decision.py data/export/community_year_panel.parquet

Builds on analysis A (loss cost by CRS class, within county and year).

1. Partial pooling. The five class estimates are noisy (intervals of +/-40 points). They are
   pooled in a normal hierarchical model: log relativity for class bin c is
   theta_c = beta * steps_c + u_c, with u_c ~ N(0, tau^2), so classes borrow strength from the
   overall per-step slope but can depart from it as much as the data support. The PPML
   covariance is widened by the placebo critical value from run_design.py, so the
   posterior inherits the corrected uncertainty. Priors: beta ~ N(0, 0.5^2) (per-step
   effects within -63% to +170%); tau ~ half-normal(0.25). The posterior is exact
   given the normal approximation: tau on a grid, then theta given tau in closed form.

2. Decision. Pricing needs the predictive relativity (expected loss of a class-c community
   relative to a non-CRS neighbor), not the causal effect of CRS, so A's within-county
   association is the right input. For each class:
     * P(discount too generous): the class's losses are reduced by less than its discount.
     * Expected annual gap, in dollars of expected loss: what the schedule charges relative
       to a premium proportional to expected loss, at today's exposure and long-run loss cost.
     * The discount that minimizes the expected absolute gap given current evidence.
     * EVPI: the expected absolute gap left after choosing that discount. It is the most
       that perfect information about the class could be worth per year.
     * EVSI: the expected reduction in that gap from a study that halves the variance
       (about twice the data, for example by recovering historical CRS classes).

Writes analysis/results/d_*.csv and analysis/figures/d_class_relativities.png.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from run_analysis import KNOWN, load, ppml  # noqa: E402

OUT = Path(__file__).parent / "results"
FIG = Path(__file__).parent / "figures"
BINS = ["1-5", "6", "7", "8", "9"]
RECENT = (2022, 2024)          # today's book: Risk Rating 2.0, discounts apply to every policy
DRAWS, SEED = 40_000, 20261018
BETA_SD, TAU_SCALE = 0.5, 0.25


def class_bins(df: pd.DataFrame) -> pd.DataFrame:
    a = df[df.crs_status_basis.isin(KNOWN)].copy()
    a["crs_bin"] = np.where(a.crs_class <= 5, "1-5", a.crs_class.astype(int).astype(str))
    return a


def estimates(a: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    fit = ppml("paid_k ~ i(crs_bin, ref='10') + sfha | county_year", a, "paid_k", ["county_year"])
    names = [f"crs_bin::{b}" for b in BINS]
    V = pd.DataFrame(fit._vcov, index=fit._coefnames, columns=fit._coefnames).loc[names, names].values
    return fit.coef()[names].values, V


def bin_facts(a: pd.DataFrame) -> pd.DataFrame:
    crs = a[a.crs_bin != "10"]
    w = crs.earned_policy_years
    rec = crs[crs.year.between(*RECENT)]
    f = pd.DataFrame({
        "steps": (crs.assign(s=(10 - crs.crs_class) * w).groupby("crs_bin").s.sum() / w.groupby(crs.crs_bin).sum()),
        "communities_recent": rec.groupby("crs_bin").community_id.nunique(),
        "policy_years_per_year": rec.groupby("crs_bin").earned_policy_years.sum() / (RECENT[1] - RECENT[0] + 1),
        "loss_cost_per_policy_year": crs.groupby("crs_bin").paid_total.sum() / w.groupby(crs.crs_bin).sum(),
    }).loc[BINS]
    f["scheduled_discount"] = 0.05 * f.steps
    return f


def posterior(y: np.ndarray, V: np.ndarray, s: np.ndarray, rng) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Draws of theta (log relativities), beta, and tau."""
    from scipy.stats import multivariate_normal
    taus = np.linspace(0.0, 1.0, 401)
    logp = []
    for t in taus:
        cov = V + t**2 * np.eye(len(y)) + BETA_SD**2 * np.outer(s, s)
        logp.append(multivariate_normal.logpdf(y, np.zeros(len(y)), cov) - 0.5 * (t / TAU_SCALE) ** 2)
    p = np.exp(np.array(logp) - max(logp))
    p /= p.sum()
    tau = rng.choice(taus, DRAWS, p=p)
    theta, beta = np.empty((DRAWS, len(y))), np.empty(DRAWS)
    Vi = np.linalg.inv(V)
    for t in np.unique(tau):
        idx = np.where(tau == t)[0]
        # Joint prior of (beta, u) mapped to theta = beta*s + u; sample beta and theta jointly.
        P = np.block([[np.array([[BETA_SD**2]]), (BETA_SD**2 * s)[None, :]],
                      [(BETA_SD**2 * s)[:, None], BETA_SD**2 * np.outer(s, s) + max(t, 1e-6) ** 2 * np.eye(len(y))]])
        H = np.hstack([np.zeros((len(y), 1)), np.eye(len(y))])          # y = H [beta, theta] + noise
        S = np.linalg.inv(np.linalg.inv(P) + H.T @ Vi @ H)
        m = S @ H.T @ Vi @ y
        z = rng.multivariate_normal(m, (S + S.T) / 2, len(idx))
        beta[idx], theta[idx] = z[:, 0], z[:, 1:]
    return theta, beta, tau


def gap(theta, d, py, lc):
    """Annual (premium at discount d) minus (expected loss), in dollars, per posterior draw.
    Base premium = expected loss at class 10, so the fair premium for the class is its
    expected loss; positive = the schedule charges more than expected loss."""
    return py * lc * ((1 - d) / np.exp(theta) - 1)


def evsi(theta_c, py, lc, var_factor, rng, n=20_000):
    """Expected absolute gap after a study whose estimate of theta_c has variance
    var_factor x the current posterior variance, choosing the discount afterwards.
    Normal approximation to the current posterior."""
    mu, sd = theta_c.mean(), theta_c.std()
    true = rng.normal(mu, sd, n)
    obs = true + rng.normal(0, sd * np.sqrt(var_factor), n)
    post_mu = (mu / sd**2 + obs / (sd**2 * var_factor)) / (1 / sd**2 + 1 / (sd**2 * var_factor))
    post_var = 1 / (1 / sd**2 + 1 / (sd**2 * var_factor))
    # The gap is linear in (1 - d) with weight exp(-theta), so the discount minimizing the
    # expected absolute gap is 1 minus the exp(-theta)-weighted median of exp(theta); for a
    # normal posterior that is exp(mu - var).
    d_star = 1 - np.exp(post_mu - post_var)
    return float(np.abs(gap(true, d_star, py, lc)).mean())


def best_discount(theta_c, py, lc) -> float:
    """Discount minimizing the expected absolute gap over the posterior draws."""
    grid = np.arange(-0.60, 0.70, 0.0025)
    loss = [np.abs(gap(theta_c, d, py, lc)).mean() for d in grid]
    return float(grid[int(np.argmin(loss))])


def analysis_d(df: pd.DataFrame, critical_value: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(SEED)
    a = class_bins(df)
    y, V = estimates(a)
    V = V * (critical_value / 1.96) ** 2
    facts = bin_facts(a)
    s = facts.steps.values
    theta, beta, tau = posterior(y, V, s, rng)
    rows = []
    for j, b in enumerate(BINS):
        f = facts.loc[b]
        t = theta[:, j]
        r = np.exp(t)
        g_sched = gap(t, f.scheduled_discount, f.policy_years_per_year, f.loss_cost_per_policy_year)
        d_star = best_discount(t, f.policy_years_per_year, f.loss_cost_per_policy_year)
        g_star = gap(t, d_star, f.policy_years_per_year, f.loss_cost_per_policy_year)
        evpi = float(np.abs(g_star).mean())
        rows.append({
            "crs_bin": b, "mean_class_steps": f.steps, "communities_2022_2024": int(f.communities_recent),
            "policy_years_per_year": f.policy_years_per_year,
            "loss_cost_per_policy_year": f.loss_cost_per_policy_year,
            "raw_effect_pct": 100 * (np.exp(y[j]) - 1),
            "raw_ci_low_pct": 100 * (np.exp(y[j] - 1.96 * np.sqrt(V[j, j])) - 1),
            "raw_ci_high_pct": 100 * (np.exp(y[j] + 1.96 * np.sqrt(V[j, j])) - 1),
            "pooled_effect_pct": 100 * (np.median(r) - 1),
            "pooled_low90_pct": 100 * (np.quantile(r, 0.05) - 1),
            "pooled_high90_pct": 100 * (np.quantile(r, 0.95) - 1),
            "scheduled_discount_pct": 100 * f.scheduled_discount,
            "p_discount_too_generous": float((r > 1 - f.scheduled_discount).mean()),
            "expected_gap_usd": float(g_sched.mean()),
            "expected_abs_gap_usd": float(np.abs(g_sched).mean()),
            "best_discount_pct": 100 * d_star,
            "expected_abs_gap_at_best_usd": evpi,
            "evpi_usd": evpi,
            "evsi_half_variance_usd": evpi - evsi(t, f.policy_years_per_year, f.loss_cost_per_policy_year, 1.0, rng),
        })
    res = pd.DataFrame(rows)
    pooled = pd.DataFrame([{
        "per_step_effect_pct": 100 * (np.exp(np.median(beta)) - 1),
        "per_step_low90_pct": 100 * (np.exp(np.quantile(beta, 0.05)) - 1),
        "per_step_high90_pct": 100 * (np.exp(np.quantile(beta, 0.95)) - 1),
        "p_per_step_reduction_below_5pct": float((np.exp(beta) > 0.95).mean()),
        "tau_median": float(np.median(tau)), "tau_high90": float(np.quantile(tau, 0.95)),
        "placebo_critical_value": critical_value,
        "total_expected_gap_usd": res.expected_gap_usd.sum(),
        "total_expected_abs_gap_usd": res.expected_abs_gap_usd.sum(),
        "total_evpi_usd": res.evpi_usd.sum(), "total_evsi_half_variance_usd": res.evsi_half_variance_usd.sum(),
    }])
    return res, pooled


def figure(res: pd.DataFrame) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ink, ink2, grid, surface = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
    raw_c, pool_c = "#eb6834", "#2a78d6"
    fig, ax = plt.subplots(figsize=(8.5, 4.8), facecolor=surface)
    ax.set_facecolor(surface)
    x = np.arange(len(res))
    labels = [f"Class {b}\nP(too generous) {p:.0%}"
              for b, p in zip(res.crs_bin, res.p_discount_too_generous, strict=True)]
    ax.errorbar(x - 0.12, res.raw_effect_pct, yerr=[res.raw_effect_pct - res.raw_ci_low_pct,
                res.raw_ci_high_pct - res.raw_effect_pct], fmt="s", ms=8, color=raw_c, ecolor=raw_c, lw=2,
                capsize=0, markeredgecolor=surface, markeredgewidth=2, label="Estimate alone (95%)")
    ax.errorbar(x + 0.12, res.pooled_effect_pct, yerr=[res.pooled_effect_pct - res.pooled_low90_pct,
                res.pooled_high90_pct - res.pooled_effect_pct], fmt="o", ms=8, color=pool_c, ecolor=pool_c, lw=2,
                capsize=0, markeredgecolor=surface, markeredgewidth=2, label="Partially pooled (90%)")
    ax.scatter(x, -res.scheduled_discount_pct, marker="_", s=900, color=ink, lw=2, zorder=3)
    ax.axhline(0, color=grid, lw=1)
    ax.set_xticks(x, labels)
    ax.set_ylim(-75, 135)
    ax.set_ylabel("Loss cost vs non-CRS neighbors (%)", color=ink2)
    ax.set_title("CRS class relativities against the discount schedule", loc="left", fontsize=12, color=ink)
    from matplotlib.lines import Line2D
    handles, names = ax.get_legend_handles_labels()
    handles.append(Line2D([], [], color=ink, lw=2))
    names.append("Scheduled discount")
    ax.legend(handles, names, frameon=False, fontsize=8, loc="upper left", ncol=3)
    ax.grid(axis="y", color=grid, lw=0.8)
    ax.tick_params(colors=ink2)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(grid)
    fig.tight_layout()
    FIG.mkdir(exist_ok=True)
    fig.savefig(FIG / "d_class_relativities.png", dpi=160, facecolor=surface)
    plt.close(fig)


def placebo_critical_value(default: float = 1.96) -> float:
    p = OUT / "c_class_placebo.csv"
    if not p.exists():
        return default
    c = pd.read_csv(p)
    return float(c.loc[c.outcome == "loss_cost", "placebo_critical_value"].iloc[0])


def main(path: str) -> None:
    df = load(path)
    res, pooled = analysis_d(df, placebo_critical_value())
    OUT.mkdir(exist_ok=True)
    res.to_csv(OUT / "d_pricing_decision.csv", index=False)
    pooled.to_csv(OUT / "d_pooled_summary.csv", index=False)
    figure(res)
    pd.set_option("display.width", 250, "display.max_columns", 30)
    print(res.round(3).T.to_string(), "\n")
    print(pooled.round(3).T.to_string())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/export/community_year_panel.parquet")
