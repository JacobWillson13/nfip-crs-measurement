"""Command line entry point.

    python -m pipeline.cli ingest [--states NC SC] [--force]   # API datasets: claims, status book
    python -m pipeline.cli download nfip_policies              # bulk download, then register
    python -m pipeline.cli register nfip_policies --file PATH  # register a browser-downloaded file
    python -m pipeline.cli transform
    python -m pipeline.cli quality
    python -m pipeline.cli run [--states ...] [--force]        # ingest + transform + quality
    python -m pipeline.cli report                              # feasibility gates 1 and 2
    python -m pipeline.cli export                              # panel to data/export/ to share
    python -m pipeline.cli discover NfipPolicies               # inspect a dataset in the catalog
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from pathlib import Path

from pipeline.config import DEFAULT_CONFIG, REPO_ROOT, load_config
from pipeline.ingest.bulk import BulkDownloadBlocked, download, register
from pipeline.ingest.client import OpenFemaClient
from pipeline.ingest.loader import load_partition
from pipeline.ops import connect
from pipeline.quality.checks import has_blocking_failure, run_checks
from pipeline.quality.report import feasibility_report
from pipeline.transform.build import build

log = logging.getLogger("pipeline")


def _client(cfg) -> OpenFemaClient:
    api = cfg.api
    return OpenFemaClient(
        page_size=api.get("page_size", 10_000),
        max_retries=api.get("max_retries", 5),
        backoff_seconds=api.get("backoff_seconds", 2.0),
        timeout_seconds=api.get("timeout_seconds", 120),
    )


def _connect(cfg):
    return connect(cfg.warehouse, cfg.duckdb)


def cmd_ingest(cfg, states, force) -> int:
    client = _client(cfg)
    con = _connect(cfg)
    run_id = uuid.uuid4().hex[:12]
    bad = 0
    for ds in cfg.datasets.values():
        if not ds.enabled or ds.source != "api":
            continue
        partitions = states if ds.partition_field else [None]
        for value in partitions:
            try:
                res = load_partition(client, con, ds, value, cfg.lake_root, force=force, run_id=run_id)
            except Exception:
                log.exception("%s %s failed", ds.key, value or "")
                bad += 1
                continue
            if res.status not in ("success", "skipped_unchanged"):
                bad += 1
    con.close()
    if bad:
        log.error("%d partition(s) failed; see ops.run_log", bad)
    return 1 if bad else 0


def _catalog_count(cfg, ds) -> int | None:
    try:
        entries = _client(cfg).dataset_metadata(ds.entity, ds.version)
        return int(entries[0]["recordCount"]) if entries and entries[0].get("recordCount") else None
    except Exception as exc:  # the catalog is a cross-check, not a requirement
        log.warning("Could not read catalog record count: %s", exc)
        return None


def cmd_download(cfg, key, force) -> int:
    ds = cfg.datasets[key]
    try:
        path = download(ds, REPO_ROOT / "data" / "downloads")
    except BulkDownloadBlocked as exc:
        log.error("%s", exc)
        return 1
    return cmd_register(cfg, key, path, force)


def cmd_register(cfg, key, file, force) -> int:
    ds = cfg.datasets[key]
    con = _connect(cfg)
    try:
        res = register(con, ds, Path(file), cfg.lake_root, _catalog_count(cfg, ds), force=force)
    finally:
        con.close()
    return 0 if res.status in ("success", "skipped_unchanged") else 1


def cmd_transform(cfg) -> int:
    con = _connect(cfg)
    build(con, cfg.lake_root, cfg.year_min, cfg.year_max)
    con.close()
    return 0


def cmd_quality(cfg) -> int:
    con = _connect(cfg)
    results = run_checks(con)
    con.close()
    return 1 if has_blocking_failure(results) else 0


def cmd_report(cfg) -> int:
    con = _connect(cfg)
    out = REPO_ROOT / "data" / "export"
    text = feasibility_report(con, out)
    con.close()
    print(text)
    return 0


def cmd_export(cfg) -> int:
    con = _connect(cfg)
    out = REPO_ROOT / "data" / "export"
    out.mkdir(parents=True, exist_ok=True)
    tables = ["gold.community_year_panel", "gold.community_year_claims", "silver.community_status"]
    for table in tables:
        target = out / f"{table.split('.', 1)[1]}.parquet"
        con.execute(f"copy {table} to '{target.as_posix()}' (format parquet, compression zstd)")
        rows = con.execute(f"select count(*) from {table}").fetchone()[0]
        print(f"Wrote {rows:,} rows to {target} ({target.stat().st_size / 1e6:.1f} MB)")

    # CSVs for the Power BI service (browser upload takes CSV, not Parquet).
    bi = out / "bi"
    bi.mkdir(exist_ok=True)
    bi_tables = {
        "community_year": """
            select community_id, community_name, state, county_fips, year, regime,
                   in_crs, crs_class, crs_status_basis, crs_discount_pct_sfha,
                   round(earned_policy_years, 2) as earned_policy_years,
                   round(earned_premium, 0) as earned_premium, round(paid_total, 0) as paid_total,
                   n_claims, round(share_exposure_sfha, 3) as share_exposure_sfha
            from gold.community_year_panel where year between 2010 and 2024""",
        "dq_results": """
            select * from ops.dq_results
            where run_at = (select max(run_at) from ops.dq_results)""",
        "run_log": "select * from ops.run_log order by finished_at",
    }
    for name, sql in bi_tables.items():
        target = bi / f"{name}.csv"
        con.execute(f"copy ({sql}) to '{target.as_posix()}' (header, delimiter ',')")
        print(f"Wrote {target} ({target.stat().st_size / 1e6:.1f} MB)")
    con.close()
    return 0


def cmd_discover(cfg, name) -> int:
    entries = _client(cfg).dataset_metadata(name)
    if not entries:
        print(f"No catalog entries named {name!r}")
        return 1
    keep = ["name", "version", "title", "webService", "recordCount", "lastDataSetRefresh", "depDate",
            "distribution"]
    for e in entries:
        print(json.dumps({k: e.get(k) for k in keep}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pipeline")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("ingest", "run"):
        sp = sub.add_parser(name)
        sp.add_argument("--states", nargs="+", help="override states in config")
        sp.add_argument("--force", action="store_true", help="reload even if source is unchanged")
    dl = sub.add_parser("download")
    dl.add_argument("dataset")
    dl.add_argument("--force", action="store_true")
    rg = sub.add_parser("register")
    rg.add_argument("dataset")
    rg.add_argument("--file", required=True)
    rg.add_argument("--force", action="store_true")
    for name in ("transform", "quality", "report", "export"):
        sub.add_parser(name)
    d = sub.add_parser("discover")
    d.add_argument("name", help="OpenFEMA dataset name, e.g. NfipClaims")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    cfg = load_config(args.config)
    states = [s.upper() for s in (getattr(args, "states", None) or cfg.states)]

    match args.cmd:
        case "ingest":
            return cmd_ingest(cfg, states, args.force)
        case "download":
            return cmd_download(cfg, args.dataset, args.force)
        case "register":
            return cmd_register(cfg, args.dataset, args.file, args.force)
        case "transform":
            return cmd_transform(cfg)
        case "quality":
            return cmd_quality(cfg)
        case "report":
            return cmd_report(cfg)
        case "export":
            return cmd_export(cfg)
        case "discover":
            return cmd_discover(cfg, args.name)
        case "run":
            rc = cmd_ingest(cfg, states, args.force)
            if rc:
                log.error("Ingest had failures; stopping before transform")
                return rc
            cmd_transform(cfg)
            return cmd_quality(cfg)
    return 2


if __name__ == "__main__":
    sys.exit(main())
