"""YAML configuration loader for project settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "app.yaml"


def load_app_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the project YAML config as a dictionary.

    ``config/app.local.yaml`` is treated as an optional local-only override for
    ``config/app.yaml``. For explicit paths the same rule applies, so
    ``/tmp/test.yaml`` is overlaid by ``/tmp/test.local.yaml`` when present.
    """

    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    loaded = _load_yaml_mapping(config_path, label="config")
    local_path = _local_config_path(config_path)
    if local_path.exists():
        local = _load_yaml_mapping(local_path, label="local config")
        loaded = _deep_merge(loaded, local)
    return loaded


def _load_yaml_mapping(path: Path, *, label: str) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{label} file not found: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"expected {label} file, got directory: {path}")

    with path.open("r", encoding="utf-8") as file:
        loaded = yaml.safe_load(file) or {}

    if not isinstance(loaded, dict):
        raise ValueError(f"{label} must be a YAML mapping")
    return loaded


def _local_config_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.local{path.suffix}")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged
