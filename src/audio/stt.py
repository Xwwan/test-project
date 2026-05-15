"""Speech-to-text interfaces and provider builders."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from src.audio.volcengine_asr import (
    AsrErrorFrame,
    AsrResultFrame,
    build_audio_request_frame,
    build_full_client_request_frame,
    parse_server_frame,
    split_pcm_audio,
)
from src.utils.config import load_app_config
from src.utils.env import get_config_value


class SpeechToTextClient(Protocol):
    def transcribe(self, audio: bytes, *, audio_format: str = "pcm") -> str:
        """Return recognized text for the provided audio bytes."""


@dataclass
class VolcengineAsrClient:
    app_id: str
    access_key: str
    resource_id: str
    url: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
    sample_rate: int = 16000
    timeout_seconds: float = 60.0
    uid: str = "voice-chat-service"

    def transcribe(self, audio: bytes, *, audio_format: str = "pcm") -> str:
        """Transcribe audio via Volcengine Bigmodel ASR."""

        if audio_format != "pcm":
            raise ValueError("Volcengine ASR currently supports audio_format='pcm'")
        websocket = _load_websocket_client()
        socket = websocket.create_connection(
            self.url,
            timeout=self.timeout_seconds,
            header=[
                f"X-Api-App-Key: {self.app_id}",
                f"X-Api-Access-Key: {self.access_key}",
                f"X-Api-Resource-Id: {self.resource_id}",
                f"X-Api-Connect-Id: {uuid4()}",
            ],
        )
        try:
            sequence = 1
            socket.send_binary(
                build_full_client_request_frame(
                    self._request_payload(),
                    sequence=sequence,
                )
            )
            sequence += 1
            for chunk in split_pcm_audio(audio, sample_rate=self.sample_rate):
                socket.send_binary(build_audio_request_frame(chunk, sequence=sequence))
                sequence += 1
            socket.send_binary(
                build_audio_request_frame(b"", is_final=True, sequence=sequence)
            )
            return self._read_until_final(socket)
        finally:
            socket.close()

    def _request_payload(self) -> dict[str, Any]:
        return {
            "user": {"uid": self.uid},
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": self.sample_rate,
                "bits": 16,
                "channel": 1,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "show_utterances": True,
                "result_type": "full",
            },
        }

    def _read_until_final(self, socket: Any) -> str:
        last_text = ""
        while True:
            raw = socket.recv()
            frame = parse_server_frame(raw)
            if isinstance(frame, AsrErrorFrame):
                raise RuntimeError(f"Volcengine ASR error {frame.code}: {frame.payload}")
            if isinstance(frame, AsrResultFrame):
                if frame.text:
                    last_text = frame.text
                if frame.is_final:
                    return last_text


def build_default_stt_client(
    *,
    config_path: str | Path | None = None,
) -> SpeechToTextClient:
    app_config = load_app_config(config_path)
    audio_config = _mapping(app_config.get("audio"), "audio")
    stt_config = _mapping(audio_config.get("stt"), "audio.stt")
    provider = _config_string(stt_config, "provider", "volcengine")
    if provider != "volcengine":
        raise ValueError("unsupported STT provider; use 'volcengine'")
    return VolcengineAsrClient(
        app_id=_secret_from_env(stt_config, "app_id_env", "VOLCENGINE_APP_ID"),
        access_key=_secret_from_env(
            stt_config,
            "access_key_env",
            "VOLCENGINE_ACCESS_KEY",
        ),
        resource_id=_secret_from_env(
            stt_config,
            "resource_id_env",
            "VOLCENGINE_RESOURCE_ID",
        ),
        url=_config_string(
            stt_config,
            "url",
            "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel",
        ),
        sample_rate=_config_int(stt_config, "sample_rate", 16000),
        timeout_seconds=_config_float(stt_config, "timeout_seconds", 60.0),
    )


def _load_websocket_client() -> Any:
    try:
        import websocket
    except ImportError as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError(
            "Volcengine ASR requires websocket-client; install requirements.txt"
        ) from exc
    return websocket


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
