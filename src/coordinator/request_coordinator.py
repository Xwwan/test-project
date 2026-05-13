"""Request Coordinator for tying initial reply, retrieval and follow-up together.

The Coordinator owns the lifecycle of one ``request_id`` / ``turn_id`` pair so
the Dialogue Service can advance the request through a well-defined state
machine without leaking state across modules.

Storage is intentionally in-memory for the MVP. The state will be lost across
process restarts; persistence (SQLite) can be added later without changing the
public function signatures below.

Public surface (matches ``docs/tasks/person-3-orchestration-curator.md``):

- :func:`create_request`
- :func:`mark_initial_reply`
- :func:`mark_retrieval_pending`
- :func:`mark_retrieval_completed`
- :func:`get_pending_followup_requests`
- :func:`mark_followup_decision`
- :func:`mark_failed`

Helpers :func:`get_request`, :func:`list_requests`, :func:`delete_request` and
:func:`reset_store` are exposed for the Dialogue Service / tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
import threading
import uuid
from copy import deepcopy

from .pending_queue import PendingFollowupQueue


REQUEST_STATUSES: tuple[str, ...] = (
    "received",
    "initial_reply_generated",
    "retrieval_pending",
    "retrieval_completed",
    "followup_generated",
    "no_followup_needed",
    "completed",
    "failed",
)

TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "followup_generated", "no_followup_needed"}
)


class RequestNotFoundError(KeyError):
    """Raised when a referenced request_id is unknown."""


class RequestStateError(RuntimeError):
    """Raised when an illegal status transition is requested."""


_store_lock = threading.RLock()
_requests: dict[str, dict] = {}
_pending_followup_queue = PendingFollowupQueue()


def reset_store() -> None:
    """Clear all in-memory request state. Intended for tests."""

    with _store_lock:
        _requests.clear()
        _pending_followup_queue.clear()


def create_request(conversation_id: str, user_message: str) -> dict:
    """Register a new user request and return its lifecycle record."""

    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("conversation_id must be a non-empty string")
    if not isinstance(user_message, str) or not user_message:
        raise ValueError("user_message must be a non-empty string")

    request_id = _generate_id("req_")
    turn_id = _generate_id("turn_")
    created_at = _utc_now_iso()

    record = {
        "request_id": request_id,
        "turn_id": turn_id,
        "conversation_id": conversation_id,
        "user_message": user_message,
        "created_at": created_at,
        "updated_at": created_at,
        "status": "received",
        "status_history": [
            {"status": "received", "at": created_at},
        ],
        "initial_reply": None,
        "retrieved_items": [],
        "followup_decision": None,
        "error": None,
    }
    with _store_lock:
        _requests[request_id] = record
    return _public_view(record)


def mark_initial_reply(request_id: str, reply: str) -> None:
    """Record that the immediate reply has been produced."""

    if not isinstance(reply, str):
        raise ValueError("reply must be a string")
    with _store_lock:
        record = _require_request(request_id)
        _ensure_can_advance_from(record, allowed={"received"})
        record["initial_reply"] = reply
        _set_status(record, "initial_reply_generated")


def mark_retrieval_pending(request_id: str) -> None:
    """Mark the retrieval step as in-flight (asynchronous workflow)."""

    with _store_lock:
        record = _require_request(request_id)
        _ensure_can_advance_from(
            record,
            allowed={"initial_reply_generated"},
        )
        _set_status(record, "retrieval_pending")


def mark_retrieval_completed(
    request_id: str,
    retrieved_items: list[dict],
) -> None:
    """Attach retrieved memory items and queue the request for follow-up."""

    if not isinstance(retrieved_items, list):
        raise ValueError("retrieved_items must be a list")
    for item in retrieved_items:
        if not isinstance(item, dict):
            raise ValueError("each retrieved item must be a dictionary")

    with _store_lock:
        record = _require_request(request_id)
        _ensure_can_advance_from(
            record,
            allowed={
                "initial_reply_generated",
                "retrieval_pending",
            },
        )
        record["retrieved_items"] = deepcopy(retrieved_items)
        _set_status(record, "retrieval_completed")
        _pending_followup_queue.enqueue(request_id)


def get_pending_followup_requests() -> list[dict]:
    """Return public views of requests waiting on a follow-up decision."""

    with _store_lock:
        snapshot = _pending_followup_queue.snapshot()
        return [_public_view(_requests[rid]) for rid in snapshot if rid in _requests]


def mark_followup_decision(request_id: str, decision: dict) -> None:
    """Record the Dialogue Agent follow-up decision and finalize the request."""

    if not isinstance(decision, dict):
        raise ValueError("decision must be a dictionary")

    decision_name = decision.get("decision")
    if decision_name not in {"followup", "no_followup"}:
        raise ValueError("decision.decision must be 'followup' or 'no_followup'")

    with _store_lock:
        record = _require_request(request_id)
        _ensure_can_advance_from(record, allowed={"retrieval_completed"})
        record["followup_decision"] = deepcopy(decision)
        _pending_followup_queue.remove(request_id)
        if decision_name == "followup":
            _set_status(record, "followup_generated")
        else:
            _set_status(record, "no_followup_needed")


def mark_failed(request_id: str, reason: str) -> None:
    """Move a request into the terminal ``failed`` state with a reason."""

    if not isinstance(reason, str) or not reason:
        raise ValueError("reason must be a non-empty string")

    with _store_lock:
        record = _require_request(request_id)
        record["error"] = reason
        _pending_followup_queue.remove(request_id)
        _set_status(record, "failed", allow_terminal=True)


def get_request(request_id: str) -> dict:
    """Return a public view of a single request."""

    with _store_lock:
        return _public_view(_require_request(request_id))


def list_requests() -> list[dict]:
    """Return public views of all known requests in creation order."""

    with _store_lock:
        return [_public_view(record) for record in _requests.values()]


def delete_request(request_id: str) -> None:
    with _store_lock:
        if request_id in _requests:
            _requests.pop(request_id)
        _pending_followup_queue.remove(request_id)


def _require_request(request_id: str) -> dict:
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("request_id must be a non-empty string")
    record = _requests.get(request_id)
    if record is None:
        raise RequestNotFoundError(f"unknown request_id: {request_id!r}")
    return record


def _ensure_can_advance_from(record: dict, allowed: set[str]) -> None:
    current = record["status"]
    if current == "failed":
        raise RequestStateError(
            f"request {record['request_id']} has already failed and cannot advance"
        )
    if current not in allowed:
        raise RequestStateError(
            f"cannot advance request {record['request_id']} from status {current!r}"
        )


def _set_status(record: dict, status: str, allow_terminal: bool = False) -> None:
    if status not in REQUEST_STATUSES:
        raise ValueError(f"unsupported request status: {status!r}")
    if not allow_terminal and record["status"] in TERMINAL_STATUSES:
        raise RequestStateError(
            f"request {record['request_id']} is already in terminal status {record['status']!r}"
        )
    now = _utc_now_iso()
    record["status"] = status
    record["updated_at"] = now
    record["status_history"].append({"status": status, "at": now})


def _public_view(record: dict) -> dict:
    """Return a defensive copy so callers cannot mutate internal state."""

    return deepcopy(record)


def _generate_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex}"


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
