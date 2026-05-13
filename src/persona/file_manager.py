"""Read and update local persona profile files."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
from typing import Any

from src.utils.env import get_config_value, resolve_project_path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR_ENV = "APP_DATA_DIR"

MODEL_PROFILE_FILENAME = "Model.md"
USER_PROFILE_FILENAME = "User.md"

_data_dir_override: Path | None = None
_write_lock = threading.Lock()


def set_data_dir(path: str | Path | None) -> None:
    """Override the data directory, mainly for tests."""

    global _data_dir_override
    _data_dir_override = Path(path) if path is not None else None


def reset_data_dir() -> None:
    """Clear any explicit data directory override."""

    set_data_dir(None)


def read_model_profile() -> str:
    """Read data/Model.md, creating it if the file is missing."""

    return _read_profile(MODEL_PROFILE_FILENAME)


def read_user_profile() -> str:
    """Read data/User.md, creating it if the file is missing."""

    return _read_profile(USER_PROFILE_FILENAME)


def write_user_profile(content: str) -> None:
    """Atomically replace data/User.md content."""

    if not isinstance(content, str):
        raise ValueError("content must be a string")
    _atomic_write(_profile_path(USER_PROFILE_FILENAME), content)


def apply_user_profile_patch(patch: dict) -> str:
    """Apply a small structured patch to User.md and return the new content."""

    if not isinstance(patch, dict):
        raise ValueError("patch must be a dictionary")

    current = read_user_profile()
    new_content = _apply_patch_to_text(current, patch)
    write_user_profile(new_content)
    return new_content


def get_data_dir() -> Path:
    """Return the configured data directory."""

    if _data_dir_override is not None:
        return _data_dir_override
    configured_path = get_config_value(DATA_DIR_ENV)
    if configured_path:
        return resolve_project_path(configured_path)
    return DEFAULT_DATA_DIR


def _profile_path(filename: str) -> Path:
    return get_data_dir() / filename


def _read_profile(filename: str) -> str:
    path = _profile_path(filename)
    _ensure_file(path)
    return path.read_text(encoding="utf-8")


def _ensure_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _atomic_write(path, "")
    if not path.is_file():
        raise IsADirectoryError(f"expected a profile file, got directory: {path}")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
        try:
            os.replace(temp_path, path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise


def _apply_patch_to_text(current: str, patch: dict) -> str:
    if "content" in patch:
        content = patch["content"]
        if not isinstance(content, str):
            raise ValueError("patch.content must be a string")
        return content

    content = current
    if "operations" in patch:
        operations = patch["operations"]
        if not isinstance(operations, list):
            raise ValueError("patch.operations must be a list")
        for operation in operations:
            content = _apply_single_operation(content, operation)
        return content

    if "prepend" in patch:
        content = _prepend_text(content, patch["prepend"])
    if "append" in patch:
        content = _append_text(content, patch["append"])
    if "replace" in patch:
        content = _replace_text(content, patch["replace"])

    if not any(key in patch for key in {"prepend", "append", "replace"}):
        raise ValueError("patch must contain content, operations, append, prepend, or replace")
    return content


def _apply_single_operation(content: str, operation: dict) -> str:
    if not isinstance(operation, dict):
        raise ValueError("each patch operation must be a dictionary")

    operation_name = operation.get("operation")
    if operation_name == "append":
        return _append_text(content, operation.get("text", ""))
    if operation_name == "prepend":
        return _prepend_text(content, operation.get("text", ""))
    if operation_name == "replace":
        return _replace_text(content, operation)
    if operation_name == "delete":
        text = operation.get("text")
        if not isinstance(text, str):
            raise ValueError("delete operation requires text")
        return content.replace(text, "")

    raise ValueError(f"unsupported profile patch operation: {operation_name!r}")


def _append_text(content: str, text: Any) -> str:
    if not isinstance(text, str):
        raise ValueError("append text must be a string")
    if text == "":
        return content
    separator = "" if not content or content.endswith("\n") else "\n"
    return f"{content}{separator}{text}"


def _prepend_text(content: str, text: Any) -> str:
    if not isinstance(text, str):
        raise ValueError("prepend text must be a string")
    if text == "":
        return content
    separator = "" if text.endswith("\n") or not content else "\n"
    return f"{text}{separator}{content}"


def _replace_text(content: str, replace: Any) -> str:
    if not isinstance(replace, dict):
        raise ValueError("replace must be a dictionary with old and new")
    old = replace.get("old")
    new = replace.get("new")
    if not isinstance(old, str) or not isinstance(new, str):
        raise ValueError("replace.old and replace.new must be strings")
    if old not in content:
        raise ValueError("replace.old was not found in User.md")
    return content.replace(old, new)
