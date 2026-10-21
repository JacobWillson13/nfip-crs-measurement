"""The generated dashboard definition is internally consistent and in sync with its SQL."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_dashboard", ROOT / "databricks" / "build_dashboard.py")
bd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bd)

GRID = 6


def _widgets(d):
    for p in d["pages"]:
        for item in p["layout"]:
            yield p["name"], item


def _datasets(d) -> dict[str, dict]:
    return {ds["name"]: ds for ds in d["datasets"]}


def _sql(ds: dict) -> str:
    return "".join(ds["queryLines"])


def test_committed_definition_matches_generator():
    committed = json.loads((ROOT / "databricks" / "dashboards" / "nfip_crs.lvdash.json").read_text())
    assert committed == bd.build(), "run: python databricks/build_dashboard.py"


def test_standard_only_build_has_no_custom_widgets():
    types = {item["widget"].get("spec", {}).get("widgetType") for _, item in _widgets(bd.build(vega_ok=False))}
    assert "custom-vega-viz" not in types


def test_parameters_declared_and_used():
    for ds in bd.build()["datasets"]:
        used = set(re.findall(r"(?<![:\w]):([a-z_]+)\b", _sql(ds)))
        declared = {p["keyword"] for p in ds.get("parameters", [])}
        assert used == declared, (ds["name"], used, declared)
        assert "{" not in _sql(ds), (ds["name"], "unfilled placeholder")


def _encoding_refs(spec: dict) -> list[dict]:
    enc = spec["encodings"]
    refs = [enc[k] for k in ("x", "y", "color", "value", "size") if k in enc and "fieldName" in enc[k]]
    y = enc.get("y", {})
    for part in ("primary", "secondary"):
        refs += y.get(part, {}).get("fields", [])
    refs += enc.get("columns", [])
    if "region" in enc:
        refs += [v for v in enc["region"].values() if isinstance(v, dict) and v.get("type") == "field"]
    if spec["widgetType"] != "range-slider":
        refs += [r for r in enc.get("fields", []) if "fieldName" in r]
    return refs


def test_widgets_reference_real_datasets_fields_and_parameters():
    d = bd.build()
    dss = _datasets(d)
    for _, item in _widgets(d):
        w = item["widget"]
        if "multilineTextboxSpec" in w:
            continue
        queries = {q["name"]: q["query"] for q in w["queries"]}
        assert len(queries) == len(w["queries"]), w["name"]
        for q in queries.values():
            assert q["datasetName"] in dss, (w["name"], q["datasetName"])
            sql = _sql(dss[q["datasetName"]])
            for f in q.get("fields", []):
                for col in re.findall(r"`([^`]+)`", f["expression"]):
                    assert re.search(rf"\b{col}\b", sql), (w["name"], col)
            declared = {p["keyword"] for p in dss[q["datasetName"]].get("parameters", [])}
            for p in q.get("parameters", []):
                assert p["keyword"] in declared, (w["name"], p["keyword"])
        s = w["spec"]
        for r in _encoding_refs(s):
            q = queries[r.get("queryName", "main_query")]
            assert r["fieldName"] in {f["name"] for f in q["fields"]}, (w["name"], r["fieldName"])
        filters = s["widgetType"].startswith("filter") or s["widgetType"] == "range-slider"
        for r in s["encodings"].get("fields", []) if filters else []:
            q = queries[r["queryName"]]
            if "parameterName" in r:
                assert r["parameterName"] in {p["keyword"] for p in q["parameters"]}, w["name"]
            elif s["widgetType"] == "range-slider":
                assert {f["name"] for f in q["fields"]} == {f"min({r['fieldName']})", f"max({r['fieldName']})"}


def test_custom_charts_bind_their_fields():
    for _, item in _widgets(bd.build()):
        s = item["widget"].get("spec", {})
        if s.get("widgetType") != "custom-vega-viz":
            continue
        assert s["data"]["queryName"] == "main_query"
        vl = json.loads(s["jsonSpec"]["spec"])
        assert vl["data"] == {"name": "databricks_query"}
        bound = {f["fieldName"] for f in s["encodings"]["fields"]}
        assert bound == {f["name"] for f in item["widget"]["queries"][0]["query"]["fields"]}
        made = set(re.findall(r'"as": "(\w+)"', s["jsonSpec"]["spec"]))   # computed by Vega-Lite transforms
        used = set(re.findall(r'"field": "(\w+)"', s["jsonSpec"]["spec"])) - made
        assert used <= bound, used - bound


def test_annotations_are_constant_numbers():
    for _, item in _widgets(bd.build()):
        for a in item["widget"].get("spec", {}).get("annotations", []):
            axis = "y" if a["type"] == "horizontal-line" else "x"
            v = a["encodings"][axis]
            float(v["dataValue"])
            assert v["dataType"] in {"INTEGER", "DOUBLE"}
            if a["type"] == "vertical-line":
                x = item["widget"]["spec"]["encodings"]["x"]["scale"]["type"]
                assert x == "quantitative", "vertical lines need a numeric x axis"


def test_layout_fits_grid_without_overlaps():
    d = bd.build()
    names = [item["widget"]["name"] for _, item in _widgets(d)]
    assert len(names) == len(set(names))
    for p in d["pages"]:
        cells = set()
        for item in p["layout"]:
            x, y, w, h = (item["position"][k] for k in ("x", "y", "width", "height"))
            assert 0 <= x and x + w <= GRID, item["widget"]["name"]
            mine = {(i, j) for i in range(x, x + w) for j in range(y, y + h)}
            assert not cells & mine, (p["name"], item["widget"]["name"])
            cells |= mine


def test_prose_numbers_come_from_committed_results():
    f = bd.facts()
    md = "".join(next(item for _, item in _widgets(bd.build()))["widget"]["multilineTextboxSpec"]["lines"])
    assert f"cost {-f['loss_step']:.1f}% against" in md
    assert f"${f['evsi_musd']:.0f}M a year" in md
    for c in f["classes"]:
        assert (f"{c['p']:.0%}" in md) == (c["p"] >= bd.LIKELY), c["bin"]


def test_hero_fits_above_the_fold():
    """The overview is the README hero image: answer, counters, and the forest plot within 12 rows."""
    first = bd.build()["pages"][0]
    kinds = {item["widget"].get("spec", {}).get("widgetType") for item in first["layout"]
             if item["position"]["y"] + item["position"]["height"] <= 12}
    assert {"counter", "custom-vega-viz", "choropleth-map"} <= kinds


def test_failure_shares_cover_row_level_checks_only():
    values, counts = bd.check_table_sql()
    pairs = dict(re.findall(r"\('(\w+)', '(\w+)'\)", values))
    assert "claim_id_not_null" in pairs and "claim_id_unique" not in pairs
    assert pairs["construction_date_sentinel"] == "silver_nfip_claims"
    for t in set(pairs.values()):
        assert f"from {bd.TABLE_PREFIX}{t}" in counts


# Widget fields accept a column or a single aggregate of one column. Arithmetic or CASE inside a
# widget renders empty on Databricks, so calculations belong in dashboard_queries.sql.
SIMPLE = re.compile(r"^(`[^`]+`|(SUM|AVG|MIN|MAX|COUNT|COUNT_IF)\(`[^`]+`\))$")
TABLE_SPEC_KEYS = {"invisibleColumns", "itemsPerPage", "paginationSize", "withRowNumber", "condensed",
                   "allowHTMLByDefault"}


def test_widget_fields_are_plain_aggregates():
    for _, item in _widgets(bd.build()):
        for q in item["widget"].get("queries", []):
            for f in q["query"].get("fields", []):
                assert SIMPLE.match(f["expression"]), (item["widget"]["name"], f["expression"])


def test_tables_match_exported_spec():
    for _, item in _widgets(bd.build()):
        spec = item["widget"].get("spec", {})
        if spec.get("widgetType") != "table":
            continue
        assert TABLE_SPEC_KEYS <= spec.keys(), item["widget"]["name"]
        for c in spec["encodings"]["columns"]:
            assert c["type"] in {"string", "integer", "float", "datetime"}, c["fieldName"]
            if c["type"] in {"integer", "float"}:
                assert "numberFormat" in c, c["fieldName"]
            if c["type"] == "datetime":
                assert "dateTimeFormat" in c, c["fieldName"]
