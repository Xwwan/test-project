"""Runtime switches for temporary latency demo profiles."""

from __future__ import annotations

from pathlib import Path
import shutil
import threading
from typing import Any

import yaml

from src.interaction import store as interaction_store
from src.memory import db, repository as memory_repository
from src.onboarding import store as onboarding_store
from src.persona import file_manager
from src.utils.config import (
    reset_runtime_config_overrides,
    set_runtime_config_overrides,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEMO_ROOT = PROJECT_ROOT / "data" / "latency-results"
DEMO_RUNTIME_DIR_NAME = "_active"
DEMO_PROFILE_NAMES = ("jxl", "normal", "sts")

_lock = threading.RLock()
_active_profile: str | None = None
_active_tts_voice: str | None = None
_active_source_dir: Path | None = None


class DemoProfileError(ValueError):
    """Raised when a demo profile cannot be used."""


def switch_demo_profile(
    profile: str,
    *,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Switch process-wide demo data to a writable copy of one profile."""

    normalized = _normalize_profile(profile)
    source_dir = _profile_dir(normalized, root=root)
    source_database_path = source_dir / "app.db"
    _validate_profile_dir(source_dir, source_database_path)
    tts_voice = _load_profile_tts_voice(source_dir / "app.local.yaml")

    with _lock:
        runtime_dir = _prepare_runtime_profile_dir(source_dir, root=root)
        runtime_database_path = runtime_dir / "app.db"
        db.set_database_path(runtime_database_path)
        file_manager.set_data_dir(runtime_dir)
        _set_tts_voice_override(tts_voice)
        try:
            _init_runtime_tables()
        except Exception:
            db.reset_database_path()
            file_manager.reset_data_dir()
            reset_runtime_config_overrides()
            raise

        global _active_profile, _active_tts_voice, _active_source_dir
        _active_profile = normalized
        _active_tts_voice = tts_voice
        _active_source_dir = source_dir
        return get_demo_profile_state(root=root)


def get_demo_profile_state(*, root: str | Path | None = None) -> dict[str, Any]:
    """Return the active demo profile and all available prepared profiles."""

    with _lock:
        return {
            "profile": _active_profile,
            "active_profile": _active_profile,
            "voice": _active_tts_voice,
            "tts_voice": _active_tts_voice,
            "database_path": str(db.get_database_path()),
            "data_dir": str(file_manager.get_data_dir()),
            "source_data_dir": str(_active_source_dir) if _active_source_dir else None,
            "runtime_data_dir": str(_runtime_profile_dir(root=root)),
            "available_profiles": list_demo_profiles(root=root),
        }


def reset_demo_profile() -> None:
    """Clear the active demo override and return to normal configuration."""

    with _lock:
        db.reset_database_path()
        file_manager.reset_data_dir()
        reset_runtime_config_overrides()
        global _active_profile, _active_tts_voice, _active_source_dir
        _active_profile = None
        _active_tts_voice = None
        _active_source_dir = None


def list_demo_profiles(*, root: str | Path | None = None) -> list[dict[str, Any]]:
    """List the fixed demo profiles under data/latency-results."""

    return [_profile_info(name, root=root) for name in DEMO_PROFILE_NAMES]


def _init_runtime_tables() -> None:
    memory_repository.init_db()
    interaction_store.init_db()
    onboarding_store.init_db()


def _normalize_profile(profile: str) -> str:
    if not isinstance(profile, str):
        raise DemoProfileError("profile must be a string")
    normalized = profile.strip().lower()
    if normalized not in DEMO_PROFILE_NAMES:
        allowed = ", ".join(DEMO_PROFILE_NAMES)
        raise DemoProfileError(f"profile must be one of: {allowed}")
    return normalized


def _profile_dir(profile: str, *, root: str | Path | None = None) -> Path:
    base = Path(root) if root is not None else DEFAULT_DEMO_ROOT
    return base / profile


def _runtime_profile_dir(*, root: str | Path | None = None) -> Path:
    base = Path(root) if root is not None else DEFAULT_DEMO_ROOT
    return base / DEMO_RUNTIME_DIR_NAME


def _prepare_runtime_profile_dir(
    source_dir: Path,
    *,
    root: str | Path | None = None,
) -> Path:
    runtime_dir = _runtime_profile_dir(root=root)
    if runtime_dir.resolve() == source_dir.resolve():
        raise DemoProfileError("runtime demo directory cannot be the source profile directory")
    if runtime_dir.exists():
        if not runtime_dir.is_dir():
            raise DemoProfileError(f"runtime demo path is not a directory: {runtime_dir}")
        shutil.rmtree(runtime_dir)
    shutil.copytree(source_dir, runtime_dir)
    _ensure_runtime_dir_writable(runtime_dir)
    return runtime_dir


def _ensure_runtime_dir_writable(path: Path) -> None:
    for item in [path, *path.rglob("*")]:
        mode = item.stat().st_mode
        if item.is_dir():
            item.chmod(mode | 0o700)
        else:
            item.chmod(mode | 0o600)


def _validate_profile_dir(profile_dir: Path, database_path: Path) -> None:
    if not profile_dir.exists():
        raise DemoProfileError(f"profile directory not found: {profile_dir}")
    if not profile_dir.is_dir():
        raise DemoProfileError(f"profile path is not a directory: {profile_dir}")
    if not database_path.exists():
        raise DemoProfileError(f"profile app.db not found: {database_path}")
    if not database_path.is_file():
        raise DemoProfileError(f"profile app.db is not a file: {database_path}")
    for filename in ("Model.md", "User.md"):
        path = profile_dir / filename
        if not path.exists():
            raise DemoProfileError(f"profile {filename} not found: {path}")
        if not path.is_file():
            raise DemoProfileError(f"profile {filename} is not a file: {path}")


def _profile_info(profile: str, *, root: str | Path | None = None) -> dict[str, Any]:
    profile_dir = _profile_dir(profile, root=root)
    database_path = profile_dir / "app.db"
    model_path = profile_dir / "Model.md"
    user_path = profile_dir / "User.md"
    local_config_path = profile_dir / "app.local.yaml"
    return {
        "profile": profile,
        "data_dir": str(profile_dir),
        "database_path": str(database_path),
        "exists": profile_dir.is_dir() and database_path.is_file(),
        "has_model_profile": model_path.is_file(),
        "has_user_profile": user_path.is_file(),
        "has_local_config": local_config_path.is_file(),
        "tts_voice": _load_profile_tts_voice(local_config_path),
    }


def _set_tts_voice_override(tts_voice: str | None) -> None:
    if tts_voice is None:
        reset_runtime_config_overrides()
        return
    set_runtime_config_overrides({"audio": {"tts": {"voice": tts_voice}}})


def _load_profile_tts_voice(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise DemoProfileError(f"profile local config must be a mapping: {path}")
    audio = loaded.get("audio") or {}
    if not isinstance(audio, dict):
        raise DemoProfileError(f"profile audio config must be a mapping: {path}")
    tts = audio.get("tts") or {}
    if not isinstance(tts, dict):
        raise DemoProfileError(f"profile audio.tts config must be a mapping: {path}")
    voice = tts.get("voice")
    if voice is None:
        return None
    if not isinstance(voice, str) or not voice:
        raise DemoProfileError(f"profile audio.tts.voice must be a non-empty string: {path}")
    return voice
