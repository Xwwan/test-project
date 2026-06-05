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

import logging
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterator

from src.coordinator import request_coordinator
from src.utils.config import load_app_config


logger = logging.getLogger("chat-service.dialogue")


# A retrieval status reported back to the caller of ``/chat``. The MVP always
# completes retrieval synchronously, but the field stays in the response so
# clients are ready for the asynchronous switch later.
RETRIEVAL_STATUS_COMPLETED = "completed"
RETRIEVAL_STATUS_PENDING = "pending"
RETRIEVAL_STATUS_FAILED = "failed"

DEFAULT_RETRIEVAL_MAX_WORKERS = 4
DEFAULT_FOLLOWUP_CONTEXT_WINDOW_TURNS = 50
FOLLOWUP_INITIAL_REPLY_WAIT_SECONDS = 30.0
FOLLOWUP_READY_DRAIN_SECONDS = 0.05


@dataclass(frozen=True)
class DialogueTurnSnapshot:
    request_id: str
    turn_id: str
    conversation_id: str
    user_message: str
    model_profile: str
    user_profile: str
    compact_history: str
    recent_history: list[dict]
    created_at: str
    initial_reply: str | None = None
    initial_reply_turn_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "turn_id": self.turn_id,
            "conversation_id": self.conversation_id,
            "user_message": self.user_message,
            "model_profile": self.model_profile,
            "user_profile": self.user_profile,
            "compact_history": self.compact_history,
            "recent_history": [dict(turn) for turn in self.recent_history],
            "created_at": self.created_at,
            "initial_reply": self.initial_reply,
            "initial_reply_turn_id": self.initial_reply_turn_id,
        }


class PendingInitialReply:
    """Thread-safe handoff from Agent A streaming to background follow-up."""

    def __init__(self, snapshot: DialogueTurnSnapshot) -> None:
        self._snapshot = snapshot
        self._event = threading.Event()
        self._retrieval_completed = threading.Event()
        self._followup_completed = threading.Event()
        self._lock = threading.Lock()

    def set_initial_reply(self, reply: str, assistant_turn_id: str) -> DialogueTurnSnapshot:
        with self._lock:
            self._snapshot = replace(
                self._snapshot,
                initial_reply=reply,
                initial_reply_turn_id=assistant_turn_id,
            )
            self._event.set()
            return self._snapshot

    def wait(self, timeout: float | None = None) -> DialogueTurnSnapshot | None:
        if not self._event.wait(timeout=timeout):
            return None
        with self._lock:
            return self._snapshot

    def mark_retrieval_completed(self) -> None:
        self._retrieval_completed.set()

    def mark_followup_completed(self) -> None:
        self._followup_completed.set()

    def retrieval_completed(self) -> bool:
        return self._retrieval_completed.is_set()

    def wait_followup_completed(self, timeout: float) -> bool:
        return self._followup_completed.wait(timeout=timeout)


class FollowupDeliveryBus:
    """In-process conversation-level queue for generated follow-up events."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._queues: dict[str, queue.Queue[dict]] = {}

    def publish(self, conversation_id: str, payload: dict) -> None:
        with self._lock:
            stream_queue = self._queues.setdefault(conversation_id, queue.Queue())
        stream_queue.put({"event": "followup", "data": dict(payload)})

    def reset(self) -> None:
        with self._lock:
            self._queues.clear()

    def listen(
        self,
        conversation_id: str,
        *,
        keepalive_seconds: float = 15.0,
        stop_after_idle: bool = False,
    ) -> Iterator[dict]:
        with self._lock:
            stream_queue = self._queues.setdefault(conversation_id, queue.Queue())

        while True:
            try:
                event = stream_queue.get(timeout=keepalive_seconds)
            except queue.Empty:
                if stop_after_idle:
                    return
                yield {"event": "ping", "data": {"conversation_id": conversation_id}}
                continue
            request_id = event.get("data", {}).get("request_id")
            if isinstance(request_id, str) and request_id:
                try:
                    request_coordinator.mark_followup_delivered(request_id)
                except request_coordinator.RequestNotFoundError:
                    logger.info(
                        "followup delivery skipped missing request request_id=%s",
                        request_id,
                    )
            yield event


_followup_delivery_bus = FollowupDeliveryBus()
_retrieval_executor_lock = threading.Lock()
_retrieval_executor: ThreadPoolExecutor | None = None


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
    generate_initial_reply_stream: Callable[..., Iterator[str]] | None = None
    generate_followup_reply: Callable[..., dict] | None = None
    retrieve_relevant_memory_ids: Callable[..., dict] | None = None
    # Person 3 — memory curator
    extract_memory_operations: Callable[..., dict] | None = None
    generate_user_profile_patch: Callable[..., dict] | None = None
    # Shared
    model_client: Any | None = None
    recent_history_limit: int = 20
    followup_context_window_turns: int | None = None
    retrieval_max_workers: int | None = None

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
    logger.info(
        "dialogue request received request_id=%s turn_id=%s conversation_id=%s mode=sync",
        request_id,
        turn_id,
        conversation_id,
    )

    try:
        _save_user_turn(deps, conversation_id, turn_id, message)
        model_profile = deps.read_model_profile()
        user_profile = deps.read_user_profile()
        compact_history = deps.get_compact_history(conversation_id)
        recent_history = deps.get_recent_history(
            conversation_id, deps.recent_history_limit
        )
        snapshot = DialogueTurnSnapshot(
            request_id=request_id,
            turn_id=turn_id,
            conversation_id=conversation_id,
            user_message=message,
            model_profile=model_profile,
            user_profile=user_profile,
            compact_history=compact_history,
            recent_history=recent_history,
            created_at=request_record["created_at"],
        )
        request_coordinator.attach_context_snapshot(request_id, snapshot.to_dict())

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
        initial_reply_turn_id = _save_assistant_turn(
            deps,
            conversation_id,
            reply_text,
            kind="initial",
        )
        snapshot = replace(
            snapshot,
            initial_reply=reply_text,
            initial_reply_turn_id=initial_reply_turn_id,
        )
        request_coordinator.attach_context_snapshot(request_id, snapshot.to_dict())
        request_coordinator.mark_initial_reply(
            request_id,
            reply_text,
            assistant_turn_id=initial_reply_turn_id,
        )
        logger.info(
            "initial reply generated request_id=%s conversation_id=%s reply_chars=%d",
            request_id,
            conversation_id,
            len(reply_text),
        )

        request_coordinator.mark_retrieval_pending(request_id)
        logger.info(
            "memory retrieval marked pending request_id=%s conversation_id=%s mode=sync",
            request_id,
            conversation_id,
        )
    except Exception as exc:
        request_coordinator.mark_failed(request_id, str(exc) or exc.__class__.__name__)
        logger.error(
            "dialogue request failed request_id=%s conversation_id=%s reason=%r",
            request_id,
            conversation_id,
            str(exc) or exc.__class__.__name__,
        )
        raise

    try:
        retrieval_status, retrieved_items = _run_retrieval(deps=deps, snapshot=snapshot)
        request_coordinator.mark_retrieval_completed(request_id, retrieved_items)
        logger.info(
            "memory retrieval completed request_id=%s conversation_id=%s status=%s "
            "retrieved_count=%d retrieved_ids=%s followup_auto_run=true",
            request_id,
            conversation_id,
            retrieval_status,
            len(retrieved_items),
            _memory_ids(retrieved_items),
        )
    except Exception as exc:
        retrieval_status = RETRIEVAL_STATUS_FAILED
        retrieved_items = []
        request_coordinator.mark_retrieval_failed(
            request_id,
            str(exc) or exc.__class__.__name__,
        )
        logger.error(
            "memory retrieval failed after initial reply request_id=%s "
            "conversation_id=%s reason=%r",
            request_id,
            conversation_id,
            str(exc) or exc.__class__.__name__,
        )
    else:
        try:
            _run_followup_decision(deps, request_id, snapshot=snapshot)
        except Exception as exc:
            request_coordinator.mark_followup_failed(
                request_id,
                str(exc) or exc.__class__.__name__,
            )
            logger.error(
                "followup decision failed after retrieval request_id=%s "
                "conversation_id=%s reason=%r",
                request_id,
                conversation_id,
                str(exc) or exc.__class__.__name__,
            )

    return {
        "request_id": request_id,
        "turn_id": turn_id,
        "conversation_id": conversation_id,
        "reply": reply_text,
        "retrieval_status": retrieval_status,
        "retrieved_memory_ids": [item.get("id") for item in retrieved_items],
    }


def handle_chat_message_stream(
    conversation_id: str,
    message: str,
    *,
    dependencies: DialogueDependencies | None = None,
    stream_followup: bool = False,
) -> Iterator[dict]:
    """Run ``/chat`` orchestration while yielding the initial reply as deltas.

    Events are JSON-serializable dictionaries with ``event`` and ``data`` keys.
    The final ``done`` event has the same payload shape as ``handle_chat_message``.
    """

    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("conversation_id must be a non-empty string")
    if not isinstance(message, str) or not message:
        raise ValueError("message must be a non-empty string")

    deps = (dependencies or DialogueDependencies()).resolved()
    request_record = request_coordinator.create_request(conversation_id, message)
    request_id = request_record["request_id"]
    turn_id = request_record["turn_id"]
    reply_parts: list[str] = []
    if stream_followup:
        logger.info(
            "stream_followup ignored request_id=%s conversation_id=%s "
            "reason=followups_use_conversation_stream",
            request_id,
            conversation_id,
        )
    logger.info(
        "dialogue request received request_id=%s turn_id=%s conversation_id=%s mode=stream",
        request_id,
        turn_id,
        conversation_id,
    )

    try:
        _save_user_turn(deps, conversation_id, turn_id, message)
        model_profile = deps.read_model_profile()
        user_profile = deps.read_user_profile()
        compact_history = deps.get_compact_history(conversation_id)
        recent_history = deps.get_recent_history(
            conversation_id, deps.recent_history_limit
        )
        snapshot = DialogueTurnSnapshot(
            request_id=request_id,
            turn_id=turn_id,
            conversation_id=conversation_id,
            user_message=message,
            model_profile=model_profile,
            user_profile=user_profile,
            compact_history=compact_history,
            recent_history=recent_history,
            created_at=request_record["created_at"],
        )
        request_coordinator.attach_context_snapshot(request_id, snapshot.to_dict())

        yield {
            "event": "meta",
            "data": {
                "request_id": request_id,
                "turn_id": turn_id,
                "conversation_id": conversation_id,
            },
        }

        request_coordinator.mark_retrieval_pending(request_id)
        logger.info(
            "memory retrieval marked pending request_id=%s conversation_id=%s "
            "mode=background parallel_with_initial=true",
            request_id,
            conversation_id,
        )
        pending_initial_reply = PendingInitialReply(snapshot)
        _run_retrieval_in_background(
            deps=deps,
            snapshot=snapshot,
            pending_initial_reply=pending_initial_reply,
        )

        stream = deps.generate_initial_reply_stream(
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
        for delta in stream:
            if not isinstance(delta, str):
                raise TypeError("generate_initial_reply_stream must yield strings")
            if not delta:
                continue
            reply_parts.append(delta)
            yield {"event": "delta", "data": {"delta": delta}}

        reply_text = "".join(reply_parts).strip()
        if not reply_text:
            raise ValueError("generate_initial_reply_stream must produce a non-empty reply")
        initial_reply_turn_id = _save_assistant_turn(
            deps,
            conversation_id,
            reply_text,
            kind="initial",
        )
        snapshot = replace(
            snapshot,
            initial_reply=reply_text,
            initial_reply_turn_id=initial_reply_turn_id,
        )
        pending_initial_reply.set_initial_reply(reply_text, initial_reply_turn_id)
        request_coordinator.attach_context_snapshot(request_id, snapshot.to_dict())
        request_coordinator.mark_initial_reply(
            request_id,
            reply_text,
            assistant_turn_id=initial_reply_turn_id,
        )
        logger.info(
            "initial reply generated request_id=%s conversation_id=%s reply_chars=%d mode=stream",
            request_id,
            conversation_id,
            len(reply_text),
        )
        pending_initial_reply.wait_followup_completed(
            timeout=FOLLOWUP_READY_DRAIN_SECONDS,
        )
    except Exception as exc:
        request_coordinator.mark_failed(request_id, str(exc) or exc.__class__.__name__)
        logger.error(
            "dialogue request failed request_id=%s conversation_id=%s mode=stream reason=%r",
            request_id,
            conversation_id,
            str(exc) or exc.__class__.__name__,
        )
        raise

    yield {
        "event": "done",
        "data": {
            "request_id": request_id,
            "turn_id": turn_id,
            "conversation_id": conversation_id,
            "reply": reply_text,
            "retrieval_status": RETRIEVAL_STATUS_PENDING,
            "retrieved_memory_ids": [],
        },
    }


def iter_followup_events(
    conversation_id: str,
    *,
    keepalive_seconds: float = 15.0,
    stop_after_idle: bool = False,
) -> Iterator[dict]:
    """Yield generated follow-up events for one conversation as SSE payloads."""

    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("conversation_id must be a non-empty string")
    yield from _followup_delivery_bus.listen(
        conversation_id,
        keepalive_seconds=keepalive_seconds,
        stop_after_idle=stop_after_idle,
    )


def reset_followup_delivery_bus() -> None:
    """Clear in-memory follow-up delivery queues. Intended for tests."""

    _followup_delivery_bus.reset()


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
    if record["status"] in {"followup_generated", "no_followup_needed"}:
        decision = record.get("followup_decision")
        if isinstance(decision, dict):
            logger.info(
                "followup decision already completed request_id=%s conversation_id=%s "
                "decision=%s followup_type=%s",
                request_id,
                record["conversation_id"],
                decision.get("decision"),
                decision.get("followup_type"),
            )
            return {
                "request_id": request_id,
                "conversation_id": record["conversation_id"],
                **decision,
            }

    if record["status"] != "retrieval_completed":
        raise request_coordinator.RequestStateError(
            f"request {request_id} is in status {record['status']!r}, "
            "expected retrieval_completed before running follow-up"
        )

    return _run_followup_decision(deps, request_id)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _run_followup_decision(
    deps: DialogueDependencies,
    request_id: str,
    *,
    snapshot: DialogueTurnSnapshot | None = None,
) -> dict:
    record = request_coordinator.get_request(request_id)
    if record["status"] in {"followup_generated", "no_followup_needed"}:
        decision = record.get("followup_decision")
        if not isinstance(decision, dict):
            raise request_coordinator.RequestStateError(
                f"request {request_id} is already finalized without a follow-up decision"
            )
        return {
            "request_id": request_id,
            "conversation_id": record["conversation_id"],
            **decision,
        }
    if record["status"] != "retrieval_completed":
        raise request_coordinator.RequestStateError(
            f"request {request_id} is in status {record['status']!r}, "
            "expected retrieval_completed before running follow-up"
        )

    conversation_id = record["conversation_id"]
    retrieved_items = record.get("retrieved_items") or []
    snapshot = snapshot or _snapshot_from_record(record)
    logger.info(
        "followup decision started request_id=%s conversation_id=%s retrieved_count=%d "
        "retrieved_ids=%s",
        request_id,
        conversation_id,
        len(retrieved_items),
        _memory_ids(retrieved_items),
    )

    context_window = _followup_context_window_turns(deps)
    latest_recent_history = _read_history_window(
        deps,
        conversation_id,
        context_window,
    )
    newer_turns = _newer_turns_since_original_request(
        latest_recent_history,
        snapshot.turn_id,
        context_window,
    )

    decision = deps.generate_followup_reply(
        {
            "request_id": request_id,
            "conversation_id": conversation_id,
            "parent_user_turn_id": snapshot.turn_id,
            "parent_initial_reply_turn_id": snapshot.initial_reply_turn_id or "",
            "original_user_query": snapshot.user_message,
            "initial_reply": snapshot.initial_reply or "",
            "original_context": {
                "model_profile": snapshot.model_profile,
                "user_profile": snapshot.user_profile,
                "compact_history": snapshot.compact_history,
                "recent_history": snapshot.recent_history,
            },
            "model_profile": snapshot.model_profile,
            "user_profile": snapshot.user_profile,
            "compact_history": snapshot.compact_history,
            "recent_history": snapshot.recent_history,
            "retrieved_items": retrieved_items,
            "latest_context": {
                "recent_history": latest_recent_history,
                "newer_turns_since_original_request": newer_turns,
            },
            "latest_recent_history": latest_recent_history,
            "newer_turns_since_original_request": newer_turns,
            "current_conversation_state": "retrieval_completed",
        },
        model_client=deps.model_client,
    )

    normalized = _normalize_followup_decision(decision, request_id)
    if normalized["decision"] == "followup":
        followup_turn_id = _save_assistant_turn(
            deps,
            conversation_id,
            normalized["reply"],
            kind="followup",
            metadata={
                "parent_request_id": request_id,
                "parent_user_turn_id": snapshot.turn_id,
                "parent_initial_reply_turn_id": snapshot.initial_reply_turn_id,
                "followup_type": normalized["followup_type"],
            },
        )
        normalized["followup_turn_id"] = followup_turn_id
        logger.info(
            "followup reply saved request_id=%s conversation_id=%s turn_kind=followup",
            request_id,
            conversation_id,
        )

    request_coordinator.mark_followup_decision(request_id, normalized)
    logger.info(
        "followup decision completed request_id=%s conversation_id=%s "
        "second_reply_enabled=%s decision=%s followup_type=%s reply_chars=%d",
        request_id,
        conversation_id,
        normalized["decision"] == "followup",
        normalized["decision"],
        normalized["followup_type"],
        len(normalized.get("reply") or ""),
    )

    result = {
        "request_id": request_id,
        "conversation_id": conversation_id,
        "parent_user_turn_id": snapshot.turn_id,
        "parent_initial_reply_turn_id": snapshot.initial_reply_turn_id or "",
        "original_user_query": snapshot.user_message,
        "initial_reply": snapshot.initial_reply or "",
        **normalized,
    }
    if normalized["decision"] == "followup":
        _publish_followup_event(result)
    return result


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
    metadata: dict | None = None,
) -> str:
    from uuid import uuid4

    assistant_turn_id = f"turn_{uuid4().hex}"
    metadata_json = {"turn_kind": kind}
    if metadata:
        metadata_json.update(metadata)
    deps.append_turn(
        conversation_id,
        {
            "turn_id": assistant_turn_id,
            "role": "assistant",
            "content": content,
            "metadata_json": metadata_json,
        },
    )
    return assistant_turn_id


def _run_retrieval(
    *,
    deps: DialogueDependencies,
    snapshot: DialogueTurnSnapshot,
) -> tuple[str, list[dict]]:
    request_id = snapshot.request_id
    lightweight_items = deps.list_lightweight_memory_items()
    if not isinstance(lightweight_items, list):
        raise TypeError("list_lightweight_memory_items must return a list")
    logger.info(
        "memory retrieval lightweight scan request_id=%s lightweight_count=%d",
        request_id,
        len(lightweight_items),
    )
    if not lightweight_items:
        logger.info(
            "memory retrieval skipped request_id=%s reason=no_lightweight_memory_items",
            request_id,
        )
        return RETRIEVAL_STATUS_COMPLETED, []

    logger.info(
        "memory retrieval agent started request_id=%s lightweight_ids=%s",
        request_id,
        _memory_ids(lightweight_items),
    )
    retrieval = deps.retrieve_relevant_memory_ids(
        request_id=request_id,
        current_query=snapshot.user_message,
        compact_history=snapshot.compact_history,
        recent_history=snapshot.recent_history,
        user_profile=snapshot.user_profile,
        lightweight_memory_items=lightweight_items,
        model_client=deps.model_client,
    )
    selected_ids = retrieval.get("selected_memory_ids") if isinstance(retrieval, dict) else None
    retrieval_reason = retrieval.get("retrieval_reason") if isinstance(retrieval, dict) else None
    logger.info(
        "memory retrieval agent completed request_id=%s selected_ids=%s needs_full_load=%s "
        "reason=%r",
        request_id,
        list(selected_ids or []),
        bool(selected_ids),
        retrieval_reason,
    )
    if not selected_ids:
        logger.info(
            "memory retrieval completed request_id=%s retrieved_count=0 trigger=no_match",
            request_id,
        )
        return RETRIEVAL_STATUS_COMPLETED, []

    full_items = deps.get_memory_items_by_ids(selected_ids)
    if not isinstance(full_items, list):
        raise TypeError("get_memory_items_by_ids must return a list")
    logger.info(
        "memory full load completed request_id=%s selected_ids=%s retrieved_count=%d "
        "retrieved_ids=%s",
        request_id,
        list(selected_ids),
        len(full_items),
        _memory_ids(full_items),
    )
    return RETRIEVAL_STATUS_COMPLETED, list(full_items)


def _run_retrieval_in_background(
    *,
    deps: DialogueDependencies,
    snapshot: DialogueTurnSnapshot,
    pending_initial_reply: PendingInitialReply | None = None,
) -> None:
    def _worker() -> None:
        request_id = snapshot.request_id
        logger.info("memory retrieval background worker started request_id=%s", request_id)
        retrieved_items: list[dict] = []
        try:
            _, retrieved_items = _run_retrieval(deps=deps, snapshot=snapshot)
            request_coordinator.mark_retrieval_completed(request_id, retrieved_items)
            if pending_initial_reply is not None:
                pending_initial_reply.mark_retrieval_completed()
            logger.info(
                "memory retrieval background worker completed request_id=%s "
                "retrieved_count=%d retrieved_ids=%s followup_auto_run=true",
                request_id,
                len(retrieved_items),
                _memory_ids(retrieved_items),
            )
        except Exception as exc:  # pragma: no cover - defensive background guard
            try:
                request_coordinator.mark_retrieval_failed(
                    request_id,
                    str(exc) or exc.__class__.__name__,
                )
            except request_coordinator.RequestNotFoundError:
                logger.info(
                    "memory retrieval background worker could not mark failed because "
                    "request disappeared request_id=%s",
                    request_id,
                )
                return
            logger.error(
                "memory retrieval background worker failed request_id=%s reason=%r",
                request_id,
                str(exc) or exc.__class__.__name__,
            )
            return

        followup_snapshot = snapshot
        if pending_initial_reply is not None:
            ready_snapshot = pending_initial_reply.wait(
                timeout=FOLLOWUP_INITIAL_REPLY_WAIT_SECONDS,
            )
            if ready_snapshot is None:
                logger.info(
                    "followup background worker waiting for initial reply timed out "
                    "request_id=%s wait_seconds=%.1f",
                    request_id,
                    FOLLOWUP_INITIAL_REPLY_WAIT_SECONDS,
                )
                return
            followup_snapshot = ready_snapshot

        try:
            _run_followup_decision(deps, request_id, snapshot=followup_snapshot)
            if pending_initial_reply is not None:
                pending_initial_reply.mark_followup_completed()
        except Exception as exc:  # pragma: no cover - defensive background guard
            try:
                request_coordinator.mark_followup_failed(
                    request_id,
                    str(exc) or exc.__class__.__name__,
                )
            except request_coordinator.RequestNotFoundError:
                logger.info(
                    "followup background worker could not mark failed because "
                    "request disappeared request_id=%s",
                    request_id,
                )
                return
            logger.error(
                "followup background worker failed request_id=%s reason=%r",
                request_id,
                str(exc) or exc.__class__.__name__,
            )
        finally:
            if pending_initial_reply is not None:
                pending_initial_reply.mark_followup_completed()

    request_id = snapshot.request_id
    executor = _get_retrieval_executor(deps)
    executor.submit(_worker)
    logger.info(
        "memory retrieval background worker scheduled request_id=%s executor=%s",
        request_id,
        executor,
    )


def _snapshot_from_record(record: dict) -> DialogueTurnSnapshot:
    stored = record.get("context_snapshot")
    if isinstance(stored, dict):
        return DialogueTurnSnapshot(
            request_id=str(stored.get("request_id") or record["request_id"]),
            turn_id=str(stored.get("turn_id") or record["turn_id"]),
            conversation_id=str(stored.get("conversation_id") or record["conversation_id"]),
            user_message=str(stored.get("user_message") or record["user_message"]),
            model_profile=str(stored.get("model_profile") or ""),
            user_profile=str(stored.get("user_profile") or ""),
            compact_history=str(stored.get("compact_history") or ""),
            recent_history=list(stored.get("recent_history") or []),
            created_at=str(stored.get("created_at") or record["created_at"]),
            initial_reply=stored.get("initial_reply") or record.get("initial_reply") or "",
            initial_reply_turn_id=(
                stored.get("initial_reply_turn_id")
                or record.get("initial_reply_turn_id")
                or ""
            ),
        )
    return DialogueTurnSnapshot(
        request_id=record["request_id"],
        turn_id=record["turn_id"],
        conversation_id=record["conversation_id"],
        user_message=record["user_message"],
        model_profile="",
        user_profile="",
        compact_history="",
        recent_history=[],
        created_at=record["created_at"],
        initial_reply=record.get("initial_reply") or "",
        initial_reply_turn_id=record.get("initial_reply_turn_id") or "",
    )


def _followup_context_window_turns(deps: DialogueDependencies) -> int:
    if deps.followup_context_window_turns is not None:
        return deps.followup_context_window_turns
    try:
        config = load_app_config()
    except Exception:
        return DEFAULT_FOLLOWUP_CONTEXT_WINDOW_TURNS
    dialogue_config = config.get("dialogue")
    if not isinstance(dialogue_config, dict):
        return DEFAULT_FOLLOWUP_CONTEXT_WINDOW_TURNS
    followup_config = dialogue_config.get("followup")
    if not isinstance(followup_config, dict):
        return DEFAULT_FOLLOWUP_CONTEXT_WINDOW_TURNS
    value = followup_config.get("context_window_turns", DEFAULT_FOLLOWUP_CONTEXT_WINDOW_TURNS)
    return value if isinstance(value, int) else DEFAULT_FOLLOWUP_CONTEXT_WINDOW_TURNS


def _read_history_window(
    deps: DialogueDependencies,
    conversation_id: str,
    context_window_turns: int,
) -> list[dict]:
    if context_window_turns == -1:
        return deps.get_recent_history(conversation_id, 1_000_000)
    if context_window_turns <= 0:
        return []
    return deps.get_recent_history(conversation_id, context_window_turns)


def _newer_turns_since_original_request(
    latest_recent_history: list[dict],
    parent_user_turn_id: str,
    context_window_turns: int,
) -> list[dict]:
    if not isinstance(latest_recent_history, list):
        return []

    start_index: int | None = None
    for index, turn in enumerate(latest_recent_history):
        if isinstance(turn, dict) and turn.get("turn_id") == parent_user_turn_id:
            start_index = index + 1
            break
    if start_index is None:
        newer = list(latest_recent_history)
    else:
        newer = list(latest_recent_history[start_index:])

    if context_window_turns != -1 and context_window_turns >= 0:
        newer = newer[-context_window_turns:]
    return [dict(turn) for turn in newer if isinstance(turn, dict)]


def _publish_followup_event(result: dict) -> None:
    payload = {
        "conversation_id": result["conversation_id"],
        "request_id": result["request_id"],
        "parent_user_turn_id": result.get("parent_user_turn_id", ""),
        "parent_initial_reply_turn_id": result.get("parent_initial_reply_turn_id", ""),
        "followup_turn_id": result.get("followup_turn_id", ""),
        "original_user_query": result.get("original_user_query", ""),
        "initial_reply": result.get("initial_reply", ""),
        "followup_type": result.get("followup_type", "supplement"),
        "reply": result.get("reply", ""),
    }
    _followup_delivery_bus.publish(result["conversation_id"], payload)


def _get_retrieval_executor(deps: DialogueDependencies) -> ThreadPoolExecutor:
    global _retrieval_executor

    with _retrieval_executor_lock:
        if _retrieval_executor is None:
            _retrieval_executor = ThreadPoolExecutor(
                max_workers=_retrieval_max_workers(deps),
                thread_name_prefix="memory-retrieval",
            )
        return _retrieval_executor


def _retrieval_max_workers(deps: DialogueDependencies) -> int:
    if deps.retrieval_max_workers is not None:
        return max(1, deps.retrieval_max_workers)
    try:
        config = load_app_config()
    except Exception:
        return DEFAULT_RETRIEVAL_MAX_WORKERS
    dialogue_config = config.get("dialogue")
    if not isinstance(dialogue_config, dict):
        return DEFAULT_RETRIEVAL_MAX_WORKERS
    retrieval_config = dialogue_config.get("retrieval")
    if not isinstance(retrieval_config, dict):
        return DEFAULT_RETRIEVAL_MAX_WORKERS
    value = retrieval_config.get("max_workers", DEFAULT_RETRIEVAL_MAX_WORKERS)
    if not isinstance(value, int):
        return DEFAULT_RETRIEVAL_MAX_WORKERS
    return max(1, value)


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


def _memory_ids(items: list[dict]) -> list[Any]:
    return [item.get("id") for item in items if isinstance(item, dict)]


# ---------------------------------------------------------------------------
# Dependency resolution
# ---------------------------------------------------------------------------


def _resolve(deps: DialogueDependencies) -> DialogueDependencies:
    """Fill in missing collaborators with lazy-imported real implementations."""

    overrides: dict[str, Any] = {}
    if deps.generate_initial_reply_stream is None and deps.generate_initial_reply is not None:
        overrides["generate_initial_reply_stream"] = _stream_from_one_shot_reply(
            deps.generate_initial_reply
        )
    for field_name in _PERSON_1_RESOLVERS:
        if getattr(deps, field_name) is None and field_name not in overrides:
            overrides[field_name] = _PERSON_1_RESOLVERS[field_name]()
    for field_name in _PERSON_2_RESOLVERS:
        if getattr(deps, field_name) is None and field_name not in overrides:
            overrides[field_name] = _PERSON_2_RESOLVERS[field_name]()
    for field_name in _PERSON_3_RESOLVERS:
        if getattr(deps, field_name) is None and field_name not in overrides:
            overrides[field_name] = _PERSON_3_RESOLVERS[field_name]()

    if not overrides:
        return deps
    return deps.with_overrides(**overrides)


def _stream_from_one_shot_reply(generate_initial_reply: Callable[..., dict]) -> Callable[..., Iterator[str]]:
    def _wrapped(input_data: dict, **kwargs: Any) -> Iterator[str]:
        reply = _require_reply_text(generate_initial_reply(input_data, **kwargs))
        yield reply

    return _wrapped


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
        generate_initial_reply_stream,
    )
    from src.agents.memory_retrieval_workflow import (  # type: ignore
        retrieve_relevant_memory_ids,
    )

    return {
        "generate_initial_reply": generate_initial_reply,
        "generate_initial_reply_stream": generate_initial_reply_stream,
        "generate_followup_reply": generate_followup_reply,
        "retrieve_relevant_memory_ids": retrieve_relevant_memory_ids,
    }


_PERSON_2_RESOLVERS: dict[str, Callable[[], Any]] = {
    "generate_initial_reply": lambda: _resolve_dialogue_agent()["generate_initial_reply"],
    "generate_initial_reply_stream": lambda: _resolve_dialogue_agent()[
        "generate_initial_reply_stream"
    ],
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
