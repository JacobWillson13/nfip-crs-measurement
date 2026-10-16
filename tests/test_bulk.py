import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from pipeline.ingest.bulk import BulkDownloadBlocked, download, register
from pipeline.ingest.schema import SchemaDriftError
from tests.conftest import FakeResponse, write_policies_parquet


def test_register_promotes_file_with_manifest(con, policies_ds, tmp_path):
    src = write_policies_parquet(tmp_path / "downloads" / "NfipPoliciesV3.parquet")
    res = register(con, policies_ds, src, tmp_path / "lake", expected_rows=7)
    assert res.status == "success" and res.landed_rows == 7 and not res.message
    target = tmp_path / "lake" / "bronze" / "nfip_policies" / "NfipPoliciesV3.parquet"
    assert target.exists() and not src.exists()   # moved, not copied
    manifest = json.loads((target.parent / "_manifest.json").read_text())
    assert manifest["rows"] == 7 and len(manifest["sha256"]) == 64


def test_reregistering_same_file_is_skipped(con, policies_ds, tmp_path):
    lake = tmp_path / "lake"
    src = write_policies_parquet(tmp_path / "downloads" / "NfipPoliciesV3.parquet")
    register(con, policies_ds, src, lake)
    target = lake / "bronze" / "nfip_policies" / "NfipPoliciesV3.parquet"
    assert register(con, policies_ds, target, lake).status == "skipped_unchanged"


def test_catalog_count_mismatch_is_recorded_not_blocking(con, policies_ds, tmp_path):
    src = write_policies_parquet(tmp_path / "downloads" / "NfipPoliciesV3.parquet")
    res = register(con, policies_ds, src, tmp_path / "lake", expected_rows=8)
    assert res.status == "success" and "catalog reports 8" in res.message


def test_missing_required_column_is_rejected(con, policies_ds, tmp_path):
    bad = tmp_path / "downloads" / "NfipPoliciesV3.parquet"
    bad.parent.mkdir(parents=True)
    pq.write_table(pa.table({"id": [1], "propertyState": ["TX"]}), bad)
    with pytest.raises(SchemaDriftError, match="policyEffectiveDate"):
        register(con, policies_ds, bad, tmp_path / "lake")
    assert con.execute("select status from ops.run_log").fetchone()[0] == "failed"


class BlockingSession:
    def get(self, *args, **kwargs):
        return FakeResponse(403)


def test_blocked_download_explains_browser_fallback(policies_ds, tmp_path):
    with pytest.raises(BulkDownloadBlocked, match="register nfip_policies --file"):
        download(policies_ds, tmp_path, session=BlockingSession())
