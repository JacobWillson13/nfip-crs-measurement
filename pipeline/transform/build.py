"""Build silver and gold tables by running the numbered SQL files in order."""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb

log = logging.getLogger(__name__)

SQL_DIR = Path(__file__).parent / "sql"


def build(con: duckdb.DuckDBPyConnection, lake_root: Path) -> list[str]:
    params = {
        "bronze_nfip_claims": (lake_root / "bronze" / "nfip_claims" / "state=*" / "*.parquet").as_posix(),
    }
    ran = []
    for sql_file in sorted(SQL_DIR.glob("*.sql")):
        log.info("Running %s", sql_file.name)
        con.execute(sql_file.read_text().format(**params))
        ran.append(sql_file.name)
    return ran
