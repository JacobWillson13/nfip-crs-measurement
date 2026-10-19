"""Check the Databricks run against the local pipeline.

    python databricks/verify.py

Queries the Delta tables through a SQL warehouse (Databricks CLI, default profile) and
compares them with the local exports and committed results: gold row counts, silver load
reconciliation, quality check results, and the headline estimates. Exits non-zero on any
mismatch.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CATALOG, SCHEMA = "workspace", "nfip"
T = f"{CATALOG}.{SCHEMA}."


def cli(*args: str, body: dict | None = None) -> dict | list:
    cmd = ["databricks", *args, "-o", "json"]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"databricks {' '.join(args)} failed: {out.stderr.strip()}")
    return json.loads(out.stdout)


def warehouse_id() -> str:
    whs = cli("warehouses", "list")
    if not whs:
        sys.exit("No SQL warehouse found. Free Edition creates one; check SQL Warehouses in the UI.")
    return whs[0]["id"]


def query(wh: str, sql: str) -> pd.DataFrame:
    r = cli("api", "post", "/api/2.0/sql/statements",
            body={"warehouse_id": wh, "statement": sql, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    while r["status"]["state"] in ("PENDING", "RUNNING"):
        time.sleep(3)
        r = cli("api", "get", f"/api/2.0/sql/statements/{r['statement_id']}")
    if r["status"]["state"] != "SUCCEEDED":
        sys.exit(f"Query failed: {r['status']}\n{sql}")
    cols = [c["name"] for c in r["manifest"]["schema"]["columns"]]
    return pd.DataFrame(r["result"].get("data_array", []), columns=cols)


def main() -> None:
    wh = warehouse_id()
    problems: list[str] = []

    loads = query(wh, f"""select dataset, file_rows, table_rows, status from {T}ops_load_log
                          qualify row_number() over (partition by dataset order by loaded_at desc) = 1""")
    print("Latest silver loads\n", loads.to_string(index=False), "\n")
    problems += [f"load {r.dataset}: {r.status}" for r in loads.itertuples() if r.status != "success"]

    local_panel = len(pd.read_parquet(ROOT / "data/export/community_year_panel.parquet", columns=["year"]))
    gold = int(query(wh, f"select count(*) n from {T}gold_community_year_panel").n[0])
    print(f"Gold panel rows: Databricks {gold:,} vs local {local_panel:,}")
    if gold != local_panel:
        problems.append(f"gold panel rows {gold} != local {local_panel}")

    dq = query(wh, f"""select check_name, severity, failing_rows, passed from {T}ops_dq_results
                       where run_at = (select max(run_at) from {T}ops_dq_results)""")
    failing = dq[dq.passed != "true"]
    print(f"Quality checks: {len(dq)} run, {len(dq) - len(failing)} passed")
    if len(failing):
        print(failing.to_string(index=False))
    problems += [f"check {r.check_name}" for r in failing.itertuples() if r.severity == "error"]

    remote = query(wh, f"""select model, coefficient, effect_pct from {T}results_pricing_adequacy
                           where coefficient = 'class_steps'""").astype({"effect_pct": float})
    local = pd.read_csv(ROOT / "analysis/results/a_pricing_adequacy.csv")
    local = local[local.Coefficient == "class_steps"][["model", "effect_pct"]]
    both = remote.merge(local, on="model", suffixes=("_databricks", "_local"))
    both["diff"] = (both.effect_pct_databricks - both.effect_pct_local).abs()
    print("\nPer-class-step estimates (%)\n", both.round(4).to_string(index=False))
    problems += [f"estimate {r.model} differs by {r.diff:.4f}" for r in both.itertuples() if r.diff > 0.01]

    print("\nAll checks match." if not problems else "\nMISMATCHES:\n  " + "\n  ".join(problems))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
