"""API request and response schemas.

The MVP intentionally stays Pydantic-free so the only runtime dependency is
the Python standard library. Each schema is a small dataclass with a
``from_dict`` constructor that performs minimum validation. ``to_dict`` is
provided so handlers can ``json.dumps`` responses without extra plumbing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
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


@dataclass
class ChatRequest:
    conversation_id: str
    message: str

    @classmethod
    def from_dict(cls, data: Any) -> "ChatRequest":
        if not isinstance(data, dict):
            raise SchemaError("chat request body must be a JSON object")
        return cls(
            conversation_id=_require_str(data.get("conversation_id"), "conversation_id"),
            message=_require_str(data.get("message"), "message"),
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
