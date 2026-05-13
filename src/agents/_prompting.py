"""Prompt and JSON helpers shared by Person 2 agents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = PROJECT_ROOT / "prompts"


def read_prompt(filename: str, fallback: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.exists():
        return fallback
    content = path.read_text(encoding="utf-8").strip()
    return content or fallback


def dumps_pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


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


def _strip_code_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
