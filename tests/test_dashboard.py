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


def test_committed_definition_matches_generator():
    committed = json.loads((ROOT / "databricks" / "dashboards" / "nfip_crs.lvdash.json").read_text())
    assert committed == bd.build(), "run: python databricks/build_dashboard.py"


def test_widgets_reference_real_datasets_and_fields():
    d = bd.build()
    sql = {ds["name"]: "".join(ds["queryLines"]) for ds in d["datasets"]}
    assert len(sql) == len(d["datasets"])
    for _, item in _widgets(d):
        w = item["widget"]
        if "multilineTextboxSpec" in w:
            continue
        queries = {q["name"]: q["query"] for q in w["queries"]}
        for q in queries.values():
            assert q["datasetName"] in sql, (w["name"], q["datasetName"])
            for f in q["fields"]:
                for col in re.findall(r"`([^`]+)`", f["expression"]):
                    known = col == "associative_filter_predicate_group"
                    assert known or re.search(rf"\b{col}\b", sql[q["datasetName"]]), (w["name"], col)
        enc = w["spec"]["encodings"]
        refs = [enc[k] for k in ("x", "y", "color", "value") if k in enc]
        refs += enc.get("columns", []) + enc.get("fields", [])
        for r in refs:
            q = queries[r.get("queryName", "main_query")]
            assert r["fieldName"] in {f["name"] for f in q["fields"]}, (w["name"], r["fieldName"])


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
