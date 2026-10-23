"""Config and path resolution."""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path
from typing import Any

import yaml


def repo_root() -> Path:
    env = os.environ.get("CHILLWATCH_HOME")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    return Path(os.environ.get("CHILLWATCH_CONFIG_DIR", repo_root() / "config"))


def data_dir() -> Path:
    return Path(os.environ.get("CHILLWATCH_DATA_DIR", repo_root() / "data"))


@cache
def _load(path: str) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_yaml(name_or_path: str | Path) -> dict[str, Any]:
    p = Path(name_or_path)
    if not p.exists():
        p = config_dir() / p
    return _load(str(p.resolve()))


def thresholds() -> dict[str, Any]:
    return load_yaml("thresholds.yaml")


def region(path: str | Path | None = None) -> dict[str, Any]:
    """Region config; ``CHILLWATCH_REGION`` selects another file (e.g. region_asos.yaml)."""
    return load_yaml(path or os.environ.get("CHILLWATCH_REGION", "region.yaml"))


def cimis_flags() -> dict[str, Any]:
    return load_yaml("cimis_flags.yaml")
