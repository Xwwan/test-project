"""Voice API request and response schemas."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from typing import Any

from src.api.schemas import SchemaError


def _require_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise SchemaError(f"{field_name} must be a non-empty string")
    return value


def _optional_bool(value: Any, field_name: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise SchemaError(f"{field_name} must be a boolean")
    return value


def _decode_base64_audio(value: Any) -> bytes:
    raw = _require_str(value, "audio_base64")
    try:
        decoded = base64.b64decode(raw.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise SchemaError("audio_base64 must be valid base64") from exc
    if not decoded:
        raise SchemaError("audio_base64 must decode to non-empty audio")
    return decoded


def _require_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"{field_name} must be an integer")
    return value


def _require_supported_live_audio(
    *,
    sample_rate: int,
    channels: int,
    audio_format: str,
) -> None:
    if sample_rate != 16000:
        raise SchemaError("sample_rate must be 16000")
    if channels != 1:
        raise SchemaError("channels must be 1")
    if audio_format != "pcm":
        raise SchemaError("audio_format must be 'pcm'")


@dataclass(frozen=True)
class VoiceChatRequest:
    conversation_id: str
    audio_bytes: bytes
    audio_format: str = "pcm"
    tts_enabled: bool = True

    @classmethod
    def from_dict(cls, data: Any) -> "VoiceChatRequest":
        if not isinstance(data, dict):
            raise SchemaError("voice chat request body must be a JSON object")
        audio_format = data.get("audio_format", "pcm")
        if not isinstance(audio_format, str) or not audio_format:
            raise SchemaError("audio_format must be a non-empty string")
        return cls(
            conversation_id=_require_str(data.get("conversation_id"), "conversation_id"),
            audio_bytes=_decode_base64_audio(data.get("audio_base64")),
            audio_format=audio_format,
            tts_enabled=_optional_bool(data.get("tts_enabled"), "tts_enabled", True),
        )


@dataclass(frozen=True)
class VoiceChatResponse:
    conversation_id: str
    request_id: str
    turn_id: str
    transcript: str
    reply: str
    retrieval_status: str
    retrieved_memory_ids: list[int] = field(default_factory=list)
    audio_bytes: bytes | None = None
    audio_format: str = "pcm"

    def to_dict(self) -> dict:
        audio_base64 = None
        if self.audio_bytes is not None:
            audio_base64 = base64.b64encode(self.audio_bytes).decode("ascii")
        return {
            "conversation_id": self.conversation_id,
            "request_id": self.request_id,
            "turn_id": self.turn_id,
            "transcript": self.transcript,
            "reply": self.reply,
            "retrieval_status": self.retrieval_status,
            "retrieved_memory_ids": list(self.retrieved_memory_ids),
            "audio_base64": audio_base64,
            "audio_format": self.audio_format,
        }


@dataclass(frozen=True)
class LiveVoiceStartRequest:
    sample_rate: int = 16000
    channels: int = 1
    audio_format: str = "pcm"

    @classmethod
    def from_dict(cls, data: Any) -> "LiveVoiceStartRequest":
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise SchemaError("live voice start request body must be a JSON object")
        sample_rate = _require_int(data.get("sample_rate", 16000), "sample_rate")
        channels = _require_int(data.get("channels", 1), "channels")
        audio_format = data.get("audio_format", "pcm")
        if not isinstance(audio_format, str) or not audio_format:
            raise SchemaError("audio_format must be a non-empty string")
        _require_supported_live_audio(
            sample_rate=sample_rate,
            channels=channels,
            audio_format=audio_format,
        )
        return cls(sample_rate=sample_rate, channels=channels, audio_format=audio_format)


@dataclass(frozen=True)
class LiveVoiceChunkRequest:
    session_id: str
    audio_bytes: bytes
    is_final: bool = False

    @classmethod
    def from_dict(cls, data: Any) -> "LiveVoiceChunkRequest":
        if not isinstance(data, dict):
            raise SchemaError("live voice chunk request body must be a JSON object")
        return cls(
            session_id=_require_str(data.get("session_id"), "session_id"),
            audio_bytes=_decode_base64_audio(data.get("audio_base64")),
            is_final=_optional_bool(data.get("is_final"), "is_final", False),
        )


@dataclass(frozen=True)
class LiveVoiceFinishRequest:
    session_id: str
    conversation_id: str
    tts_enabled: bool = True

    @classmethod
    def from_dict(cls, data: Any) -> "LiveVoiceFinishRequest":
        if not isinstance(data, dict):
            raise SchemaError("live voice finish request body must be a JSON object")
        return cls(
            session_id=_require_str(data.get("session_id"), "session_id"),
            conversation_id=_require_str(data.get("conversation_id"), "conversation_id"),
            tts_enabled=_optional_bool(data.get("tts_enabled"), "tts_enabled", True),
        )


@dataclass(frozen=True)
class LiveVoiceAbortRequest:
    session_id: str

    @classmethod
    def from_dict(cls, data: Any) -> "LiveVoiceAbortRequest":
        if not isinstance(data, dict):
            raise SchemaError("live voice abort request body must be a JSON object")
        return cls(session_id=_require_str(data.get("session_id"), "session_id"))


@dataclass(frozen=True)
class LiveVoiceStartResponse:
    session_id: str
    sample_rate: int
    channels: int
    audio_format: str
    chunk_duration_ms: int = 160
    chunk_bytes: int = 5120

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "audio_format": self.audio_format,
            "chunk_duration_ms": self.chunk_duration_ms,
            "chunk_bytes": self.chunk_bytes,
        }


@dataclass(frozen=True)
class LiveVoiceTranscriptResponse:
    session_id: str
    transcript: str
    is_final: bool
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "transcript": self.transcript,
            "is_final": self.is_final,
            "error": self.error,
        }
