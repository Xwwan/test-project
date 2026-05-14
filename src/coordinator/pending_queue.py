"""In-memory queue of requests waiting for a follow-up decision.

The Dialogue Service drops a ``request_id`` here once the asynchronous
retrieval step completes, and ``POST /followups/{request_id}/run`` consumes the
entry once the Dialogue Agent has decided whether to send a second reply.

We deliberately keep this as a thin, thread-safe wrapper around ``list`` so the
MVP can be driven entirely from memory. Persistence can be layered on top by
swapping :class:`PendingFollowupQueue` for a SQLite-backed implementation.
"""

from __future__ import annotations

import threading


class PendingFollowupQueue:
    """Thread-safe FIFO queue keyed on ``request_id``.

    The queue keeps each id at most once: re-enqueuing an already pending id
    is a no-op so the public API stays idempotent.
    """

    def __init__(self) -> None:
        self._items: list[str] = []
        self._lookup: set[str] = set()
        self._lock = threading.Lock()

    def enqueue(self, request_id: str) -> bool:
        """Add ``request_id`` to the queue. Returns True if newly added."""

        _validate_request_id(request_id)
        with self._lock:
            if request_id in self._lookup:
                return False
            self._items.append(request_id)
            self._lookup.add(request_id)
            return True

    def remove(self, request_id: str) -> bool:
        """Remove ``request_id`` if present. Returns True when removed."""

        _validate_request_id(request_id)
        with self._lock:
            if request_id not in self._lookup:
                return False
            self._items.remove(request_id)
            self._lookup.discard(request_id)
            return True

    def contains(self, request_id: str) -> bool:
        _validate_request_id(request_id)
        with self._lock:
            return request_id in self._lookup

    def snapshot(self) -> list[str]:
        """Return the queued ids in insertion order without consuming them."""

        with self._lock:
            return list(self._items)

    def pop(self) -> str | None:
        """Remove and return the oldest pending id, or ``None`` if empty."""

        with self._lock:
            if not self._items:
                return None
            request_id = self._items.pop(0)
            self._lookup.discard(request_id)
            return request_id

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._lookup.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


def _validate_request_id(request_id: str) -> None:
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("request_id must be a non-empty string")
