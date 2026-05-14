"""Memory Service: thin adapter between Person 3 logic and Person 1's store.

The service is intentionally small: it converts curator output / profile
consolidator output into Memory Store / Persona File Manager calls without
adding extra business logic. Anything more complex belongs in the curator or
the dialogue service.
"""

from __future__ import annotations

from typing import Any

from .dialogue_service import DialogueDependencies


def apply_operations(
    operations: list[dict],
    *,
    dependencies: DialogueDependencies | None = None,
) -> list[dict]:
    """Forward Memory Curator operations to Person 1's Memory Store."""

    if not isinstance(operations, list):
        raise ValueError("operations must be a list")

    deps = (dependencies or DialogueDependencies()).resolved()
    return deps.apply_memory_operations(operations)


def curate_conversation_memory(
    conversation_id: str,
    *,
    history_limit: int = 50,
    dependencies: DialogueDependencies | None = None,
    model_client: Any | None = None,
) -> dict:
    """Run Memory Curator over a conversation and persist the operations.

    Returns a JSON-serializable dict::

        {
          "conversation_id": "...",
          "operations": [...],
          "applied": [...]
        }
    """

    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("conversation_id must be a non-empty string")

    deps = (dependencies or DialogueDependencies()).resolved()
    turns = deps.get_recent_history(conversation_id, history_limit)
    if not isinstance(turns, list):
        raise TypeError("get_recent_history must return a list")
    existing_candidates = deps.list_lightweight_memory_items()
    if not isinstance(existing_candidates, list):
        raise TypeError("list_lightweight_memory_items must return a list")

    extraction = deps.extract_memory_operations(
        conversation_id=conversation_id,
        turns=turns,
        existing_memory_candidates=existing_candidates,
        model_client=model_client if model_client is not None else deps.model_client,
    )
    operations = extraction.get("operations", []) if isinstance(extraction, dict) else []
    applied = deps.apply_memory_operations(operations) if operations else []

    return {
        "conversation_id": conversation_id,
        "operations": operations,
        "applied": applied,
    }


def refresh_user_profile(
    *,
    dependencies: DialogueDependencies | None = None,
    min_importance: float = 0.6,
) -> dict:
    """Run Profile Consolidator across all active memory items and apply the patch."""

    deps = (dependencies or DialogueDependencies()).resolved()
    candidates = deps.list_lightweight_memory_items()
    if not isinstance(candidates, list):
        raise TypeError("list_lightweight_memory_items must return a list")

    memory_ids = [item["id"] for item in candidates if isinstance(item, dict) and "id" in item]
    full_items = deps.get_memory_items_by_ids(memory_ids) if memory_ids else []

    current_profile = deps.read_user_profile()
    decision = deps.generate_user_profile_patch(
        full_items,
        current_profile,
        min_importance=min_importance,
    )

    applied_content: str | None = None
    if decision.get("should_update") and decision.get("patch"):
        applied_content = deps.apply_user_profile_patch(decision["patch"])

    return {
        "should_update": bool(decision.get("should_update")),
        "patch": decision.get("patch"),
        "reason": decision.get("reason"),
        "new_profile": applied_content,
    }
