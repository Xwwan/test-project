"""Service layer for Person 3 orchestration.

The dialogue service is the main entry point that the API layer calls; the
memory service is a small adapter over Person 1's Memory Store for use by the
Memory Curator path.
"""

from .dialogue_service import (
    DialogueDependencies,
    handle_chat_message,
    handle_chat_message_stream,
    handle_followup,
    iter_followup_events,
    reset_followup_delivery_bus,
)
from .memory_service import (
    apply_operations,
    curate_conversation_memory,
    refresh_user_profile,
)
from .interaction_service import (
    create_interaction_session,
    get_interaction_session_status,
    iter_text_interaction_events,
)

__all__ = [
    "DialogueDependencies",
    "apply_operations",
    "create_interaction_session",
    "curate_conversation_memory",
    "get_interaction_session_status",
    "handle_chat_message",
    "handle_chat_message_stream",
    "handle_followup",
    "iter_followup_events",
    "iter_text_interaction_events",
    "refresh_user_profile",
    "reset_followup_delivery_bus",
]
