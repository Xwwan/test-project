"""Compact conversation history backed by SQLite."""

from __future__ import annotations

from datetime import datetime, timezone

from src.memory import db, migrations


def get_compact_history(conversation_id: str) -> str:
    """Return the compact history text for a conversation, or an empty string."""

    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("conversation_id must be a non-empty string")

    with db.transaction() as connection:
        migrations.run_migrations(connection)
        row = connection.execute(
            "SELECT compact FROM compact_histories WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    if row is None:
        return ""
    return row["compact"]


def update_compact_history(conversation_id: str, compact: str) -> None:
    """Replace the compact history text for a conversation."""

    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("conversation_id must be a non-empty string")
    if not isinstance(compact, str):
        raise ValueError("compact must be a string")

    with db.transaction() as connection:
        migrations.run_migrations(connection)
        connection.execute(
            """
            INSERT INTO compact_histories (conversation_id, compact, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(conversation_id)
            DO UPDATE SET compact = excluded.compact, updated_at = excluded.updated_at
            """,
            (conversation_id, compact, _utc_now_iso()),
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
