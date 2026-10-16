"""Build silver and gold tables by running the numbered SQL files in order.

Each SQL file declares its inputs on a `-- requires:` line. A file whose bronze
inputs are not loaded yet is skipped with a warning, so the pipeline can be
built up one source at a time.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb

log = logging.getLogger(__name__)

SQL_DIR = Path(__file__).parent / "sql"


def _sources(lake_root: Path) -> dict[str, tuple[str, bool]]:
    bronze = lake_root / "bronze"
    claims_parts = list((bronze / "nfip_claims").glob("state=*/*.parquet"))
    status_parts = list((bronze / "nfip_community_status_book" / "all").glob("*.parquet"))
    policy_files = sorted((bronze / "nfip_policies").glob("*.parquet"))
    return {
        "nfip_claims": ((bronze / "nfip_claims" / "state=*" / "*.parquet").as_posix(), bool(claims_parts)),
        "nfip_community_status_book": (
            (bronze / "nfip_community_status_book" / "all" / "*.parquet").as_posix(), bool(status_parts)),
        "nfip_policies": (policy_files[-1].as_posix() if policy_files else "", bool(policy_files)),
    }


def _requires(sql: str) -> list[str]:
    m = re.search(r"^--\s*requires:\s*(.+)$", sql, flags=re.MULTILINE)
    return [s.strip() for s in m.group(1).split(",")] if m else []


def build(con: duckdb.DuckDBPyConnection, lake_root: Path, year_min: int = 2009,
          year_max: int = 2026) -> dict[str, str]:
    sources = _sources(lake_root)
    params = {f"bronze_{k}": path for k, (path, _) in sources.items()}
    params.update(year_min=year_min, year_max=year_max)
    built: set[str] = set()
    status: dict[str, str] = {}
    for sql_file in sorted(SQL_DIR.glob("*.sql")):
        sql = sql_file.read_text()
        missing = []
        for req in _requires(sql):
            if req in sources and not sources[req][1]:
                missing.append(req)
            elif req not in sources and req not in built:
                missing.append(req)
        if missing:
            log.warning("Skipping %s: inputs not available yet: %s", sql_file.name, ", ".join(missing))
            status[sql_file.name] = "skipped"
            continue
        log.info("Running %s", sql_file.name)
        con.execute(sql.format(**params))
        built.add(sql_file.stem.split("_", 1)[1])
        status[sql_file.name] = "built"
    return status
