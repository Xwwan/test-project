"""Recent conversation history backed by SQLite."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any

from src.memory import db, migrations


ALLOWED_ROLES = {"user", "assistant", "system"}


def append_turn(conversation_id: str, turn: dict) -> str:
    """Append one turn to a conversation and return its turn_id."""

    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("conversation_id must be a non-empty string")
    if not isinstance(turn, dict):
        raise ValueError("turn must be a dictionary")

    turn_id = turn.get("turn_id")
    if not isinstance(turn_id, str) or not turn_id:
        raise ValueError("turn.turn_id is required")

    role = turn.get("role")
    if role not in ALLOWED_ROLES:
        raise ValueError(f"unsupported turn role: {role!r}")

    content = turn.get("content")
    if not isinstance(content, str):
        raise ValueError("turn.content must be a string")

    created_at = turn.get("created_at") or _utc_now_iso()
    metadata = turn.get("metadata_json", turn.get("metadata", {}))
    metadata_json = _encode_metadata(metadata)

    with db.transaction() as connection:
        migrations.run_migrations(connection)
        try:
            connection.execute(
                """
                INSERT INTO conversation_turns (
                    conversation_id,
                    turn_id,
                    role,
                    content,
                    created_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    turn_id,
                    role,
                    content,
                    created_at,
                    metadata_json,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"turn_id already exists for conversation {conversation_id!r}: {turn_id}"
            ) from exc

    return turn_id


def get_recent_history(conversation_id: str, limit: int = 20) -> list[dict]:
    """Return the latest turns in chronological order."""

    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("conversation_id must be a non-empty string")
    if not isinstance(limit, int):
        raise ValueError("limit must be an integer")
    if limit <= 0:
        return []

    with db.transaction() as connection:
        migrations.run_migrations(connection)
        rows = connection.execute(
            """
            SELECT turn_id, role, content, created_at
            FROM conversation_turns
            WHERE conversation_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (conversation_id, limit),
        ).fetchall()

    return [
        {
            "turn_id": row["turn_id"],
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"],
        }
        for row in reversed(rows)
    ]


def _encode_metadata(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("turn metadata_json must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("turn metadata_json must be a JSON object")
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
