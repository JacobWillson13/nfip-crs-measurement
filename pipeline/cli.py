"""Command line entry point.

    python -m pipeline.cli ingest [--states NC SC] [--force]
    python -m pipeline.cli transform
    python -m pipeline.cli quality
    python -m pipeline.cli run [--states ...] [--force]     # ingest + transform + quality
    python -m pipeline.cli discover NfipPolicies             # inspect a dataset in the catalog
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid

from pipeline.config import DEFAULT_CONFIG, load_config
from pipeline.ingest.client import OpenFemaClient
from pipeline.ingest.loader import load_partition
from pipeline.ops import connect
from pipeline.quality.checks import has_blocking_failure, run_checks
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


def cmd_ingest(cfg, states, force) -> int:
    client = _client(cfg)
    con = connect(cfg.warehouse)
    run_id = uuid.uuid4().hex[:12]
    bad = 0
    for ds in cfg.datasets.values():
        if not ds.enabled:
            continue
        for state in states:
            try:
                res = load_partition(client, con, ds, state, cfg.lake_root, force=force, run_id=run_id)
            except Exception:
                log.exception("%s state=%s failed", ds.key, state)
                bad += 1
                continue
            if res.status not in ("success", "skipped_unchanged"):
                bad += 1
    con.close()
    return 1 if bad else 0


def cmd_transform(cfg) -> int:
    con = connect(cfg.warehouse)
    build(con, cfg.lake_root)
    con.close()
    return 0


def cmd_quality(cfg) -> int:
    con = connect(cfg.warehouse)
    results = run_checks(con)
    con.close()
    return 1 if has_blocking_failure(results) else 0


def cmd_discover(cfg, name) -> int:
    entries = _client(cfg).dataset_metadata(name)
    if not entries:
        print(f"No catalog entries named {name!r}")
        return 1
    keep = ["name", "version", "title", "webService", "recordCount", "lastDataSetRefresh", "depDate"]
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
    sub.add_parser("transform")
    sub.add_parser("quality")
    d = sub.add_parser("discover")
    d.add_argument("name", help="OpenFEMA dataset name, e.g. NfipClaims")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    cfg = load_config(args.config)
    states = [s.upper() for s in (getattr(args, "states", None) or cfg.states)]

    if args.cmd == "ingest":
        return cmd_ingest(cfg, states, args.force)
    if args.cmd == "transform":
        return cmd_transform(cfg)
    if args.cmd == "quality":
        return cmd_quality(cfg)
    if args.cmd == "discover":
        return cmd_discover(cfg, args.name)
    if args.cmd == "run":
        rc = cmd_ingest(cfg, states, args.force)
        if rc:
            log.error("Ingest had failures; stopping before transform")
            return rc
        cmd_transform(cfg)
        return cmd_quality(cfg)
    return 2


if __name__ == "__main__":
    sys.exit(main())
