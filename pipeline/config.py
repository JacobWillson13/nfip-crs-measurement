"""Load pipeline settings from config/pipeline.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "config" / "pipeline.yaml"


@dataclass
class DatasetConfig:
    key: str
    entity: str
    version: int
    partition_field: str
    enabled: bool = True


@dataclass
class Config:
    lake_root: Path
    warehouse: Path
    states: list[str]
    datasets: dict[str, DatasetConfig]
    api: dict = field(default_factory=dict)


def load_config(path: Path | str = DEFAULT_CONFIG) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    datasets = {
        key: DatasetConfig(key=key, **spec) for key, spec in raw.get("datasets", {}).items()
    }
    return Config(
        lake_root=(REPO_ROOT / raw["lake_root"]).resolve(),
        warehouse=(REPO_ROOT / raw["warehouse"]).resolve(),
        states=[s.upper() for s in raw.get("states", [])],
        datasets=datasets,
        api=raw.get("api", {}),
    )
