"""SQLite persistence for onboarding workflow sessions."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
import uuid

from src.memory import db


ACTIVE = "active"
COMPLETED = "completed"
ABANDONED = "abandoned"
VALID_STATUSES = {ACTIVE, COMPLETED, ABANDONED}

ONBOARDING_SESSIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS onboarding_sessions (
    session_id TEXT PRIMARY KEY,
    conversation_id TEXT,
    status TEXT NOT NULL,
    stage INTEGER NOT NULL,
    collected_json TEXT DEFAULT '{}',
    turns_json TEXT DEFAULT '[]',
    final_payload_json TEXT DEFAULT '{}',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    completed_at DATETIME
);
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_onboarding_sessions_status ON onboarding_sessions(status);",
    "CREATE INDEX IF NOT EXISTS idx_onboarding_sessions_conversation ON onboarding_sessions(conversation_id);",
]


class OnboardingSessionNotFoundError(LookupError):
    """Raised when an onboarding session id is unknown."""


def init_db() -> None:
    """Initialize tables required by onboarding."""

    with db.transaction() as connection:
        run_migrations(connection)


def run_migrations(connection: Any) -> None:
    """Initialize only the onboarding-owned tables."""

    connection.execute(ONBOARDING_SESSIONS_TABLE_SQL)
    _ensure_column(
        connection,
        table_name="onboarding_sessions",
        column_name="final_payload_json",
        definition="TEXT DEFAULT '{}'",
    )
    for statement in INDEX_SQL:
        connection.execute(statement)


def _ensure_column(
    connection: Any,
    *,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    columns = {
        row["name"] if hasattr(row, "keys") else row[1]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
        )


def create_session(
    *,
    conversation_id: str | None = None,
    stage: int = 1,
    session_id: str | None = None,
    collected: dict | None = None,
    turns: list[dict] | None = None,
    final_payload: dict | None = None,
) -> dict:
    """Create and return a new active onboarding session."""

    normalized_session_id = session_id or f"onb_{uuid.uuid4().hex}"
    _validate_session_id(normalized_session_id)
    normalized_conversation_id = _optional_string(conversation_id, "conversation_id")
    _validate_stage(stage)
    normalized_collected = _json_object(collected or {}, "collected")
    normalized_turns = _json_array(turns or [], "turns")
    normalized_final_payload = _json_object(final_payload or {}, "final_payload")
    now = _utc_now_iso()

    with db.transaction() as connection:
        run_migrations(connection)
        connection.execute(
            """
            INSERT INTO onboarding_sessions (
                session_id,
                conversation_id,
                status,
                stage,
                collected_json,
                turns_json,
                final_payload_json,
                created_at,
                updated_at,
                completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                normalized_session_id,
                normalized_conversation_id,
                ACTIVE,
                stage,
                _encode_json(normalized_collected),
                _encode_json(normalized_turns),
                _encode_json(normalized_final_payload),
                now,
                now,
            ),
        )

    return get_session(normalized_session_id)


def get_session(session_id: str) -> dict:
    """Return one onboarding session by id."""

    _validate_session_id(session_id)
    with db.transaction() as connection:
        run_migrations(connection)
        row = connection.execute(
            """
            SELECT
                session_id,
                conversation_id,
                status,
                stage,
                collected_json,
                turns_json,
                final_payload_json,
                created_at,
                updated_at,
                completed_at
            FROM onboarding_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()

    if row is None:
        raise OnboardingSessionNotFoundError(f"unknown onboarding session: {session_id}")
    return _row_to_session(row)


def update_session(
    session_id: str,
    *,
    status: str | None = None,
    stage: int | None = None,
    collected: dict | None = None,
    turns: list[dict] | None = None,
    final_payload: dict | None = None,
    completed_at: str | None = None,
) -> dict:
    """Update mutable onboarding session fields and return the saved row."""

    _validate_session_id(session_id)
    updates: dict[str, Any] = {"updated_at": _utc_now_iso()}

    if status is not None:
        if status not in VALID_STATUSES:
            raise ValueError(f"unsupported onboarding status: {status!r}")
        updates["status"] = status
        if status == COMPLETED and completed_at is None:
            completed_at = _utc_now_iso()
    if stage is not None:
        _validate_stage(stage)
        updates["stage"] = stage
    if collected is not None:
        updates["collected_json"] = _encode_json(_json_object(collected, "collected"))
    if turns is not None:
        updates["turns_json"] = _encode_json(_json_array(turns, "turns"))
    if final_payload is not None:
        updates["final_payload_json"] = _encode_json(
            _json_object(final_payload, "final_payload")
        )
    if completed_at is not None:
        updates["completed_at"] = completed_at

    if not updates:
        return get_session(session_id)

    assignments = ", ".join(f"{column} = ?" for column in updates)
    values = list(updates.values())
    values.append(session_id)

    with db.transaction() as connection:
        run_migrations(connection)
        cursor = connection.execute(
            f"UPDATE onboarding_sessions SET {assignments} WHERE session_id = ?",
            values,
        )
        if cursor.rowcount == 0:
            raise OnboardingSessionNotFoundError(
                f"unknown onboarding session: {session_id}"
            )

    return get_session(session_id)


def _row_to_session(row: Any) -> dict:
    return {
        "session_id": row["session_id"],
        "conversation_id": row["conversation_id"] or "",
        "status": row["status"],
        "stage": row["stage"],
        "collected": _decode_json(row["collected_json"], default={}),
        "turns": _decode_json(row["turns_json"], default=[]),
        "final_payload": _decode_json(row["final_payload_json"], default={}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def _validate_session_id(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("session_id must be a non-empty string")


def _validate_stage(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("stage must be an integer")
    if value < 1 or value > 5:
        raise ValueError("stage must be between 1 and 5")


def _optional_string(value: str | None, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _json_object(value: Any, field_name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a dictionary")
    return dict(value)


def _json_array(value: Any, field_name: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return list(value)


def _encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _decode_json(raw: str | None, *, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
