"""Shared fixtures: a fake OpenFEMA server backed by real records from the v3 API."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pipeline.config import DatasetConfig
from pipeline.ingest.client import OpenFemaClient
from pipeline.ops import connect

FIXTURE = Path(__file__).parent / "fixtures" / "nfip_claims_sample.json"


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


class FakeOpenFema:
    """Mimics the subset of OpenFEMA query behavior the pipeline uses."""

    def __init__(self, records: list[dict], refresh: str = "2026-09-09T00:00:00.000Z"):
        self.records = records
        self.refresh = refresh
        self.calls: list[tuple[str, dict]] = []
        self.fail_next: list[int] = []        # status codes to return before succeeding
        self.count_offset = 0                 # simulate a count that disagrees with the data

    def get(self, url, params=None, timeout=None):
        params = params or {}
        self.calls.append((url, dict(params)))
        if self.fail_next:
            return FakeResponse(self.fail_next.pop(0))
        if url.endswith("v1/OpenFemaDataSets"):
            return FakeResponse(200, {"OpenFemaDataSets": [
                {"name": "NfipClaims", "version": 3, "lastDataSetRefresh": self.refresh}]})
        entity = url.rsplit("/", 1)[-1]
        rows = self.records
        m = re.fullmatch(r"(\w+) eq '([^']*)'", params.get("$filter", ""))
        if m:
            rows = [r for r in rows if str(r.get(m.group(1))) == m.group(2)]
        rows = sorted(rows, key=lambda r: r["id"])
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
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def fake_server(records) -> FakeOpenFema:
    return FakeOpenFema(records)


@pytest.fixture
def client(fake_server) -> OpenFemaClient:
    # page_size=2 forces multi-page paging on the small fixture.
    return OpenFemaClient(session=fake_server, page_size=2, backoff_seconds=0, sleep=lambda s: None)


@pytest.fixture
def claims_ds() -> DatasetConfig:
    return DatasetConfig(key="nfip_claims", entity="NfipClaims", version=3, partition_field="state")


@pytest.fixture
def con(tmp_path):
    c = connect(tmp_path / "warehouse.duckdb")
    yield c
    c.close()
