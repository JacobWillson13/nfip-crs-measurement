"""Operational tables: run log, watermarks, and data quality results."""

from __future__ import annotations

from pathlib import Path

import duckdb

OPS_DDL = """
create schema if not exists ops;

create table if not exists ops.run_log (
    run_id          varchar,
    dataset         varchar,
    partition_key   varchar,
    started_at      timestamptz,
    finished_at     timestamptz,
    status          varchar,   -- success | skipped_unchanged | count_mismatch | failed
    expected_rows   bigint,    -- server-side count for the partition filter
    landed_rows     bigint,    -- rows written to bronze
    source_refresh  varchar,   -- dataset lastDataSetRefresh at run time
    message         varchar
);

create table if not exists ops.watermarks (
    dataset         varchar,
    partition_key   varchar,
    source_refresh  varchar,
    updated_at      timestamptz,
    primary key (dataset, partition_key)
);

create table if not exists ops.dq_results (
    run_at          timestamptz,
    check_name      varchar,
    layer           varchar,
    severity        varchar,   -- error | warn
    failing_rows    bigint,
    passed          boolean
);
"""


def connect(warehouse: Path) -> duckdb.DuckDBPyConnection:
    warehouse.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(warehouse))
    con.execute(OPS_DDL)
    return con
