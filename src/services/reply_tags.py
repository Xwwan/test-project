"""Reply tag helpers for robot expressions and actions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.utils.config import load_app_config


_TAG_PATTERN = re.compile(
    r"\[\s*(?P<kind>emo|act)\s*[:：]\s*(?P<key>[^\]\r\n]+?)\s*\]",
    re.IGNORECASE,
)
_REPEATED_INLINE_SPACE_PATTERN = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCTUATION_PATTERN = re.compile(r"[ \t]+([,.;:!?，。！？、；：])")


@dataclass(frozen=True)
class ReplyTagConfig:
    emo: frozenset[str] = frozenset()
    act: frozenset[str] = frozenset()

    def allowed_keys(self, kind: str) -> frozenset[str]:
        if kind == "emo":
            return self.emo
        if kind == "act":
            return self.act
        return frozenset()


def load_reply_tag_config(
    config_path: str | Path | None = None,
) -> ReplyTagConfig:
    app_config = load_app_config(config_path)
    tag_config = _mapping(app_config.get("reply_tags"), "reply_tags")
    return ReplyTagConfig(
        emo=_string_set(tag_config.get("emo"), "reply_tags.emo"),
        act=_string_set(tag_config.get("act"), "reply_tags.act"),
    )


def prepare_tts_text(
    text: str,
    *,
    config: ReplyTagConfig | None = None,
    config_path: str | Path | None = None,
) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    tag_config = config or load_reply_tag_config(config_path)
    return strip_configured_reply_tags(text, tag_config)


def strip_configured_reply_tags(text: str, config: ReplyTagConfig) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    def replace_tag(match: re.Match[str]) -> str:
        return ""

    cleaned = _TAG_PATTERN.sub(replace_tag, text)
    cleaned = _REPEATED_INLINE_SPACE_PATTERN.sub(" ", cleaned)
    cleaned = _SPACE_BEFORE_PUNCTUATION_PATTERN.sub(r"\1", cleaned)
    return cleaned.strip()


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a YAML mapping")
    return value


def _string_set(value: Any, path: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        raise ValueError(f"{path} must be a YAML list")

    keys: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{path}[{index}] must be a non-empty string")
        keys.add(item.strip())
    return frozenset(keys)
