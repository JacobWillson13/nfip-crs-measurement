"""Land one dataset partition (one state) from the API into bronze Parquet.

Each partition is fully replaced on every load instead of appended:
FEMA revises historical claims (payments, recoveries, reclassified fields), so an
id-based append would keep stale versions. A full per-state replace is idempotent
and cheap at state granularity.

Write path: stage to _staging/<run_id>, reconcile row count against the API's
server-side count, then swap into place. A failed or mismatched run never
touches the last good partition.
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from pipeline.config import DatasetConfig
from pipeline.ingest.client import OpenFemaClient
from pipeline.ingest.schema import SCHEMAS, check_fields_present, to_arrow

log = logging.getLogger(__name__)


@dataclass
class LoadResult:
    dataset: str
    partition_key: str
    status: str
    expected_rows: int | None = None
    landed_rows: int | None = None
    message: str = ""


def _now() -> datetime:
    return datetime.now(UTC)


def _source_refresh(client: OpenFemaClient, ds: DatasetConfig) -> str | None:
    entries = client.dataset_metadata(ds.entity, ds.version)
    if not entries:
        log.warning("No catalog entry for %s v%s; watermark disabled for this run", ds.entity, ds.version)
        return None
    return entries[0].get("lastDataSetRefresh")


def _current_watermark(con: duckdb.DuckDBPyConnection, dataset: str, key: str) -> str | None:
    row = con.execute(
        "select source_refresh from ops.watermarks where dataset = ? and partition_key = ?",
        [dataset, key],
    ).fetchone()
    return row[0] if row else None


def _log_run(con, run_id, result: LoadResult, started_at, source_refresh) -> None:
    con.execute(
        "insert into ops.run_log values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [run_id, result.dataset, result.partition_key, started_at, _now(), result.status,
         result.expected_rows, result.landed_rows, source_refresh, result.message],
    )


def load_partition(
    client: OpenFemaClient,
    con: duckdb.DuckDBPyConnection,
    ds: DatasetConfig,
    partition_value: str,
    lake_root: Path,
    force: bool = False,
    run_id: str | None = None,
) -> LoadResult:
    run_id = run_id or uuid.uuid4().hex[:12]
    started_at = _now()
    key = f"{ds.partition_field}={partition_value}"
    fields = SCHEMAS[ds.key]

    source_refresh = _source_refresh(client, ds)
    if not force and source_refresh and _current_watermark(con, ds.key, key) == source_refresh:
        result = LoadResult(ds.key, key, "skipped_unchanged", message=f"source refresh {source_refresh} already loaded")
        _log_run(con, run_id, result, started_at, source_refresh)
        log.info("%s %s: unchanged since last load, skipping", ds.key, key)
        return result

    table_root = lake_root / "bronze" / ds.key
    staging = table_root / "_staging" / run_id / key
    final = table_root / key
    staging.mkdir(parents=True, exist_ok=True)

    filter_ = f"{ds.partition_field} eq '{partition_value}'"
    try:
        expected = client.count(ds.entity, ds.version, filter_)
        landed = 0
        for i, page in enumerate(client.iter_pages(ds.entity, ds.version, filter_, list(fields))):
            if i == 0:
                check_fields_present(page, fields)
            table = to_arrow(page, fields)
            pq.write_table(table, staging / f"part-{i:05d}.parquet", compression="zstd")
            landed += table.num_rows
            log.info("%s %s: page %d, %d rows so far", ds.key, key, i, landed)
    except Exception as exc:
        shutil.rmtree(table_root / "_staging" / run_id, ignore_errors=True)
        result = LoadResult(ds.key, key, "failed", message=f"{type(exc).__name__}: {exc}")
        _log_run(con, run_id, result, started_at, source_refresh)
        raise

    if landed != expected:
        shutil.rmtree(table_root / "_staging" / run_id, ignore_errors=True)
        result = LoadResult(ds.key, key, "count_mismatch", expected, landed,
                            "landed rows differ from API count; last good partition kept")
        _log_run(con, run_id, result, started_at, source_refresh)
        log.error("%s %s: expected %d rows, landed %d", ds.key, key, expected, landed)
        return result

    manifest = {
        "run_id": run_id,
        "dataset": ds.key,
        "entity": ds.entity,
        "version": ds.version,
        "partition": key,
        "rows": landed,
        "source_refresh": source_refresh,
        "fetched_at": _now().isoformat(),
        "fields": fields,
    }
    (staging / "_manifest.json").write_text(json.dumps(manifest, indent=2))

    # Swap: move the old partition aside, promote staging, then drop the old copy.
    trash = table_root / "_trash" / run_id / key
    if final.exists():
        trash.parent.mkdir(parents=True, exist_ok=True)
        final.rename(trash)
    final.parent.mkdir(parents=True, exist_ok=True)
    staging.rename(final)
    shutil.rmtree(table_root / "_trash" / run_id, ignore_errors=True)
    shutil.rmtree(table_root / "_staging" / run_id, ignore_errors=True)

    con.execute(
        "insert or replace into ops.watermarks values (?, ?, ?, ?)",
        [ds.key, key, source_refresh, _now()],
    )
    result = LoadResult(ds.key, key, "success", expected, landed)
    _log_run(con, run_id, result, started_at, source_refresh)
    log.info("%s %s: loaded %d rows", ds.key, key, landed)
    return result
