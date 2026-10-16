"""Load pipeline settings from config/pipeline.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "config" / "pipeline.yaml"

# 50 states, DC, and the territories that participate in the NFIP.
ALL_STATES = [
    "AK", "AL", "AR", "AZ", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI", "IA", "ID", "IL", "IN", "KS",
    "KY", "LA", "MA", "MD", "ME", "MI", "MN", "MO", "MS", "MT", "NC", "ND", "NE", "NH", "NJ", "NM", "NV",
    "NY", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY",
    "AS", "GU", "MP", "PR", "VI",
]


@dataclass
class DatasetConfig:
    key: str
    entity: str
    version: int
    source: str = "api"                 # api | bulk
    partition_field: str | None = None  # api datasets only
    key_field: str = "id"               # unique field used for ordering and counts
    enabled: bool = True
    url: str | None = None              # bulk datasets only


@dataclass
class Config:
    lake_root: Path
    warehouse: Path
    states: list[str]
    datasets: dict[str, DatasetConfig]
    year_min: int = 2009
    year_max: int = field(default_factory=lambda: date.today().year)
    api: dict = field(default_factory=dict)
    duckdb: dict = field(default_factory=dict)


def load_config(path: Path | str = DEFAULT_CONFIG) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    datasets = {key: DatasetConfig(key=key, **spec) for key, spec in raw.get("datasets", {}).items()}
    states = raw.get("states", "all")
    states = ALL_STATES if states == "all" else [s.upper() for s in states]
    panel = raw.get("panel", {})
    return Config(
        lake_root=(REPO_ROOT / raw["lake_root"]).resolve(),
        warehouse=(REPO_ROOT / raw["warehouse"]).resolve(),
        states=states,
        datasets=datasets,
        year_min=panel.get("year_min", 2009),
        year_max=panel.get("year_max") or date.today().year,
        api=raw.get("api", {}),
        duckdb=raw.get("duckdb", {}),
    )
