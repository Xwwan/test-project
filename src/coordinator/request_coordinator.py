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
    "retrieval_failed",
    "followup_generated",
    "no_followup_needed",
    "followup_failed",
    "completed",
    "failed",
)

TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "completed",
        "failed",
        "retrieval_failed",
        "followup_generated",
        "no_followup_needed",
        "followup_failed",
    }
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
        "initial_reply_turn_id": None,
        "context_snapshot": None,
        "retrieved_items": [],
        "followup_decision": None,
        "error": None,
        "retrieval_status": None,
        "retrieval_error": None,
        "followup_status": None,
        "followup_error": None,
        "delivery_status": "none",
    }
    with _store_lock:
        _requests[request_id] = record
    return _public_view(record)


def mark_initial_reply(
    request_id: str,
    reply: str,
    *,
    assistant_turn_id: str | None = None,
) -> None:
    """Record that the immediate reply has been produced."""

    if not isinstance(reply, str):
        raise ValueError("reply must be a string")
    if assistant_turn_id is not None and not isinstance(assistant_turn_id, str):
        raise ValueError("assistant_turn_id must be a string")
    with _store_lock:
        record = _require_request(request_id)
        _ensure_can_advance_from(
            record,
            allowed={
                "received",
                "retrieval_pending",
                "retrieval_completed",
                "retrieval_failed",
            },
        )
        record["initial_reply"] = reply
        record["initial_reply_turn_id"] = assistant_turn_id
        if record["status"] == "received":
            _set_status(record, "initial_reply_generated")
        else:
            record["updated_at"] = _utc_now_iso()


def attach_context_snapshot(request_id: str, snapshot: dict) -> None:
    """Attach the immutable request-time dialogue context snapshot."""

    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be a dictionary")
    with _store_lock:
        record = _require_request(request_id)
        record["context_snapshot"] = deepcopy(snapshot)


def mark_retrieval_pending(request_id: str) -> None:
    """Mark the retrieval step as in-flight (asynchronous workflow)."""

    with _store_lock:
        record = _require_request(request_id)
        _ensure_can_advance_from(
            record,
            allowed={"received", "initial_reply_generated"},
        )
        record["retrieval_status"] = "pending"
        record["retrieval_error"] = None
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
        record["retrieval_status"] = "completed"
        record["retrieval_error"] = None
        record["followup_status"] = "pending"
        _set_status(record, "retrieval_completed")
        _pending_followup_queue.enqueue(request_id)


def mark_retrieval_failed(request_id: str, reason: str) -> None:
    """Record a retrieval failure without failing the already-produced reply."""

    if not isinstance(reason, str) or not reason:
        raise ValueError("reason must be a non-empty string")

    with _store_lock:
        record = _require_request(request_id)
        if record["status"] not in {
            "initial_reply_generated",
            "retrieval_pending",
            "retrieval_failed",
        }:
            raise RequestStateError(
                f"cannot mark retrieval failed for request {record['request_id']} "
                f"from status {record['status']!r}"
            )
        if record["status"] in TERMINAL_STATUSES and record["status"] != "retrieval_failed":
            raise RequestStateError(
                f"request {record['request_id']} is already in terminal status "
                f"{record['status']!r}"
            )
        record["retrieval_status"] = "failed"
        record["retrieval_error"] = reason
        record["followup_status"] = "failed"
        record["followup_error"] = reason
        _pending_followup_queue.remove(request_id)
        _set_status(record, "retrieval_failed", allow_terminal=True)


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
        record["followup_status"] = (
            "generated" if decision_name == "followup" else "no_followup"
        )
        record["followup_error"] = None
        record["delivery_status"] = "pending" if decision_name == "followup" else "none"
        _pending_followup_queue.remove(request_id)
        if decision_name == "followup":
            _set_status(record, "followup_generated")
        else:
            _set_status(record, "no_followup_needed")


def mark_followup_failed(request_id: str, reason: str) -> None:
    """Record a follow-up generation failure without failing initial reply."""

    if not isinstance(reason, str) or not reason:
        raise ValueError("reason must be a non-empty string")

    with _store_lock:
        record = _require_request(request_id)
        _ensure_can_advance_from(record, allowed={"retrieval_completed"})
        record["followup_status"] = "failed"
        record["followup_error"] = reason
        _pending_followup_queue.remove(request_id)
        _set_status(record, "followup_failed")


def mark_followup_delivered(request_id: str) -> None:
    """Mark a generated follow-up event as delivered to a conversation stream."""

    with _store_lock:
        record = _require_request(request_id)
        if record.get("delivery_status") != "pending":
            return
        record["delivery_status"] = "delivered"
        record["updated_at"] = _utc_now_iso()


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
