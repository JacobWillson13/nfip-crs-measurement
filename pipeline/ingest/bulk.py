"""Bulk file ingestion for datasets too large for the API (policies: about 75M rows).

Bronze for a bulk dataset is FEMA's file, untouched, plus a manifest. Rewriting
75M rows into our own Parquet would add hours and disk for no gain; the schema
contract is enforced when the file is registered instead.

fema.gov sometimes refuses scripted downloads of the bulk files. `download`
tries once with a normal client and, if blocked, tells you to download it in a
browser and run `register` on the saved file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import requests

from pipeline.config import DatasetConfig
from pipeline.ingest.loader import LoadResult, _current_watermark, _log_run
from pipeline.ingest.schema import BULK_REQUIRED_COLUMNS, SchemaDriftError

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) nfip-crs-measurement/0.2"


class BulkDownloadBlocked(RuntimeError):
    """fema.gov refused the scripted download; fall back to a browser download."""


def bronze_dir(lake_root: Path, ds: DatasetConfig) -> Path:
    return lake_root / "bronze" / ds.key


def bronze_file(lake_root: Path, ds: DatasetConfig) -> Path | None:
    files = sorted(bronze_dir(lake_root, ds).glob("*.parquet"))
    return files[-1] if files else None


def download(ds: DatasetConfig, dest_dir: Path, session: requests.Session | None = None,
             chunk_bytes: int = 8 * 1024 * 1024) -> Path:
    if not ds.url:
        raise ValueError(f"{ds.key} has no url configured")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / ds.url.rsplit("/", 1)[-1]
    part = dest.with_suffix(dest.suffix + ".part")
    session = session or requests.Session()
    with session.get(ds.url, stream=True, timeout=120, headers={"User-Agent": USER_AGENT}) as resp:
        if resp.status_code in (401, 403, 429):
            raise BulkDownloadBlocked(
                f"fema.gov returned HTTP {resp.status_code} for {ds.url}.\n"
                f"Download it in a browser, then run:\n"
                f"  python -m pipeline.cli register {ds.key} --file ~/Downloads/{dest.name}"
            )
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with part.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=chunk_bytes):
                fh.write(chunk)
                done += len(chunk)
                if total and done % (chunk_bytes * 64) < chunk_bytes:
                    log.info("%s: %.1f / %.1f GB", dest.name, done / 1e9, total / 1e9)
    part.rename(dest)
    log.info("Downloaded %s (%.2f GB)", dest, dest.stat().st_size / 1e9)
    return dest


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(16 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def register(
    con: duckdb.DuckDBPyConnection,
    ds: DatasetConfig,
    source_file: Path,
    lake_root: Path,
    expected_rows: int | None = None,
    force: bool = False,
    run_id: str | None = None,
) -> LoadResult:
    """Validate a downloaded bulk file and promote it into bronze."""
    run_id = run_id or uuid.uuid4().hex[:12]
    started_at = datetime.now(UTC)
    source_file = Path(source_file).expanduser().resolve()
    if not source_file.exists():
        raise FileNotFoundError(source_file)

    log.info("Hashing %s", source_file.name)
    sha = _sha256(source_file)
    if not force and _current_watermark(con, ds.key, "all") == sha:
        result = LoadResult(ds.key, "all", "skipped_unchanged", message=f"sha256 {sha[:12]} already registered")
        _log_run(con, run_id, result, started_at, sha)
        return result

    path_sql = source_file.as_posix().replace("'", "''")
    cols = {r[0] for r in con.execute(f"describe select * from read_parquet('{path_sql}')").fetchall()}
    missing = [c for c in BULK_REQUIRED_COLUMNS.get(ds.key, []) if c not in cols]
    if missing:
        result = LoadResult(ds.key, "all", "failed", message=f"missing columns {missing}")
        _log_run(con, run_id, result, started_at, sha)
        raise SchemaDriftError(f"{source_file.name} is missing required columns {missing}. "
                               f"Columns present: {sorted(cols)}")

    rows = con.execute(f"select count(*) from read_parquet('{path_sql}')").fetchone()[0]
    note = ""
    if expected_rows is not None and rows != expected_rows:
        # The catalog count and the file can be from different refreshes; record, don't block.
        note = f"file has {rows} rows, catalog reports {expected_rows}"
        log.warning("%s: %s", ds.key, note)

    target_dir = bronze_dir(lake_root, ds)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source_file.name
    if target != source_file:
        for old in target_dir.glob("*.parquet"):
            old.unlink()
        try:
            source_file.rename(target)          # same filesystem: instant, no extra disk
        except OSError:
            shutil.copy2(source_file, target)   # different filesystem: copy
    manifest = {
        "run_id": run_id, "dataset": ds.key, "entity": ds.entity, "version": ds.version,
        "file": target.name, "rows": rows, "sha256": sha, "source_url": ds.url,
        "registered_at": datetime.now(UTC).isoformat(), "columns": sorted(cols),
    }
    (target_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2))

    con.execute("insert or replace into ops.watermarks values (?, ?, ?, ?)",
                [ds.key, "all", sha, datetime.now(UTC)])
    result = LoadResult(ds.key, "all", "success", expected_rows, rows, note)
    _log_run(con, run_id, result, started_at, sha)
    log.info("%s: registered %s (%d rows)", ds.key, target.name, rows)
    return result
