"""HTTP API layer for the chat service."""

from typing import Any

from .schemas import (
    ChatRequest,
    ChatResponse,
    FollowupDecisionResponse,
    MemoryCurateRequest,
    MemoryCurateResponse,
)

__all__ = [
    "ChatRequest",
    "ChatResponse",
    "FollowupDecisionResponse",
    "MemoryCurateRequest",
    "MemoryCurateResponse",
    "build_app",
    "create_request_handler",
    "dispatch",
]


def __getattr__(name: str) -> Any:
    if name in {"build_app", "create_request_handler", "dispatch"}:
        from . import routes

        return getattr(routes, name)
    raise AttributeError(f"module 'src.api' has no attribute {name!r}")
