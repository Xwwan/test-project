"""Text-to-speech interfaces and provider builders."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator, Protocol

from src.audio.dashscope_tts import DashScopeTtsClient
from src.utils.config import load_app_config
from src.utils.env import get_config_value


class TextToSpeechClient(Protocol):
    def synthesize(self, text: str) -> bytes:
        """Return synthesized audio bytes for the provided text."""

    def synthesize_stream(self, text: str) -> Iterator[bytes]:
        """Yield synthesized audio chunks for the provided text."""


def build_default_tts_client(
    *,
    config_path: str | Path | None = None,
) -> TextToSpeechClient:
    app_config = load_app_config(config_path)
    audio_config = _mapping(app_config.get("audio"), "audio")
    tts_config = _mapping(audio_config.get("tts"), "audio.tts")
    provider = _config_string(tts_config, "provider", "dashscope")
    if provider != "dashscope":
        raise ValueError("unsupported TTS provider; use 'dashscope'")
    return DashScopeTtsClient(
        api_key=_secret_from_env(tts_config, "api_key_env", "DASHSCOPE_API_KEY"),
        realtime_url=_config_string(
            tts_config,
            "realtime_url",
            "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        ),
        model=_config_string(tts_config, "model", "qwen3-tts-flash-realtime"),
        voice=_config_string(tts_config, "voice", "Cherry"),
        sample_rate=_config_int(tts_config, "sample_rate", 24000),
        timeout_seconds=_config_float(tts_config, "timeout_seconds", 60.0),
    )


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a YAML mapping")
    return value


def _config_string(config: dict[str, Any], key: str, default: str) -> str:
    value = config.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _config_int(config: dict[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _config_float(config: dict[str, Any], key: str, default: float) -> float:
    value = config.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a number")
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    raise ValueError(f"{key} must be a positive number")


def _secret_from_env(config: dict[str, Any], env_key: str, default_env_name: str) -> str:
    env_name = _config_string(config, env_key, default_env_name)
    value = get_config_value(env_name)
    if value:
        return value
    raise ValueError(f"missing audio secret; set {env_name}")
