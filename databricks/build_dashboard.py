"""Generate the AI/BI dashboard definition from databricks/dashboard_queries.sql.

    python databricks/build_dashboard.py

Writes databricks/dashboards/nfip_crs.lvdash.json, which the bundle deploys
(resources.dashboards in databricks.yml). The SQL lives only in dashboard_queries.sql;
this script adds pages and widgets. tests/test_dashboard.py checks every widget points at
a real dataset and field, and that widgets don't overlap.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).parent
SQL = HERE / "dashboard_queries.sql"
OUT = HERE / "dashboards" / "nfip_crs.lvdash.json"
REPO_URL = "https://github.com/JacobWillson13/nfip-crs-measurement"


def datasets() -> list[dict]:
    """One dataset per '-- dataset: name' block; the next comment line is its description."""
    blocks = re.split(r"^-- dataset: (\w+)\s*$", SQL.read_text(), flags=re.MULTILINE)[1:]
    out = []
    for name, body in zip(blocks[::2], blocks[1::2], strict=True):
        lines = [ln for ln in body.strip().splitlines()]
        desc = lines[0].removeprefix("-- ").strip() if lines and lines[0].startswith("--") else name
        sql = "\n".join(ln for ln in lines if not ln.startswith("--")).strip().rstrip(";")
        out.append({"name": name, "displayName": desc[:80], "queryLines": [ln + "\n" for ln in sql.splitlines()]})
    return out


# ---------------------------------------------------------------- widget builders
_n = 0


def _name(prefix: str) -> str:
    global _n
    _n += 1
    return f"{prefix}_{_n:02d}"


def pos(x: int, y: int, w: int, h: int) -> dict:
    return {"x": x, "y": y, "width": w, "height": h}


def text(md: str, p: dict) -> dict:
    return {"widget": {"name": _name("text"), "multilineTextboxSpec": {"lines": md.strip().splitlines(keepends=True)}},
            "position": p}


def _query(dataset: str, fields: dict[str, str], disaggregated: bool) -> list[dict]:
    return [{"name": "main_query", "query": {
        "datasetName": dataset, "disaggregated": disaggregated,
        "fields": [{"name": n, "expression": e} for n, e in fields.items()]}}]


def _frame(title: str, description: str = "") -> dict:
    f = {"showTitle": True, "title": title}
    if description:
        f.update(showDescription=True, description=description)
    return f


def counter(dataset: str, expr: str, title: str, p: dict, description: str = "") -> dict:
    field = "value"
    return {"widget": {"name": _name("counter"), "queries": _query(dataset, {field: expr}, False),
                       "spec": {"version": 2, "widgetType": "counter",
                                "encodings": {"value": {"fieldName": field, "displayName": title}},
                                "frame": _frame(title, description)}},
            "position": p}


def chart(kind: str, dataset: str, x: tuple[str, str, str, str], y: tuple[str, str, str], title: str, p: dict,
          color: tuple[str, str, str] | None = None, description: str = "") -> dict:
    """kind: line or bar. x = (field, expression, scale type, axis title); y and color = (field, expression, title)."""
    fields = {x[0]: x[1], y[0]: y[1]}
    enc = {"x": {"fieldName": x[0], "scale": {"type": x[2]}, "displayName": x[3], "axis": {"title": x[3]}},
           "y": {"fieldName": y[0], "scale": {"type": "quantitative"}, "displayName": y[2], "axis": {"title": y[2]}},
           "label": {"show": kind == "bar"}}
    if color:
        fields[color[0]] = color[1]
        enc["color"] = {"fieldName": color[0], "scale": {"type": "categorical"}, "displayName": color[2]}
    return {"widget": {"name": _name(kind), "queries": _query(dataset, fields, False),
                       "spec": {"version": 3, "widgetType": kind, "encodings": enc,
                                "frame": _frame(title, description)}},
            "position": p}


def table(dataset: str, columns: list[tuple[str, str, str]], title: str, p: dict, description: str = "") -> dict:
    """columns = (field, title, type) with type string | integer | float | datetime. The spec keys and
    per-column keys mirror dashboards exported by Databricks; leaving any out makes the widget invalid."""
    cols = []
    for i, (field, col_title, kind) in enumerate(columns):
        display = {"integer": "number", "float": "number", "datetime": "datetime"}.get(kind, "string")
        c = {"fieldName": field, "title": col_title, "displayName": col_title, "type": kind, "displayAs": display,
             "visible": True, "order": 100000 + i, "alignContent": "left" if display == "string" else "right",
             "booleanValues": ["false", "true"], "allowSearch": False, "allowHTML": False,
             "highlightLinks": False, "useMonospaceFont": False, "preserveWhitespace": False,
             "imageUrlTemplate": "{{ @ }}", "imageTitleTemplate": "{{ @ }}", "imageWidth": "", "imageHeight": "",
             "linkUrlTemplate": "{{ @ }}", "linkTextTemplate": "{{ @ }}", "linkTitleTemplate": "{{ @ }}",
             "linkOpenInNewTab": True}
        if kind in ("integer", "float"):
            c["numberFormat"] = "0" if kind == "integer" else "0.0"
        if kind == "datetime":
            c["dateTimeFormat"] = "YYYY-MM-DD HH:mm"
        cols.append(c)
    fields = {f: f"`{f}`" for f, _, _ in columns}
    return {"widget": {"name": _name("table"), "queries": _query(dataset, fields, True),
                       "spec": {"version": 1, "widgetType": "table", "condensed": True, "withRowNumber": False,
                                "allowHTMLByDefault": False, "itemsPerPage": 25, "paginationSize": "default",
                                "invisibleColumns": [], "encodings": {"columns": cols},
                                "frame": _frame(title, description)}},
            "position": p}


# ---------------------------------------------------------------- pages
def page(name: str, title: str, layout: list[dict]) -> dict:
    return {"name": name, "displayName": title, "pageType": "PAGE_TYPE_CANVAS", "layout": layout}


def overview() -> dict:
    return page("overview", "Overview", [
        text(f"""
# Are FEMA's CRS flood insurance discounts priced right?
Communities in FEMA's Community Rating System get 5% off flood insurance per class step. This dashboard compares
those discounts with observed losses across 335,000 NFIP community-years (2010 to 2024), within the same county and
year so communities faced the same storms. Pipeline, methods, and caveats: [GitHub repo]({REPO_URL}).
""", pos(0, 0, 6, 2)),
        counter("headline", "MAX(`claims_per_step_pct`)", "Claims per class step (%)", pos(0, 2, 2, 2),
                "Within county and year; schedule is -5% per step"),
        counter("headline", "MAX(`expected_abs_gap_musd`)", "Expected pricing gap ($M/yr)", pos(2, 2, 2, 2),
                "Schedule vs expected loss, all CRS classes"),
        counter("headline", "MAX(`evsi_musd`)", "Value of halving uncertainty ($M/yr)", pos(4, 2, 2, 2),
                "Expected reduction in pricing gap from twice the evidence"),
        chart("line", "losses_by_year", ("year", "`year`", "categorical", "Year"),
              ("claims_per_1k", "SUM(`claims_per_1k_policy_years`)", "Claims per 1,000 policy-years"),
              "Claim frequency", pos(0, 4, 3, 6), description="Spikes are catastrophe years: 2012, 2016, 2017, 2024"),
        chart("bar", "losses_by_year", ("year", "`year`", "categorical", "Year"),
              ("paid", "SUM(`paid_busd`)", "Paid losses ($B)"),
              "Paid NFIP losses ($B)", pos(3, 4, 3, 6)),
    ])


def pricing() -> dict:
    cols = [("crs_class", "Class", "string"), ("pooled_loss_cost_effect_pct", "Loss cost vs non-CRS (%)", "float"),
            ("low90_pct", "90% low", "float"), ("high90_pct", "90% high", "float"),
            ("scheduled_discount_pct", "Scheduled discount (%)", "float"),
            ("p_too_generous_pct", "P(discount too generous) %", "float"),
            ("expected_gap_musd", "Expected gap ($M/yr)", "float"),
            ("best_discount_pct", "Best discount now (%)", "float"),
            ("evpi_musd", "Value of perfect info ($M/yr)", "float"),
            ("evsi_half_variance_musd", "Value of halving uncertainty ($M/yr)", "float")]
    return page("pricing", "Pricing decision", [
        text("""
## Pricing decision by CRS class
Class relativities are partially pooled in a Bayesian hierarchical model, with uncertainty widened by
placebo tests. **Losses above discount** is the pooled loss cost relative to non-CRS neighbors plus the
scheduled discount: above zero, the class loses more than its discount assumes. Re-pricing on today's
evidence barely reduces the expected gap; better data (recovering historical CRS classes) is worth far more.
""", pos(0, 0, 6, 2)),
        chart("bar", "class_decision", ("crs_class", "`crs_class`", "categorical", "CRS class"),
              ("above", "SUM(`losses_above_discount_pts`)", "Points"),
              "Losses above discount (points)", pos(0, 2, 3, 6),
              description="Pooled loss cost vs non-CRS neighbors, plus the scheduled discount"),
        chart("bar", "class_decision", ("crs_class", "`crs_class`", "categorical", "CRS class"),
              ("p", "SUM(`p_too_generous_pct`)", "Probability (%)"),
              "Probability the discount is too generous", pos(3, 2, 3, 6)),
        table("class_decision", cols, "Decision table", pos(0, 8, 6, 5),
              "Dollars are expected annual loss at 2022 to 2024 exposure and 2010 to 2024 loss cost"),
    ])


def designs() -> dict:
    return page("designs", "Measurement designs", [
        text("""
## Can the causal effect of joining CRS be measured?
**Left:** placebo simulations of the entry difference-in-differences. The original design (year fixed
effects only) was biased (+41% with no true effect) and flagged fake effects 25% of the time; comparing
within county and year fixes the bias but detects only large effects. **Right:** a proposed randomized
recruitment trial. It can measure whether outreach raises participation, but not a realistic effect on claims.
""", pos(0, 0, 6, 2)),
        chart("line", "entry_design", ("n_entrants", "`n_entrants`", "quantitative", "CRS entrants in the design"),
              ("mde", "AVG(`mde_pct`)", "Minimum detectable effect (%)"),
              "Observational design: detectable effect", pos(0, 2, 3, 7),
              color=("design_outcome", "`design_outcome`", "Design, outcome")),
        chart("line", "experiment_power", ("communities", "`communities`", "quantitative", "Communities in the trial"),
              ("itt", "AVG(`itt_mde_pct`)", "Detectable drop from the offer (%)"),
              "Randomized trial: detectable drop in claims", pos(3, 2, 3, 7),
              color=("design", "`design`", "Design")),
        table("experiment_power", [("design", "Design", "string"), ("trial_size", "Size", "string"),
                                   ("communities", "Communities", "integer"),
                                   ("itt_mde_pct", "Offer effect detectable (%)", "float"),
                                   ("complier_mde_pct_at_50pct_uptake", "Joining effect, 50% uptake (%)", "float"),
                                   ("complier_mde_pct_at_25pct_uptake", "Joining effect, 25% uptake (%)", "float")],
              "Trial power", pos(0, 9, 6, 5)),
    ])


def health() -> dict:
    return page("health", "Pipeline health", [
        text("""
## Pipeline health
Latest run of the Databricks job: silver loads reconciled against their files, and the quality checks
shared with the local DuckDB pipeline. An error-level failure stops the job before the analysis runs;
warnings are recorded for review.
""", pos(0, 0, 6, 2)),
        counter("pipeline_health", "COUNT(`check_name`)", "Checks run", pos(0, 2, 2, 2)),
        counter("pipeline_health", "SUM(`passed_flag`)", "Checks passed", pos(2, 2, 2, 2)),
        counter("pipeline_health", "SUM(`blocking`)", "Blocking failures", pos(4, 2, 2, 2)),
        table("pipeline_health", [("check_name", "Check", "string"), ("layer", "Layer", "string"),
                                  ("severity", "Severity", "string"), ("failing_rows", "Failing rows", "integer"),
                                  ("passed", "Passed", "string")], "Quality checks", pos(0, 4, 4, 8)),
        table("load_log", [("dataset", "Table", "string"), ("file_rows", "File rows", "integer"),
                           ("table_rows", "Table rows", "integer"), ("status", "Status", "string"),
                           ("loaded_at", "Loaded at", "datetime")], "Latest silver loads", pos(4, 4, 2, 8)),
    ])


def build() -> dict:
    global _n
    _n = 0
    return {"datasets": datasets(), "pages": [overview(), pricing(), designs(), health()]}


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=2) + "\n")
    d = json.loads(OUT.read_text())
    print(f"Wrote {OUT.relative_to(HERE.parent)}: {len(d['datasets'])} datasets, "
          f"{sum(len(p['layout']) for p in d['pages'])} widgets on {len(d['pages'])} pages")


if __name__ == "__main__":
    main()
