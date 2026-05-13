"""Dialogue Service: orchestrate Person 1, Person 2 and Request Coordinator.

The service implements the ``/chat`` flow described in
``docs/tasks/person-3-orchestration-curator.md`` section 4.4 and the follow-up
flow in section 4.5. All Person 1 / Person 2 collaborators are injected via
:class:`DialogueDependencies` so tests can swap real implementations for
deterministic fakes and so this branch can compile before Person 2's
``feature/dialogue-retrieval`` has been merged into ``main``.

Public entry points:

* :func:`handle_chat_message` — produce the first user-facing reply and bind
  retrieval results to a single ``request_id``.
* :func:`handle_followup` — decide whether retrieved memory justifies a second
  user-facing reply for a previously-handled request.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable

from src.coordinator import request_coordinator


# A retrieval status reported back to the caller of ``/chat``. The MVP always
# completes retrieval synchronously, but the field stays in the response so
# clients are ready for the asynchronous switch later.
RETRIEVAL_STATUS_COMPLETED = "completed"
RETRIEVAL_STATUS_PENDING = "pending"
RETRIEVAL_STATUS_FAILED = "failed"


@dataclass
class DialogueDependencies:
    """All collaborators the Dialogue Service needs.

    Every field is optional; missing entries are filled in via
    :meth:`resolved` by lazily importing the real Person 1 / Person 2
    implementations. Tests pass concrete fakes for every field they care
    about.
    """

    # Person 1 — persona
    read_model_profile: Callable[[], str] | None = None
    read_user_profile: Callable[[], str] | None = None
    apply_user_profile_patch: Callable[[dict], str] | None = None
    # Person 1 — conversation history
    append_turn: Callable[[str, dict], str] | None = None
    get_recent_history: Callable[..., list[dict]] | None = None
    get_compact_history: Callable[[str], str] | None = None
    update_compact_history: Callable[[str, str], None] | None = None
    # Person 1 — memory store
    list_lightweight_memory_items: Callable[..., list[dict]] | None = None
    get_memory_items_by_ids: Callable[[list[int]], list[dict]] | None = None
    apply_memory_operations: Callable[[list[dict]], list[dict]] | None = None
    # Person 2 — dialogue + retrieval agents
    generate_initial_reply: Callable[..., dict] | None = None
    generate_followup_reply: Callable[..., dict] | None = None
    retrieve_relevant_memory_ids: Callable[..., dict] | None = None
    # Person 3 — memory curator
    extract_memory_operations: Callable[..., dict] | None = None
    generate_user_profile_patch: Callable[..., dict] | None = None
    # Shared
    model_client: Any | None = None
    recent_history_limit: int = 20

    def with_overrides(self, **overrides: Any) -> "DialogueDependencies":
        return replace(self, **overrides)

    def resolved(self) -> "DialogueDependencies":
        """Return a copy with missing collaborators populated from real impls."""

        return _resolve(self)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def handle_chat_message(
    conversation_id: str,
    message: str,
    *,
    dependencies: DialogueDependencies | None = None,
) -> dict:
    """Run the full ``/chat`` orchestration for one user message.

    Returns a JSON-serializable dict shaped like::

        {
          "request_id": "req_xxx",
          "turn_id": "turn_xxx",
          "reply": "first user-facing reply",
          "retrieval_status": "completed" | "pending" | "failed",
          "retrieved_memory_ids": [int, ...],
          "conversation_id": "..."
        }
    """

    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("conversation_id must be a non-empty string")
    if not isinstance(message, str) or not message:
        raise ValueError("message must be a non-empty string")

    deps = (dependencies or DialogueDependencies()).resolved()
    request_record = request_coordinator.create_request(conversation_id, message)
    request_id = request_record["request_id"]
    turn_id = request_record["turn_id"]

    try:
        _save_user_turn(deps, conversation_id, turn_id, message)
        model_profile = deps.read_model_profile()
        user_profile = deps.read_user_profile()
        compact_history = deps.get_compact_history(conversation_id)
        recent_history = deps.get_recent_history(
            conversation_id, deps.recent_history_limit
        )

        initial = deps.generate_initial_reply(
            {
                "request_id": request_id,
                "model_profile": model_profile,
                "user_profile": user_profile,
                "compact_history": compact_history,
                "recent_history": recent_history,
                "current_query": message,
            },
            model_client=deps.model_client,
        )
        reply_text = _require_reply_text(initial)
        request_coordinator.mark_initial_reply(request_id, reply_text)
        _save_assistant_turn(deps, conversation_id, reply_text, kind="initial")

        request_coordinator.mark_retrieval_pending(request_id)
        retrieval_status, retrieved_items = _run_retrieval(
            deps=deps,
            request_id=request_id,
            user_message=message,
            compact_history=compact_history,
            recent_history=recent_history,
            user_profile=user_profile,
        )
        request_coordinator.mark_retrieval_completed(request_id, retrieved_items)
    except Exception as exc:
        request_coordinator.mark_failed(request_id, str(exc) or exc.__class__.__name__)
        raise

    return {
        "request_id": request_id,
        "turn_id": turn_id,
        "conversation_id": conversation_id,
        "reply": reply_text,
        "retrieval_status": retrieval_status,
        "retrieved_memory_ids": [item.get("id") for item in retrieved_items],
    }


def handle_followup(
    request_id: str,
    *,
    dependencies: DialogueDependencies | None = None,
) -> dict:
    """Decide whether retrieved memory justifies a second reply for ``request_id``.

    The request must be in status ``retrieval_completed`` (i.e. ``/chat`` has
    already finished). When the decision is ``followup`` the second reply is
    appended to the conversation history.
    """

    deps = (dependencies or DialogueDependencies()).resolved()
    record = request_coordinator.get_request(request_id)
    if record["status"] != "retrieval_completed":
        raise request_coordinator.RequestStateError(
            f"request {request_id} is in status {record['status']!r}, "
            "expected retrieval_completed before running follow-up"
        )

    conversation_id = record["conversation_id"]
    retrieved_items = record.get("retrieved_items") or []

    model_profile = deps.read_model_profile()
    user_profile = deps.read_user_profile()
    compact_history = deps.get_compact_history(conversation_id)
    recent_history = deps.get_recent_history(conversation_id, deps.recent_history_limit)

    decision = deps.generate_followup_reply(
        {
            "request_id": request_id,
            "model_profile": model_profile,
            "user_profile": user_profile,
            "compact_history": compact_history,
            "recent_history": recent_history,
            "original_user_query": record["user_message"],
            "initial_reply": record.get("initial_reply") or "",
            "retrieved_items": retrieved_items,
            "current_conversation_state": "retrieval_completed",
        },
        model_client=deps.model_client,
    )

    normalized = _normalize_followup_decision(decision, request_id)
    request_coordinator.mark_followup_decision(request_id, normalized)
    if normalized["decision"] == "followup":
        _save_assistant_turn(
            deps,
            conversation_id,
            normalized["reply"],
            kind="followup",
        )

    return {
        "request_id": request_id,
        "conversation_id": conversation_id,
        **normalized,
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _save_user_turn(
    deps: DialogueDependencies,
    conversation_id: str,
    turn_id: str,
    content: str,
) -> None:
    deps.append_turn(
        conversation_id,
        {
            "turn_id": turn_id,
            "role": "user",
            "content": content,
        },
    )


def _save_assistant_turn(
    deps: DialogueDependencies,
    conversation_id: str,
    content: str,
    *,
    kind: str,
) -> str:
    from uuid import uuid4

    assistant_turn_id = f"turn_{uuid4().hex}"
    deps.append_turn(
        conversation_id,
        {
            "turn_id": assistant_turn_id,
            "role": "assistant",
            "content": content,
            "metadata_json": {"turn_kind": kind},
        },
    )
    return assistant_turn_id


def _run_retrieval(
    *,
    deps: DialogueDependencies,
    request_id: str,
    user_message: str,
    compact_history: str,
    recent_history: list[dict],
    user_profile: str,
) -> tuple[str, list[dict]]:
    lightweight_items = deps.list_lightweight_memory_items()
    if not isinstance(lightweight_items, list):
        raise TypeError("list_lightweight_memory_items must return a list")
    if not lightweight_items:
        return RETRIEVAL_STATUS_COMPLETED, []

    retrieval = deps.retrieve_relevant_memory_ids(
        request_id=request_id,
        current_query=user_message,
        compact_history=compact_history,
        recent_history=recent_history,
        user_profile=user_profile,
        lightweight_memory_items=lightweight_items,
        model_client=deps.model_client,
    )
    selected_ids = retrieval.get("selected_memory_ids") if isinstance(retrieval, dict) else None
    if not selected_ids:
        return RETRIEVAL_STATUS_COMPLETED, []

    full_items = deps.get_memory_items_by_ids(selected_ids)
    if not isinstance(full_items, list):
        raise TypeError("get_memory_items_by_ids must return a list")
    return RETRIEVAL_STATUS_COMPLETED, list(full_items)


def _require_reply_text(initial: Any) -> str:
    if not isinstance(initial, dict):
        raise TypeError("generate_initial_reply must return a dictionary")
    reply = initial.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("generate_initial_reply must produce a non-empty reply")
    return reply


def _normalize_followup_decision(decision: Any, request_id: str) -> dict:
    if not isinstance(decision, dict):
        raise TypeError("generate_followup_reply must return a dictionary")

    decision_name = decision.get("decision")
    if decision_name not in {"followup", "no_followup"}:
        raise ValueError(
            "generate_followup_reply must return decision in {'followup', 'no_followup'}"
        )

    followup_type = decision.get("followup_type", "none")
    reply = decision.get("reply") or ""
    if not isinstance(reply, str):
        raise ValueError("followup reply must be a string")
    reply = reply.strip()

    if decision_name == "followup":
        if not reply:
            decision_name = "no_followup"
            followup_type = "none"
        elif followup_type not in {"supplement", "correction"}:
            followup_type = "supplement"

    if decision_name == "no_followup":
        return {
            "decision": "no_followup",
            "followup_type": "none",
            "reply": "",
        }
    return {
        "decision": "followup",
        "followup_type": followup_type,
        "reply": reply,
    }


# ---------------------------------------------------------------------------
# Dependency resolution
# ---------------------------------------------------------------------------


def _resolve(deps: DialogueDependencies) -> DialogueDependencies:
    """Fill in missing collaborators with lazy-imported real implementations."""

    overrides: dict[str, Any] = {}
    for field_name in _PERSON_1_RESOLVERS:
        if getattr(deps, field_name) is None:
            overrides[field_name] = _PERSON_1_RESOLVERS[field_name]()
    for field_name in _PERSON_2_RESOLVERS:
        if getattr(deps, field_name) is None:
            overrides[field_name] = _PERSON_2_RESOLVERS[field_name]()
    for field_name in _PERSON_3_RESOLVERS:
        if getattr(deps, field_name) is None:
            overrides[field_name] = _PERSON_3_RESOLVERS[field_name]()

    if not overrides:
        return deps
    return deps.with_overrides(**overrides)


def _resolve_persona() -> dict:
    from src.persona import file_manager

    return {
        "read_model_profile": file_manager.read_model_profile,
        "read_user_profile": file_manager.read_user_profile,
        "apply_user_profile_patch": file_manager.apply_user_profile_patch,
    }


def _resolve_history() -> dict:
    from src.conversation import compact_store, history_store

    return {
        "append_turn": history_store.append_turn,
        "get_recent_history": history_store.get_recent_history,
        "get_compact_history": compact_store.get_compact_history,
        "update_compact_history": compact_store.update_compact_history,
    }


def _resolve_memory_store() -> dict:
    from src.memory import repository

    return {
        "list_lightweight_memory_items": repository.list_lightweight_memory_items,
        "get_memory_items_by_ids": repository.get_memory_items_by_ids,
        "apply_memory_operations": repository.apply_memory_operations,
    }


_PERSON_1_RESOLVERS: dict[str, Callable[[], Any]] = {
    "read_model_profile": lambda: _resolve_persona()["read_model_profile"],
    "read_user_profile": lambda: _resolve_persona()["read_user_profile"],
    "apply_user_profile_patch": lambda: _resolve_persona()["apply_user_profile_patch"],
    "append_turn": lambda: _resolve_history()["append_turn"],
    "get_recent_history": lambda: _resolve_history()["get_recent_history"],
    "get_compact_history": lambda: _resolve_history()["get_compact_history"],
    "update_compact_history": lambda: _resolve_history()["update_compact_history"],
    "list_lightweight_memory_items": lambda: _resolve_memory_store()[
        "list_lightweight_memory_items"
    ],
    "get_memory_items_by_ids": lambda: _resolve_memory_store()["get_memory_items_by_ids"],
    "apply_memory_operations": lambda: _resolve_memory_store()["apply_memory_operations"],
}


def _resolve_dialogue_agent() -> dict:
    from src.agents.dialogue_agent import (  # type: ignore
        generate_followup_reply,
        generate_initial_reply,
    )
    from src.agents.memory_retrieval_workflow import (  # type: ignore
        retrieve_relevant_memory_ids,
    )

    return {
        "generate_initial_reply": generate_initial_reply,
        "generate_followup_reply": generate_followup_reply,
        "retrieve_relevant_memory_ids": retrieve_relevant_memory_ids,
    }


_PERSON_2_RESOLVERS: dict[str, Callable[[], Any]] = {
    "generate_initial_reply": lambda: _resolve_dialogue_agent()["generate_initial_reply"],
    "generate_followup_reply": lambda: _resolve_dialogue_agent()["generate_followup_reply"],
    "retrieve_relevant_memory_ids": lambda: _resolve_dialogue_agent()[
        "retrieve_relevant_memory_ids"
    ],
}


def _resolve_curator() -> dict:
    from src.agents.memory_curator import extract_memory_operations
    from src.agents.profile_consolidator import generate_user_profile_patch

    return {
        "extract_memory_operations": extract_memory_operations,
        "generate_user_profile_patch": generate_user_profile_patch,
    }


_PERSON_3_RESOLVERS: dict[str, Callable[[], Any]] = {
    "extract_memory_operations": lambda: _resolve_curator()["extract_memory_operations"],
    "generate_user_profile_patch": lambda: _resolve_curator()["generate_user_profile_patch"],
}
