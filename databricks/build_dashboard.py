"""Generate the AI/BI dashboard definition from databricks/dashboard_queries.sql.

    python databricks/build_dashboard.py              # default, with the custom interval chart
    python databricks/build_dashboard.py --standard   # standard widgets only, if custom charts are unavailable

Writes databricks/dashboards/nfip_crs.lvdash.json, which the bundle deploys
(resources.dashboards in databricks.yml). The SQL lives only in dashboard_queries.sql;
this script adds pages and widgets. Numbers quoted in text widgets are read from the
committed results in analysis/results/, so the prose cannot drift from the tables.
tests/test_dashboard.py checks every widget points at a real dataset, field, and
parameter, and that widgets don't overlap; tests/test_dashboard_sql.py runs every dataset
query on local Spark.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
SQL = HERE / "dashboard_queries.sql"
OUT = HERE / "dashboards" / "nfip_crs.lvdash.json"
RESULTS = ROOT / "analysis" / "results"
REPO_URL = "https://github.com/JacobWillson13/nfip-crs-measurement"
TABLE_PREFIX = "workspace.nfip."
CLASSES = ["Class 1-5", "Class 6", "Class 7", "Class 8", "Class 9"]
LIKELY = 0.8                 # P(discount too generous) at which the overview calls a class out

sys.path.insert(0, str(ROOT))
from pipeline.quality.checks import CHECKS  # noqa: E402

# ---------------------------------------------------------------- datasets
_ROW_CHECK = re.compile(r"^\s*select count\(\*\) from (silver|gold)\.(\w+)\s+where\b", re.IGNORECASE)


def check_table_sql() -> tuple[str, str]:
    """VALUES rows mapping each row-level check to the table it counts, and a row count per table.
    Checks that count groups, differences, or flags get no share."""
    pairs = []
    for chk in CHECKS:
        m = _ROW_CHECK.match(chk.sql)
        if m:
            pairs.append((chk.name, f"{m.group(1)}_{m.group(2)}"))
    values = ", ".join(f"('{c}', '{t}')" for c, t in pairs)
    tables = sorted({t for _, t in pairs})
    counts = "\n  union all\n  ".join(f"select '{t}' as table_name, count(*) as n from {TABLE_PREFIX}{t}"
                                       for t in tables)
    return values, counts


def _param(line: str) -> dict:
    keyword, dtype, *rest = line.split()
    p = {"displayName": keyword, "keyword": keyword, "dataType": dtype}
    if rest == ["MULTI"]:
        p["complexType"] = "MULTI"
        p["defaultSelection"] = {"values": {"dataType": dtype, "values": []}}
    else:
        raise ValueError(f"unsupported parameter declaration: {line}")
    return p


def datasets() -> list[dict]:
    """One dataset per '-- dataset: name' block; the next comment line is its description and
    '-- param:' lines declare its parameters."""
    values, counts = check_table_sql()
    text = SQL.read_text().replace("{check_tables}", values).replace("{table_counts}", counts)
    blocks = re.split(r"^-- dataset: (\w+)\s*$", text, flags=re.MULTILINE)[1:]
    out = []
    for name, body in zip(blocks[::2], blocks[1::2], strict=True):
        lines = body.strip().splitlines()
        desc = lines[0].removeprefix("-- ").strip() if lines and lines[0].startswith("--") else name
        params = [_param(ln.split(":", 1)[1]) for ln in lines if ln.startswith("-- param:")]
        sql = "\n".join(ln for ln in lines if not ln.lstrip().startswith("--")).strip().rstrip(";")
        ds = {"name": name, "displayName": desc[:80], "queryLines": [ln + "\n" for ln in sql.splitlines()]}
        if params:
            ds["parameters"] = params
        out.append(ds)
    return out


# ---------------------------------------------------------------- committed results, for prose
def _rows(name: str) -> list[dict]:
    with open(RESULTS / f"{name}.csv", newline="") as f:
        return list(csv.DictReader(f))


def facts() -> dict:
    a = {(r["model"], r["Coefficient"]): r for r in _rows("a_pricing_adequacy")}
    placebo = {r["outcome"]: r for r in _rows("c_class_placebo")}
    d = _rows("d_pricing_decision")
    pooled = _rows("d_pooled_summary")[0]
    power = _rows("c_power_design")
    n0 = min(int(r["n_entrants"]) for r in power)
    biased = next(r for r in power if r["design"] == "year_fe_only" and r["outcome"] == "loss_cost"
                  and int(r["n_entrants"]) == n0)
    return {
        "claims_step": float(a[("A_claim_frequency_per_class_step", "class_steps")]["effect_pct"]),
        "claims_ci": (float(placebo["claim_frequency"]["placebo_ci_low_pct"]),
                      float(placebo["claim_frequency"]["placebo_ci_high_pct"])),
        "loss_step": float(a[("A_loss_cost_per_class_step", "class_steps")]["effect_pct"]),
        "loss_ci": (float(placebo["loss_cost"]["placebo_ci_low_pct"]),
                    float(placebo["loss_cost"]["placebo_ci_high_pct"])),
        "classes": [{"bin": r["crs_bin"], "p": float(r["p_discount_too_generous"]),
                     "sched": float(r["scheduled_discount_pct"]), "best": float(r["best_discount_pct"]),
                     "net_musd": float(r["expected_gap_usd"]) / 1e6} for r in d],
        "abs_gap_musd": float(pooled["total_expected_abs_gap_usd"]) / 1e6,
        "evsi_musd": float(pooled["total_evsi_half_variance_usd"]) / 1e6,
        "entrants": int(_rows("b_sample")[0]["treated_communities"]),
        "biased_pct": float(biased["placebo_mean_pct"]),
        "biased_fpr": float(biased["false_positive_rate"]),
    }


def _change(lo: float, hi: float) -> str:
    """A percent-change interval in words: 'between 8.5% and 1.6% fewer', or crossing zero."""
    if hi < 0:
        return f"{-hi:.1f}% to {-lo:.1f}% lower"
    return f"{-lo:.1f}% lower to {hi:.1f}% higher"


def _count(n: int) -> str:
    words = {0: "no discounts", 1: "one discount", 2: "two discounts", 3: "three discounts", 4: "four discounts"}
    return words.get(n, f"{n} discounts")


def _discount(x: float) -> str:
    return f"a {x:.0f}% discount" if x >= 0.5 else (f"a {-x:.0f}% surcharge" if x <= -0.5 else "no discount")


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


def _query(dataset: str, fields: dict[str, str], disaggregated: bool, name: str = "main_query") -> dict:
    return {"name": name, "query": {
        "datasetName": dataset, "disaggregated": disaggregated,
        "fields": [{"name": n, "expression": e} for n, e in fields.items()]}}


def _frame(title: str, description: str = "") -> dict:
    f = {"showTitle": True, "title": title}
    if description:
        f.update(showDescription=True, description=description)
    return f


def plain(places: int = 1) -> dict:
    return {"type": "number-plain", "abbreviation": "none", "decimalPlaces": {"type": "max", "places": places}}


def usd() -> dict:
    return {"type": "number-currency", "currencyCode": "USD", "abbreviation": "compact",
            "decimalPlaces": {"type": "max", "places": 1}}


def counter(dataset: str, expr: str, title: str, p: dict, description: str = "", fmt: dict | None = None) -> dict:
    field = "value"
    value = {"fieldName": field, "displayName": title}
    if fmt:
        value["format"] = fmt
    return {"widget": {"name": _name("counter"), "queries": [_query(dataset, {field: expr}, False)],
                       "spec": {"version": 2, "widgetType": "counter", "encodings": {"value": value},
                                "frame": _frame(title, description)}},
            "position": p}


def hline(y: float, label: str = "") -> dict:
    enc = {"y": {"dataValue": str(y), "dataType": "INTEGER" if float(y).is_integer() else "DOUBLE"}}
    if label:
        enc["label"] = {"value": label}
    return {"type": "horizontal-line", "encodings": enc}


def vline(x: int, label: str) -> dict:
    return {"type": "vertical-line", "encodings": {"x": {"dataValue": str(x), "dataType": "INTEGER"},
                                                   "label": {"value": label}}}


def chart(kind: str, dataset: str, x: tuple[str, str, str, str], y: tuple[str, str, str], title: str, p: dict,
          color: tuple[str, str, str] | None = None, description: str = "", annotations: list | None = None,
          x_order: list[str] | None = None, grouped: bool = False, color_map: dict[str, str] | None = None) -> dict:
    """kind: line or bar. x = (field, expression, scale type, axis title); y and color = (field, expression, title)."""
    fields = {x[0]: x[1], y[0]: y[1]}
    xscale = {"type": x[2]}
    if x_order:
        xscale["sort"] = {"by": "custom-order", "orderedValues": x_order}
    enc = {"x": {"fieldName": x[0], "scale": xscale, "displayName": x[3], "axis": {"title": x[3]}},
           "y": {"fieldName": y[0], "scale": {"type": "quantitative"}, "displayName": y[2], "axis": {"title": y[2]}},
           "label": {"show": kind == "bar"}}
    if color:
        fields[color[0]] = color[1]
        cscale = {"type": "categorical"}
        if color_map:
            cscale["mappings"] = [{"value": v, "color": c} for v, c in color_map.items()]
        enc["color"] = {"fieldName": color[0], "scale": cscale, "displayName": color[2]}
    spec = {"version": 3, "widgetType": kind, "encodings": enc, "frame": _frame(title, description)}
    if grouped:
        spec["mark"] = {"layout": "group"}
    if annotations:
        spec["annotations"] = annotations
    return {"widget": {"name": _name(kind), "queries": [_query(dataset, fields, False)], "spec": spec}, "position": p}


def combo(dataset: str, x: tuple[str, str, str], bars: tuple[str, str, str], line: tuple[str, str, str],
          title: str, p: dict, description: str = "") -> dict:
    """Bars on the primary axis, a line on the secondary. Each tuple = (field, expression, title)."""
    fields = {x[0]: x[1], bars[0]: bars[1], line[0]: line[1]}
    enc = {"x": {"fieldName": x[0], "scale": {"type": "categorical"}, "displayName": x[2], "axis": {"title": x[2]}},
           "y": {"scale": {"type": "quantitative"},
                 "primary": {"fields": [{"fieldName": bars[0], "displayName": bars[2]}]},
                 "secondary": {"fields": [{"fieldName": line[0], "displayName": line[2]}]}},
           "label": {"show": False}}
    return {"widget": {"name": _name("combo"), "queries": [_query(dataset, fields, False)],
                       "spec": {"version": 1, "widgetType": "combo", "encodings": enc,
                                "frame": _frame(title, description)}},
            "position": p}


def us_state_map(dataset: str, state_field: str, value: tuple[str, str, str], title: str, p: dict,
                 description: str = "") -> dict:
    fields = {state_field: f"`{state_field}`", value[0]: value[1]}
    enc = {"region": {"regionType": "mapbox-v4-admin",
                      "admin0": {"type": "value", "value": "United States", "geographicRole": "admin0-name"},
                      "admin1": {"fieldName": state_field, "type": "field", "geographicRole": "admin1-name"}},
           "color": {"fieldName": value[0], "displayName": value[2], "scale": {
               "type": "quantitative", "colorRamp": {"mode": "custom-sequential",
                                                     "colors": {"start": "#DCEBF7", "end": "#0B4F8A"}}}}}
    return {"widget": {"name": _name("map"), "queries": [_query(dataset, fields, False)],
                       "spec": {"version": 1, "widgetType": "choropleth-map", "encodings": enc,
                                "frame": _frame(title, description)}},
            "position": p}


def vega(dataset: str, fields: dict[str, str], vl: dict, title: str, p: dict, description: str = "") -> dict:
    """Custom Vega-Lite chart. Field names here are the names the Vega-Lite spec reads."""
    return {"widget": {"name": _name("custom"), "queries": [_query(dataset, fields, True)],
                       "spec": {"version": 1, "widgetType": "custom-vega-viz",
                                "data": {"queryName": "main_query"},
                                "encodings": {"fields": [{"fieldName": f} for f in fields]},
                                "jsonSpec": {"type": "vega-lite", "spec": json.dumps(vl)},
                                "frame": _frame(title, description)}},
            "position": p}


def table(dataset: str, columns: list[tuple], title: str, p: dict, description: str = "") -> dict:
    """columns = (field, title, type[, number format]) with type string | integer | float | datetime. The spec
    keys and per-column keys mirror dashboards exported by Databricks; leaving any out makes the widget invalid."""
    cols = []
    for i, (field, col_title, kind, *fmt) in enumerate(columns):
        display = {"integer": "number", "float": "number", "datetime": "datetime"}.get(kind, "string")
        c = {"fieldName": field, "title": col_title, "displayName": col_title, "type": kind, "displayAs": display,
             "visible": True, "order": 100000 + i, "alignContent": "left" if display == "string" else "right",
             "booleanValues": ["false", "true"], "allowSearch": False, "allowHTML": False,
             "highlightLinks": False, "useMonospaceFont": False, "preserveWhitespace": False,
             "imageUrlTemplate": "{{ @ }}", "imageTitleTemplate": "{{ @ }}", "imageWidth": "", "imageHeight": "",
             "linkUrlTemplate": "{{ @ }}", "linkTextTemplate": "{{ @ }}", "linkTitleTemplate": "{{ @ }}",
             "linkOpenInNewTab": True}
        if kind in ("integer", "float"):
            c["numberFormat"] = fmt[0] if fmt else ("0" if kind == "integer" else "0.0")
        if kind == "datetime":
            c["dateTimeFormat"] = "YYYY-MM-DD HH:mm"
        cols.append(c)
    fields = {f: f"`{f}`" for f, *_ in columns}
    return {"widget": {"name": _name("table"), "queries": [_query(dataset, fields, True)],
                       "spec": {"version": 1, "widgetType": "table", "condensed": True, "withRowNumber": False,
                                "allowHTMLByDefault": False, "itemsPerPage": 25, "paginationSize": "default",
                                "invisibleColumns": [], "encodings": {"columns": cols},
                                "frame": _frame(title, description)}},
            "position": p}


def multi_select(keyword: str, options: tuple[str, str], targets: list[str], title: str, p: dict) -> dict:
    """Filter bound to a MULTI parameter on each target dataset; the choices come from options =
    (dataset, field). Parameters, not field filters, because the targets recompute rates after filtering."""
    values = f"values_{keyword}"
    queries = [_query(options[0], {options[1]: f"`{options[1]}`"}, False, values)]
    fields = [{"fieldName": options[1], "displayName": title, "queryName": values}]
    for ds in targets:
        qname = f"param_{keyword}_{ds}"
        queries.append({"name": qname, "query": {"datasetName": ds, "disaggregated": False,
                                                 "parameters": [{"name": keyword, "keyword": keyword}]}})
        fields.append({"parameterName": keyword, "queryName": qname})
    return {"widget": {"name": _name("filter"), "queries": queries,
                       "spec": {"version": 2, "widgetType": "filter-multi-select",
                                "encodings": {"fields": fields}, "frame": _frame(title)}},
            "position": p}


def range_slider(dataset: str, field: str, title: str, p: dict) -> dict:
    qname = f"range_{dataset}_{field}"
    q = _query(dataset, {f"min({field})": f"MIN(`{field}`)", f"max({field})": f"MAX(`{field}`)"}, False, qname)
    return {"widget": {"name": _name("filter"), "queries": [q],
                       "spec": {"version": 2, "widgetType": "range-slider",
                                "encodings": {"fields": [{"fieldName": field, "queryName": qname}]},
                                "frame": _frame(title)}},
            "position": p}


# ---------------------------------------------------------------- pages
def page(name: str, title: str, layout: list[dict]) -> dict:
    return {"name": name, "displayName": title, "pageType": "PAGE_TYPE_CANVAS", "layout": layout}


def _names(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def overview(f: dict, vega_ok: bool) -> dict:
    """The hero page: the answer, three numbers, and the one chart that shows it, above the fold
    (rows 0 to 11); the filterable history sits below."""
    generous = [c for c in f["classes"] if c["p"] >= LIKELY]
    head = "CRS flood discounts are right on average"
    if generous:
        head += f", but {_count(len(generous)).split()[0]} of {len(f['classes'])} classes get too much"
    who = _names([f"Class {c['bin']}" for c in generous])
    odds = _names([f"{c['p']:.0%}" for c in generous])
    return page("overview", "Overview", [
        text(f"""
# {head}
Comparing communities in the same county and year, so they faced the same storms, each CRS class step cuts loss
cost {-f['loss_step']:.1f}% against a scheduled 5% discount. {who} {'is' if len(generous) == 1 else 'are'} likely
over-discounted ({odds} probability). The evidence is thin, so better data is worth more than re-pricing:
about ${f['evsi_musd']:.0f}M a year.

335,000 NFIP community-years, 2010 to 2024. [Pipeline, methods, and caveats on GitHub]({REPO_URL})
""", pos(0, 0, 6, 3)),
        counter("headline", "MAX(`loss_cost_per_step_pct`)", "Loss cost per class step (%)", pos(0, 3, 2, 2),
                f"95% interval {f['loss_ci'][0]:+.1f}% to {f['loss_ci'][1]:+.1f}%; the schedule assumes -5%",
                plain(1)),
        counter("headline", "MAX(`expected_abs_gap_usd`)", "Expected pricing gap per year", pos(2, 3, 2, 2),
                "Sum over classes of |premium at the scheduled discount - expected loss|", usd()),
        counter("headline", "MAX(`evsi_usd`)", "Value of halving uncertainty per year", pos(4, 3, 2, 2),
                "Expected cut in that gap from about twice the evidence", usd()),
        _forest(vega_ok, pos(0, 5, 3, 7), hero=True),
        us_state_map("state_map", "state_name", ("crs_share", "SUM(`crs_share_pct`)", "In CRS (%)"),
                     "Policies in CRS communities (%)", pos(3, 5, 3, 7),
                     "Share of NFIP policy-years, 2022 to 2024"),
        text("""
### Losses over time
The filters apply to this chart only.
""", pos(0, 12, 2, 2)),
        multi_select("state", ("state_options", "state"), ["losses_by_year"], "State", pos(2, 12, 1, 2)),
        multi_select("crs_group", ("crs_group_options", "crs_group"), ["losses_by_year"], "CRS class",
                     pos(3, 12, 2, 2)),
        range_slider("losses_by_year", "year", "Years", pos(5, 12, 1, 2)),
        combo("losses_by_year", ("year", "`year`", "Year"), ("paid", "SUM(`paid_busd`)", "Paid losses ($B)"),
              ("claims_per_1k", "SUM(`claims_per_1k_policy_years`)", "Claims per 1,000 policy-years"),
              "Paid losses and claim frequency", pos(0, 14, 6, 6),
              "National peaks: 2012 (Sandy), 2017 (Harvey, Irma, Maria), 2024 (Helene, Milton); "
              "2022 losses are mostly Ian"),
    ])


BLUE, CORAL = "#2A78D6", "#E8734A"


def _forest(vega_ok: bool, p: dict, hero: bool = False) -> dict:
    title = "Where each discount stands" if hero else "Loss cost by class against the break-even discount"
    desc = ("Dot and bar: loss cost vs non-CRS neighbors in the same county and year, with 90% interval. "
            "Tick: break-even at the scheduled discount. Coral: the discount is likely too generous "
            f"({LIKELY:.0%} or more).")
    if not vega_ok:
        return chart("bar", "class_decision", ("crs_class", "`crs_class`", "categorical", "CRS class"),
                     ("above", "SUM(`losses_above_discount_pts`)", "Points"),
                     "Losses above discount (points)", p, x_order=CLASSES, annotations=[hline(0)],
                     description="Pooled loss cost vs non-CRS neighbors plus the scheduled discount; "
                                 "above zero, the class loses more than its discount assumes")
    ink = {"expr": "colors.textPrimary.default"}
    flag = f"datum.pTooGenerous >= {100 * LIKELY:.0f}"
    color = {"condition": {"test": flag, "value": CORAL}, "value": BLUE}
    x = {"type": "quantitative", "title": "Loss cost vs non-CRS neighbors (%)", "scale": {"domain": [-60, 100]},
         "axis": {"values": [-50, -25, 0, 25, 50]}}
    y = {"field": "crsClass", "type": "nominal", "sort": CLASSES, "title": None,
         "axis": {"labelFontSize": 13, "labelPadding": 8}}
    tooltip = [{"field": "crsClass", "type": "nominal", "title": "Class"},
               {"field": "pooled", "type": "quantitative", "title": "Estimate (%)", "format": ".1f"},
               {"field": "low90", "type": "quantitative", "title": "90% low (%)", "format": ".1f"},
               {"field": "high90", "type": "quantitative", "title": "90% high (%)", "format": ".1f"},
               {"field": "breakEven", "type": "quantitative", "title": "Break-even (%)", "format": ".1f"},
               {"field": "pTooGenerous", "type": "quantitative", "title": "P(too generous) %", "format": ".0f"}]
    vl = {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
        "data": {"name": "databricks_query"},
        "width": "container", "height": "container",
        "config": {"autosize": {"type": "fit", "contains": "padding"}, "view": {"stroke": None}},
        "layer": [
            # zero line: no y encoding, so it spans the full height
            {"mark": {"type": "rule", "strokeDash": [3, 3], "opacity": 0.4, "color": ink},
             "encoding": {"x": {"datum": 0, **x}}},
            {"mark": {"type": "rule", "strokeWidth": 4, "opacity": 0.45, "strokeCap": "round"},
             "encoding": {"y": y, "x": {"field": "low90", **x}, "x2": {"field": "high90"}, "color": color}},
            {"mark": {"type": "tick", "thickness": 3, "size": 30, "color": ink},
             "encoding": {"y": y, "x": {"field": "breakEven", **x}, "tooltip": tooltip}},
            {"mark": {"type": "point", "filled": True, "size": 180, "opacity": 1},
             "encoding": {"y": y, "x": {"field": "pooled", **x}, "color": color, "tooltip": tooltip}},
            # label the classes that are likely over-discounted, just past the interval
            {"transform": [{"filter": flag},
                           {"calculate": "format(datum.pTooGenerous, '.0f') + '% likely too generous'",
                            "as": "flagLabel"}],
             "mark": {"type": "text", "align": "left", "dx": 10, "fontSize": 12, "fontWeight": "bold",
                      "color": CORAL},
             "encoding": {"y": y, "x": {"field": "high90", **x}, "text": {"field": "flagLabel"}}},
        ],
    }
    fields = {"crsClass": "`crs_class`", "pooled": "`pooled_loss_cost_effect_pct`", "low90": "`low90_pct`",
              "high90": "`high90_pct`", "breakEven": "`break_even_pct`", "pTooGenerous": "`p_too_generous_pct`"}
    return vega("class_decision", fields, vl, title, p, desc)


def pricing(f: dict, vega_ok: bool) -> dict:
    c15 = next(c for c in f["classes"] if c["bin"] == "1-5")
    net15 = f"{'+' if c15['net_musd'] >= 0 else '-'}${abs(c15['net_musd']):.1f}M"
    cols = [("crs_class", "Class", "string"),
            ("loss_cost_interval", "Loss cost vs non-CRS % (90% interval)", "string"),
            ("scheduled_discount_pct", "Scheduled %", "float"),
            ("best_discount_pct", "Best now %", "float"),
            ("p_too_generous_pct", "P(too generous) %", "float", "0"),
            ("expected_gap_musd", "Net gap $M/yr", "float"),
            ("expected_abs_gap_musd", "Abs. gap $M/yr", "float"),
            ("evpi_musd", "Perfect info $M/yr", "float"),
            ("evsi_half_variance_musd", "Halve uncertainty $M/yr", "float")]
    return page("pricing", "Pricing decision", [
        text(f"""
## Pricing decision by CRS class
Class relativities are partially pooled in a Bayesian hierarchical model, with uncertainty widened by placebo
tests. **Net gap** is premium at the scheduled discount minus expected loss: negative means the discount is too
generous. It can disagree with the probability (Class 1-5 is {c15['p']:.0%} likely too generous yet
{net15} net) because the loss estimate is skewed. **Abs. gap** sums to the overview's
${f['abs_gap_musd']:.0f}M. Re-pricing on today's evidence barely shrinks it; better data (recovering historical CRS
classes) is worth far more.
""", pos(0, 0, 6, 3)),
        chart("bar", "discount_compare", ("crs_class", "`crs_class`", "categorical", "CRS class"),
              ("discount", "SUM(`discount_pct`)", "Discount (%)"), "Scheduled vs best-supported discount",
              pos(0, 3, 4, 6), color=("discount_type", "`discount_type`", "Discount"), x_order=CLASSES,
              grouped=True, color_map={"Scheduled": "#8A99A8", "Best on current evidence": BLUE},
              description="Best discount minimizes the expected absolute gap; below zero is a surcharge",
              annotations=[hline(0)]),
        chart("bar", "class_decision", ("crs_class", "`crs_class`", "categorical", "CRS class"),
              ("p", "SUM(`p_too_generous_pct`)", "Probability (%)"),
              "Probability the discount is too generous", pos(4, 3, 2, 6), x_order=CLASSES,
              annotations=[hline(50, "Coin flip")]),
        table("class_decision", cols, "Decision table", pos(0, 9, 6, 5),
              "Dollars are expected annual loss at 2022 to 2024 exposure and 2010 to 2024 loss cost"),
    ])


def designs(f: dict) -> dict:
    return page("designs", "Measurement designs", [
        text(f"""
## Can the causal effect of joining CRS be measured?
**Left:** placebo simulations of the entry difference-in-differences, comparing communities within the same county
and year. The original design (year fixed effects only) was dropped: with no true effect it reported
{f['biased_pct']:+.0f}% and flagged fake effects {f['biased_fpr']:.0%} of the time (table below). Even the fixed
design detects only large effects. **Right:** a proposed randomized recruitment trial. It can measure whether
outreach raises participation, but not a realistic effect on claims.
""", pos(0, 0, 6, 2)),
        chart("line", "entry_design", ("n_entrants", "`n_entrants`", "quantitative", "CRS entrants in the design"),
              ("mde", "AVG(`mde_pct`)", "Minimum detectable effect (%)"),
              "Observational design: detectable effect", pos(0, 2, 3, 7),
              color=("design_outcome", "`design_outcome`", "Outcome"),
              description="Joining CRS usually starts at Class 9 or 8, a 5 to 10% discount",
              annotations=[hline(10, "10%: a Class 8 discount"), vline(f["entrants"], f"{f['entrants']} actual")]),
        chart("line", "experiment_power", ("communities", "`communities`", "quantitative", "Communities in the trial"),
              ("itt", "AVG(`itt_mde_pct`)", "Detectable drop from the offer (%)"),
              "Randomized trial: detectable drop in claims", pos(3, 2, 3, 7),
              color=("design", "`design`", "Design"),
              description="Realistic offer effect: 25% uptake x 20% fewer claims among joiners = 5%",
              annotations=[hline(5, "5%: realistic offer effect")]),
        table("entry_design_check", [("design", "Design", "string"), ("outcome", "Outcome", "string"),
                                     ("bias_under_no_effect_pct", "Estimate with no true effect (%)", "float"),
                                     ("false_positive_rate_pct", "False positives at reported SEs (%)", "float", "0"),
                                     ("mde_pct", "Detectable effect (%)", "float"),
                                     ("verdict", "Verdict", "string")],
              f"Design check at the {f['entrants']} actual entrants", pos(0, 9, 6, 3),
              "Placebo entry dates given to communities that never joined: a sound design reports about 0%. "
              "Reported standard errors still overstate significance, so detectable effects use the placebo spread."),
        table("experiment_power", [("design", "Design", "string"), ("trial_size", "Size", "string"),
                                   ("communities", "Communities", "integer"),
                                   ("itt_mde_label", "Offer effect detectable (%)", "string"),
                                   ("complier_p50_label", "Joining effect, 50% uptake (%)", "string"),
                                   ("complier_p25_label", "Joining effect, 25% uptake (%)", "string")],
              "Trial power", pos(0, 12, 6, 5), "Drops of 90% or more are shown as not detectable"),
    ])


def health() -> dict:
    return page("health", "Pipeline health", [
        text("""
## Pipeline health
Latest run of the Databricks job: silver loads reconciled against their files, and quality checks that are the same
code as the local DuckDB pipeline (a CI test runs both on Spark and DuckDB and compares results). An error-level
failure stops the job before the analysis runs; warnings are recorded for review.
""", pos(0, 0, 6, 2)),
        counter("pipeline_health", "MAX(`days_since_run`)", "Days since last run", pos(0, 2, 1, 2),
                "The job is scheduled monthly (paused in development mode)", plain(1)),
        counter("pipeline_health", "COUNT(`check_name`)", "Checks run", pos(1, 2, 1, 2)),
        counter("pipeline_health", "SUM(`passed_flag`)", "Checks passed", pos(2, 2, 1, 2)),
        counter("pipeline_health", "SUM(`warning`)", "Warnings", pos(3, 2, 1, 2)),
        counter("pipeline_health", "SUM(`blocking`)", "Blocking failures", pos(4, 2, 1, 2)),
        counter("pipeline_health", "MAX(`runs_recorded`)", "Runs recorded", pos(5, 2, 1, 2)),
        table("pipeline_health", [("check_name", "Check", "string"), ("layer", "Layer", "string"),
                                  ("severity", "Severity", "string"), ("status", "Status", "string"),
                                  ("failing_rows", "Failing rows", "integer"),
                                  ("failing_share_pct", "Share of table (%)", "float", "0.00")],
              "Quality checks, latest run", pos(0, 4, 4, 8),
              "Failures first. Share is blank for checks that compare totals or count groups."),
        chart("bar", "dq_history", ("run", "`run`", "categorical", "Run"), ("checks", "SUM(`checks`)", "Checks"),
              "Check outcomes by run", pos(4, 4, 2, 8), color=("status", "`status`", "Status"),
              color_map={"Passed": "#3B9C7A", "Warning": "#E8A33D", "Blocking": "#E2574C"}),
        table("load_log", [("dataset", "Table", "string"), ("file_rows", "File rows", "integer"),
                           ("table_rows", "Table rows", "integer"), ("status", "Status", "string"),
                           ("loaded_at", "Loaded at", "datetime")], "Latest silver loads", pos(0, 12, 6, 3)),
    ])


# One palette for every page; the first color is the forest plot's blue, so default series match it.
THEME = {
    "canvasBackgroundColor": {"light": "#F6F7F9", "dark": "#12181D"},
    "widgetBackgroundColor": {"light": "#FFFFFF", "dark": "#1B232A"},
    "fontColor": {"light": "#1B232A", "dark": "#E8ECF0"},
    "selectionColor": {"light": "#2272B4", "dark": "#8ACAFF"},
    "visualizationColors": [BLUE, CORAL, "#3B9C7A", "#8A99A8", "#E8A33D", "#7B5EA7", "#5AB4C5"],
    "widgetHeaderAlignment": "LEFT",
}


def build(vega_ok: bool = True) -> dict:
    global _n
    _n = 0
    f = facts()
    return {"datasets": datasets(), "pages": [overview(f, vega_ok), pricing(f, vega_ok), designs(f), health()],
            "uiSettings": {"theme": THEME}}


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(build(vega_ok="--standard" not in sys.argv), indent=2) + "\n")
    d = json.loads(OUT.read_text())
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(d['datasets'])} datasets, "
          f"{sum(len(p['layout']) for p in d['pages'])} widgets on {len(d['pages'])} pages")


if __name__ == "__main__":
    main()
