"""API request and response schemas.

The MVP intentionally stays Pydantic-free so the only runtime dependency is
the Python standard library. Each schema is a small dataclass with a
``from_dict`` constructor that performs minimum validation. ``to_dict`` is
provided so handlers can ``json.dumps`` responses without extra plumbing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import base64
import binascii
from typing import Any


class SchemaError(ValueError):
    """Raised when an incoming payload does not match the declared schema."""


def _require_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise SchemaError(f"{field_name} must be a non-empty string")
    return value


def _optional_str(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SchemaError(f"{field_name} must be a string")
    return value


def _optional_bool(value: Any, field_name: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise SchemaError(f"{field_name} must be a boolean")
    return value


def _require_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"{field_name} must be an integer")
    return value


def _decode_base64(value: Any, field_name: str) -> bytes:
    raw = _require_str(value, field_name)
    try:
        decoded = base64.b64decode(raw.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise SchemaError(f"{field_name} must be valid base64") from exc
    if not decoded:
        raise SchemaError(f"{field_name} must decode to non-empty bytes")
    return decoded


def _workflow(value: Any) -> str:
    workflow = _require_str(value, "workflow")
    if workflow not in {"chat", "onboarding"}:
        raise SchemaError("workflow must be one of {'chat', 'onboarding'}")
    return workflow


@dataclass
class ChatRequest:
    conversation_id: str
    message: str
    tts_enabled: bool = False

    @classmethod
    def from_dict(cls, data: Any) -> "ChatRequest":
        if not isinstance(data, dict):
            raise SchemaError("chat request body must be a JSON object")
        return cls(
            conversation_id=_require_str(data.get("conversation_id"), "conversation_id"),
            message=_require_str(data.get("message"), "message"),
            tts_enabled=_optional_bool(data.get("tts_enabled"), "tts_enabled", False),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ChatResponse:
    request_id: str
    turn_id: str
    conversation_id: str
    reply: str
    retrieval_status: str
    retrieved_memory_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InteractionSessionCreateRequest:
    workflow: str
    conversation_id: str = ""
    tts_enabled: bool = False
    input_mode: str = "text"

    @classmethod
    def from_dict(cls, data: Any) -> "InteractionSessionCreateRequest":
        if not isinstance(data, dict):
            raise SchemaError("interaction session request body must be a JSON object")
        return cls(
            workflow=_workflow(data.get("workflow")),
            conversation_id=_optional_str(data.get("conversation_id"), "conversation_id"),
            tts_enabled=_optional_bool(data.get("tts_enabled"), "tts_enabled", False),
            input_mode=_optional_str(data.get("input_mode"), "input_mode") or "text",
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InteractionTextStreamRequest:
    interaction_session_id: str
    workflow: str
    message: str
    tts_enabled: bool = False

    @classmethod
    def from_dict(cls, data: Any) -> "InteractionTextStreamRequest":
        if not isinstance(data, dict):
            raise SchemaError("interaction text stream request body must be a JSON object")
        return cls(
            interaction_session_id=_require_str(
                data.get("interaction_session_id"),
                "interaction_session_id",
            ),
            workflow=_workflow(data.get("workflow")),
            message=_require_str(data.get("message"), "message"),
            tts_enabled=_optional_bool(data.get("tts_enabled"), "tts_enabled", False),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InteractionLiveStartRequest:
    interaction_session_id: str
    workflow: str
    sample_rate: int = 16000
    channels: int = 1
    audio_format: str = "pcm"

    @classmethod
    def from_dict(cls, data: Any) -> "InteractionLiveStartRequest":
        if not isinstance(data, dict):
            raise SchemaError("interaction live start request body must be a JSON object")
        audio_format = data.get("audio_format", "pcm")
        if not isinstance(audio_format, str) or not audio_format:
            raise SchemaError("audio_format must be a non-empty string")
        return cls(
            interaction_session_id=_require_str(
                data.get("interaction_session_id"),
                "interaction_session_id",
            ),
            workflow=_workflow(data.get("workflow")),
            sample_rate=_require_int(data.get("sample_rate", 16000), "sample_rate"),
            channels=_require_int(data.get("channels", 1), "channels"),
            audio_format=audio_format,
        )


@dataclass
class InteractionLiveChunkRequest:
    interaction_session_id: str
    workflow: str
    live_session_id: str
    audio_bytes: bytes
    is_final: bool = False

    @classmethod
    def from_dict(cls, data: Any) -> "InteractionLiveChunkRequest":
        if not isinstance(data, dict):
            raise SchemaError("interaction live chunk request body must be a JSON object")
        live_session_id = data.get("live_session_id") or data.get("session_id")
        return cls(
            interaction_session_id=_require_str(
                data.get("interaction_session_id"),
                "interaction_session_id",
            ),
            workflow=_workflow(data.get("workflow")),
            live_session_id=_require_str(live_session_id, "live_session_id"),
            audio_bytes=_decode_base64(data.get("audio_base64"), "audio_base64"),
            is_final=_optional_bool(data.get("is_final"), "is_final", False),
        )


@dataclass
class InteractionLiveSessionRequest:
    interaction_session_id: str
    workflow: str
    live_session_id: str

    @classmethod
    def from_dict(cls, data: Any) -> "InteractionLiveSessionRequest":
        if not isinstance(data, dict):
            raise SchemaError("interaction live request body must be a JSON object")
        live_session_id = data.get("live_session_id") or data.get("session_id")
        return cls(
            interaction_session_id=_require_str(
                data.get("interaction_session_id"),
                "interaction_session_id",
            ),
            workflow=_workflow(data.get("workflow")),
            live_session_id=_require_str(live_session_id, "live_session_id"),
        )


@dataclass
class InteractionLiveFinishStreamRequest:
    interaction_session_id: str
    workflow: str
    live_session_id: str
    tts_enabled: bool = True

    @classmethod
    def from_dict(cls, data: Any) -> "InteractionLiveFinishStreamRequest":
        if not isinstance(data, dict):
            raise SchemaError("interaction live finish request body must be a JSON object")
        live_session_id = data.get("live_session_id") or data.get("session_id")
        return cls(
            interaction_session_id=_require_str(
                data.get("interaction_session_id"),
                "interaction_session_id",
            ),
            workflow=_workflow(data.get("workflow")),
            live_session_id=_require_str(live_session_id, "live_session_id"),
            tts_enabled=_optional_bool(data.get("tts_enabled"), "tts_enabled", True),
        )


@dataclass
class FollowupDecisionResponse:
    request_id: str
    conversation_id: str
    decision: str
    followup_type: str
    reply: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MemoryCurateRequest:
    conversation_id: str
    history_limit: int = 50

    @classmethod
    def from_dict(cls, data: Any) -> "MemoryCurateRequest":
        if not isinstance(data, dict):
            raise SchemaError("memory curate request body must be a JSON object")
        history_limit = data.get("history_limit", 50)
        if not isinstance(history_limit, int) or history_limit <= 0:
            raise SchemaError("history_limit must be a positive integer")
        return cls(
            conversation_id=_require_str(data.get("conversation_id"), "conversation_id"),
            history_limit=history_limit,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MemoryCurateResponse:
    conversation_id: str
    operations: list[dict]
    applied: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProfileRefreshResponse:
    should_update: bool
    patch: dict | None
    reason: str | None
    new_profile: str | None

    def to_dict(self) -> dict:
        return asdict(self)
