import json

import pyarrow.parquet as pq

from pipeline.ingest.loader import load_partition


def _partition(lake, state):
    return lake / "bronze" / "nfip_claims" / f"state={state}"


def test_loads_partition_and_reconciles(client, con, claims_ds, tmp_path):
    lake = tmp_path / "lake"
    res = load_partition(client, con, claims_ds, "TX", lake)
    assert res.status == "success"
    assert res.expected_rows == res.landed_rows == 4

    part = _partition(lake, "TX")
    assert pq.read_table(part).num_rows == 4
    manifest = json.loads((part / "_manifest.json").read_text())
    assert manifest["rows"] == 4 and manifest["source_refresh"]
    assert not (lake / "bronze" / "nfip_claims" / "_staging").exists() or not any(
        (lake / "bronze" / "nfip_claims" / "_staging").iterdir())

    status, landed = con.execute("select status, landed_rows from ops.run_log").fetchone()
    assert (status, landed) == ("success", 4)


def test_unchanged_source_is_skipped(client, con, claims_ds, tmp_path):
    lake = tmp_path / "lake"
    load_partition(client, con, claims_ds, "VA", lake)
    res = load_partition(client, con, claims_ds, "VA", lake)
    assert res.status == "skipped_unchanged"


def test_new_source_refresh_triggers_reload(client, con, claims_ds, fake_server, tmp_path):
    lake = tmp_path / "lake"
    load_partition(client, con, claims_ds, "VA", lake)
    fake_server.refresh = "2026-10-09T00:00:00.000Z"
    assert load_partition(client, con, claims_ds, "VA", lake).status == "success"


def test_force_reloads_unchanged_source(client, con, claims_ds, tmp_path):
    lake = tmp_path / "lake"
    load_partition(client, con, claims_ds, "VA", lake)
    assert load_partition(client, con, claims_ds, "VA", lake, force=True).status == "success"


def test_count_mismatch_keeps_last_good_partition(client, con, claims_ds, fake_server, tmp_path):
    lake = tmp_path / "lake"
    load_partition(client, con, claims_ds, "TX", lake)
    fake_server.refresh = "2026-10-09T00:00:00.000Z"
    fake_server.count_offset = 1  # API says 5 rows, data has 4
    res = load_partition(client, con, claims_ds, "TX", lake)
    assert res.status == "count_mismatch"
    assert pq.read_table(_partition(lake, "TX")).num_rows == 4  # untouched
    # Watermark still points at the last successful load.
    wm = con.execute("select source_refresh from ops.watermarks where partition_key = 'state=TX'").fetchone()[0]
    assert wm == "2026-09-09T00:00:00.000Z"


def test_reload_replaces_rather_than_appends(client, con, claims_ds, tmp_path):
    lake = tmp_path / "lake"
    load_partition(client, con, claims_ds, "TX", lake)
    load_partition(client, con, claims_ds, "TX", lake, force=True)
    assert pq.read_table(_partition(lake, "TX")).num_rows == 4
