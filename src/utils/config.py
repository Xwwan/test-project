"""YAML configuration loader for project settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "app.yaml"


def load_app_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the project YAML config as a dictionary."""

    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"config file not found: {config_path}")
    if not config_path.is_file():
        raise IsADirectoryError(f"expected config file, got directory: {config_path}")

    with config_path.open("r", encoding="utf-8") as file:
        loaded = yaml.safe_load(file) or {}

    if not isinstance(loaded, dict):
        raise ValueError("app config must be a YAML mapping")
    return loaded
