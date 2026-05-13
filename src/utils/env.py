"""Tiny .env reader for local standard-library configuration."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOTENV_PATH = PROJECT_ROOT / ".env"


def get_config_value(name: str) -> str | None:
    """Return an environment value, falling back to project-root .env."""

    value = os.getenv(name)
    if value is not None:
        return value
    return _read_dotenv().get(name)


def resolve_project_path(value: str | Path) -> Path:
    """Resolve relative config paths from the project root."""

    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _read_dotenv(path: Path = DEFAULT_DOTENV_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    if not path.is_file():
        raise IsADirectoryError(f"expected .env file, got directory: {path}")

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values
