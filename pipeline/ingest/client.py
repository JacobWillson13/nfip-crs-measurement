"""Thin OpenFEMA API client: paging, retries, dataset metadata, and record counts."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://www.fema.gov/api/open"
RETRY_STATUS = {429, 500, 502, 503, 504}


class OpenFemaError(RuntimeError):
    """Raised when the API returns an unrecoverable error."""


class OpenFemaClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        base_url: str = BASE_URL,
        page_size: int = 10_000,
        max_retries: int = 5,
        backoff_seconds: float = 2.0,
        timeout_seconds: float = 120,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.timeout_seconds = timeout_seconds
        self.sleep = sleep

    def _get(self, path: str, params: dict) -> dict:
        url = f"{self.base_url}/{path}"
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout_seconds)
            except requests.RequestException as exc:
                last_error = exc
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code not in RETRY_STATUS:
                    raise OpenFemaError(f"HTTP {resp.status_code} from {url}: {resp.text[:300]}")
                last_error = OpenFemaError(f"HTTP {resp.status_code} from {url}")
            if attempt == self.max_retries:
                break
            wait = self.backoff_seconds * 2 ** (attempt - 1)
            log.warning("Attempt %d/%d failed (%s); retrying in %.1fs", attempt, self.max_retries, last_error, wait)
            self.sleep(wait)
        raise OpenFemaError(f"Giving up on {url} after {self.max_retries} attempts: {last_error}")

    def dataset_metadata(self, name: str, version: int | None = None) -> list[dict]:
        """Catalog entries for a dataset. Used for discovery and the refresh watermark."""
        flt = f"name eq '{name}'" + (f" and version eq {version}" if version is not None else "")
        data = self._get("v1/OpenFemaDataSets", {"$filter": flt})
        return data.get("OpenFemaDataSets", [])

    def count(self, entity: str, version: int, filter_: str | None = None) -> int:
        """Server-side record count for a filter. This is the reconciliation target."""
        params = {"$top": 1, "$select": "id", "$count": "true"}
        if filter_:
            params["$filter"] = filter_
        data = self._get(f"v{version}/{entity}", params)
        return int(data["metadata"]["count"])

    def iter_pages(
        self,
        entity: str,
        version: int,
        filter_: str | None = None,
        select: list[str] | None = None,
        orderby: str = "id",
    ) -> Iterator[list[dict]]:
        """Yield pages of records. Ordering by id keeps $skip paging stable."""
        skip = 0
        while True:
            params: dict = {"$top": self.page_size, "$skip": skip, "$orderby": orderby}
            if filter_:
                params["$filter"] = filter_
            if select:
                params["$select"] = ",".join(select)
            data = self._get(f"v{version}/{entity}", params)
            rows = data.get(entity, [])
            if rows:
                yield rows
            if len(rows) < self.page_size:
                return
            skip += len(rows)
