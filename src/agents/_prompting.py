"""Prompt and JSON helpers shared by Person 2 agents."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

from src.utils.config import load_app_config


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = PROJECT_ROOT / "prompts"
PROMPT_DEBUG_ENV = "CHAT_DEBUG_PROMPTS"
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def read_prompt(filename: str, fallback: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.exists():
        return fallback
    content = path.read_text(encoding="utf-8").strip()
    return content or fallback


def dumps_pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def dump_prompt_debug(
    label: str,
    *,
    request_id: str = "",
    recent_history: Any = None,
    messages: list[Any] | None = None,
) -> None:
    """Print the exact model prompt when prompt debugging is enabled."""

    if not _prompt_debug_enabled():
        return

    print("", file=sys.stderr)
    print(f"=== prompt debug: {label} ===", file=sys.stderr)
    if request_id:
        print(f"request_id: {request_id}", file=sys.stderr)
    if recent_history is not None:
        print("--- recent_history raw ---", file=sys.stderr)
        print(dumps_pretty(recent_history), file=sys.stderr)
        print("--- recent_history normalized ---", file=sys.stderr)
        print(dumps_pretty(normalize_history(recent_history)), file=sys.stderr)
    if messages is not None:
        print("--- messages sent to model ---", file=sys.stderr)
        for index, message in enumerate(messages):
            role = _message_value(message, "role")
            content = _message_value(message, "content")
            print(f"[{index}] role={role}", file=sys.stderr)
            print(content, file=sys.stderr)
    print(f"=== end prompt debug: {label} ===", file=sys.stderr)
    print("", file=sys.stderr)


def section(title: str, content: Any) -> str:
    if isinstance(content, str):
        body = content
    else:
        body = dumps_pretty(content)
    return f"## {title}\n{body if body else '(empty)'}"


def parse_json_object(text: str) -> dict:
    cleaned = _strip_code_fence(text.strip())
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model JSON output must be an object")
    return parsed


def normalize_history(recent_history: Any) -> list[dict]:
    if not isinstance(recent_history, list):
        return []

    normalized = []
    for turn in recent_history:
        if not isinstance(turn, dict):
            continue
        normalized.append(
            {
                key: turn.get(key, "")
                for key in ("role", "content", "created_at")
                if key in turn
            }
        )
    return normalized


def _prompt_debug_enabled() -> bool:
    env_value = os.getenv(PROMPT_DEBUG_ENV)
    if env_value is not None:
        normalized = env_value.strip().lower()
        if normalized in TRUE_VALUES:
            return True
        if normalized in FALSE_VALUES:
            return False

    try:
        config = load_app_config()
    except Exception:
        return False

    debug_config = config.get("debug")
    if not isinstance(debug_config, dict):
        return False
    return bool(debug_config.get("print_prompts"))


def _message_value(message: Any, key: str) -> str:
    if isinstance(message, dict):
        value = message.get(key, "")
    else:
        value = getattr(message, key, "")
    return value if isinstance(value, str) else str(value)


def _strip_code_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
