"""Service layer for Person 3 orchestration.

The dialogue service is the main entry point that the API layer calls; the
memory service is a small adapter over Person 1's Memory Store for use by the
Memory Curator path.
"""

from .dialogue_service import (
    DialogueDependencies,
    handle_chat_message,
    handle_followup,
)
from .memory_service import (
    apply_operations,
    curate_conversation_memory,
    refresh_user_profile,
)

__all__ = [
    "DialogueDependencies",
    "apply_operations",
    "curate_conversation_memory",
    "handle_chat_message",
    "handle_followup",
    "refresh_user_profile",
]
