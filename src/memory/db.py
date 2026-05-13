"""SQLite connection helpers for local application state."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

from src.utils.env import get_config_value, resolve_project_path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "app.db"
DATABASE_PATH_ENV = "APP_DB_PATH"

_database_path_override: Path | None = None


def set_database_path(path: str | Path | None) -> None:
    """Override the SQLite database path, mainly for tests."""

    global _database_path_override
    _database_path_override = Path(path) if path is not None else None


def reset_database_path() -> None:
    """Clear any explicit database path override."""

    set_database_path(None)


def get_database_path() -> Path:
    """Return the configured SQLite database path."""

    if _database_path_override is not None:
        return _database_path_override
    configured_path = get_config_value(DATABASE_PATH_ENV)
    if configured_path:
        return resolve_project_path(configured_path)
    return DEFAULT_DATABASE_PATH


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with row access enabled."""

    database_path = Path(path) if path is not None else get_database_path()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    """Initialize the configured SQLite database."""

    from .migrations import init_db as run_init_db

    run_init_db()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Run database work inside a commit/rollback transaction."""

    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
