"""Shared fixtures.

* FakeOpenFema mimics the OpenFEMA query API (filter, select, orderby, skip, top, count).
* Claims fixtures are real v3 records. Status Book and policy fixtures are synthetic,
  built around the same community IDs so the joins can be tested end to end.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from pipeline.config import DatasetConfig
from pipeline.ingest.bulk import register
from pipeline.ingest.client import OpenFemaClient
from pipeline.ingest.loader import load_partition
from pipeline.ops import connect

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)
        self.headers: dict = {}

    def json(self) -> dict:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpenFema:
    def __init__(self, datasets: dict[str, list[dict]], refresh: str = "2026-09-09T00:00:00.000Z"):
        self.datasets = datasets
        self.refresh = refresh
        self.calls: list[tuple[str, dict]] = []
        self.fail_next: list[int] = []
        self.count_offset = 0

    def get(self, url, params=None, timeout=None, **kwargs):
        params = params or {}
        self.calls.append((url, dict(params)))
        if self.fail_next:
            return FakeResponse(self.fail_next.pop(0))
        if url.endswith("v1/OpenFemaDataSets"):
            m = re.search(r"name eq '(\w+)'", params.get("$filter", ""))
            name = m.group(1) if m else "NfipClaims"
            return FakeResponse(200, {"OpenFemaDataSets": [
                {"name": name, "version": 3, "lastDataSetRefresh": self.refresh,
                 "recordCount": len(self.datasets.get(name, []))}]})
        entity = url.rsplit("/", 1)[-1]
        rows = self.datasets[entity]
        m = re.fullmatch(r"(\w+) eq '([^']*)'", params.get("$filter", ""))
        if m:
            rows = [r for r in rows if str(r.get(m.group(1))) == m.group(2)]
        if "$orderby" in params:
            rows = sorted(rows, key=lambda r: r[params["$orderby"]])
        if params.get("$count") == "true":
            return FakeResponse(200, {"metadata": {"count": len(rows) + self.count_offset}, entity: rows[:1]})
        skip, top = int(params.get("$skip", 0)), int(params.get("$top", 1000))
        page = rows[skip: skip + top]
        if "$select" in params:
            cols = params["$select"].split(",")
            page = [{c: r.get(c) for c in cols} for r in page]
        return FakeResponse(200, {"metadata": {}, entity: page})


@pytest.fixture
def records() -> list[dict]:
    return json.loads((FIXTURES / "nfip_claims_sample.json").read_text())


@pytest.fixture
def status_records() -> list[dict]:
    return json.loads((FIXTURES / "status_book_synthetic.json").read_text())


@pytest.fixture
def fake_server(records, status_records) -> FakeOpenFema:
    return FakeOpenFema({"NfipClaims": records, "NfipCommunityStatusBook": status_records})


@pytest.fixture
def client(fake_server) -> OpenFemaClient:
    # page_size=2 forces multi-page paging on the small fixtures.
    return OpenFemaClient(session=fake_server, page_size=2, backoff_seconds=0, sleep=lambda s: None)


@pytest.fixture
def claims_ds() -> DatasetConfig:
    return DatasetConfig(key="nfip_claims", entity="NfipClaims", version=3, partition_field="state")


@pytest.fixture
def status_ds() -> DatasetConfig:
    return DatasetConfig(key="nfip_community_status_book", entity="NfipCommunityStatusBook", version=1,
                         key_field="communityIdNumber")


@pytest.fixture
def policies_ds() -> DatasetConfig:
    return DatasetConfig(key="nfip_policies", entity="NfipPolicies", version=3, source="bulk",
                         url="https://example.invalid/NfipPoliciesV3.parquet")


@pytest.fixture
def con(tmp_path):
    c = connect(tmp_path / "data" / "warehouse.duckdb")
    yield c
    c.close()


def _ts(s: str | None):
    return None if s is None else datetime.fromisoformat(s).replace(tzinfo=UTC)


# Synthetic policy transactions. Expected exposure is worked out in test_exposure_panel.py.
POLICY_ROWS = [
    # id, state, community, geoid, eff, term, cancelled (string), count, crs, zone, bldg, cont, premium
    (1, "TX", "480296", "482015327003", "2016-07-01", "2017-07-01", None, 1, 5, "AE", 200000, 50000, 1000),
    (2, "TX", "480296", "482013504003", "2017-01-01", "2018-01-01", "2017-04-01T00:00:00.000Z",
     2, 5, "X", 100000, 0, 400),
    (3, "TX", "480307", "482013430001", "2017-01-01", "2018-01-01", None, 1, 7, "X", 53000, 15000, 600),
    (4, "VA", "515531", "518100458061", "2016-01-01", "2017-01-01", None, 1, 7, "A", 227000, 21000, 900),
    (5, "VA", "515529", "517402130023", "2009-06-01", "2010-06-01", None, 1, None, "X", 250000, 0, 365),
    (6, "VA", "515529", "517402130023", "2012-05-01", "2011-05-01", None, 1, None, "X", 1, 0, 1),  # bad term
    (7, "VA", None, "517402130023", "2012-01-01", "2013-01-01", None, 1, None, "X", 1, 0, 1),     # no community
    # Pasadena entered CRS in 2012; its current Class 7 took effect 2016-10-01 (counts from 2017).
    # FEMA stamps today's class (7) on every year, so both rows below carry 7 on the policy.
    (8, "TX", "480307", "482013430001", "2011-01-01", "2012-01-01", None, 1, 7, "X", 53000, 15000, 500),
    (9, "TX", "480307", "482013430001", "2014-01-01", "2015-01-01", None, 1, 7, "X", 53000, 15000, 550),
]


def write_policies_parquet(path: Path) -> Path:
    cols = list(zip(*POLICY_ROWS, strict=True))
    table = pa.table({
        "id": pa.array(cols[0], pa.int64()),
        "propertyState": pa.array(cols[1]),
        "nfipRatedCommunityNumber": pa.array(cols[2]),
        "censusGeoid": pa.array(cols[3]),
        "policyEffectiveDate": pa.array([_ts(v) for v in cols[4]], pa.timestamp("us", tz="UTC")),
        "policyTerminationDate": pa.array([_ts(v) for v in cols[5]], pa.timestamp("us", tz="UTC")),
        "cancellationDateOfFloodPolicy": pa.array(cols[6], pa.string()),   # string dates on purpose
        "policyCount": pa.array(cols[7], pa.int64()),
        "crsClassCode": pa.array(cols[8], pa.int64()),
        "ratedFloodZone": pa.array(cols[9]),
        "totalBuildingInsuranceCoverage": pa.array(cols[10], pa.float64()),
        "totalContentsInsuranceCoverage": pa.array(cols[11], pa.float64()),
        "totalInsurancePremiumOfThePolicy": pa.array(cols[12], pa.float64()),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    return path


@pytest.fixture
def loaded_lake(client, con, claims_ds, status_ds, policies_ds, tmp_path):
    """All three sources landed in bronze: claims (VA, TX), status book, policies."""
    lake = tmp_path / "lake"
    for state in ("VA", "TX"):
        load_partition(client, con, claims_ds, state, lake)
    load_partition(client, con, status_ds, None, lake)
    src = write_policies_parquet(tmp_path / "downloads" / "NfipPoliciesV3.parquet")
    register(con, policies_ds, src, lake)
    return lake
