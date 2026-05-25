"""SQLite persistence for unified interaction sessions and runs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from src.memory import db


WORKFLOW_CHAT = "chat"
WORKFLOW_ONBOARDING = "onboarding"
VALID_WORKFLOWS = {WORKFLOW_CHAT, WORKFLOW_ONBOARDING}

SESSION_ACTIVE = "active"
SESSION_COMPLETED = "completed"
SESSION_ABANDONED = "abandoned"
VALID_SESSION_STATUSES = {SESSION_ACTIVE, SESSION_COMPLETED, SESSION_ABANDONED}

RUN_RUNNING = "running"
RUN_COMPLETED = "completed"
RUN_FAILED = "failed"
VALID_RUN_STATUSES = {RUN_RUNNING, RUN_COMPLETED, RUN_FAILED}

PLAYBACK_IDLE = "idle"
PLAYBACK_DONE = "done"
PLAYBACK_ERROR = "error"
VALID_PLAYBACK_STATUSES = {PLAYBACK_IDLE, PLAYBACK_DONE, PLAYBACK_ERROR}

INTERACTION_SESSIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS interaction_sessions (
    interaction_session_id TEXT PRIMARY KEY,
    workflow TEXT NOT NULL,
    conversation_id TEXT,
    onboarding_session_id TEXT,
    status TEXT NOT NULL,
    input_mode TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
"""

INTERACTION_RUNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS interaction_runs (
    run_id TEXT PRIMARY KEY,
    interaction_session_id TEXT NOT NULL,
    workflow TEXT NOT NULL,
    input_mode TEXT,
    transcript TEXT,
    reply TEXT,
    status TEXT NOT NULL,
    error TEXT,
    request_id TEXT,
    onboarding_session_id TEXT,
    stage INTEGER,
    playback_key TEXT,
    playback_status TEXT DEFAULT 'idle',
    playback_error TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    completed_at DATETIME
);
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_interaction_sessions_workflow ON interaction_sessions(workflow);",
    "CREATE INDEX IF NOT EXISTS idx_interaction_sessions_conversation ON interaction_sessions(conversation_id);",
    "CREATE INDEX IF NOT EXISTS idx_interaction_runs_session ON interaction_runs(interaction_session_id);",
    "CREATE INDEX IF NOT EXISTS idx_interaction_runs_status ON interaction_runs(status);",
]


class InteractionSessionNotFoundError(LookupError):
    """Raised when an interaction session id is unknown."""


class InteractionRunNotFoundError(LookupError):
    """Raised when an interaction run id is unknown."""


def init_db() -> None:
    """Initialize tables required by the unified interaction API."""

    with db.transaction() as connection:
        run_migrations(connection)


def run_migrations(connection: Any) -> None:
    """Create or update interaction-owned tables."""

    connection.execute(INTERACTION_SESSIONS_TABLE_SQL)
    connection.execute(INTERACTION_RUNS_TABLE_SQL)
    for column_name, definition in {
        "request_id": "TEXT",
        "onboarding_session_id": "TEXT",
        "stage": "INTEGER",
        "playback_key": "TEXT",
        "playback_status": "TEXT DEFAULT 'idle'",
        "playback_error": "TEXT",
    }.items():
        _ensure_column(
            connection,
            table_name="interaction_runs",
            column_name=column_name,
            definition=definition,
        )
    for statement in INDEX_SQL:
        connection.execute(statement)


def create_session(
    *,
    workflow: str,
    conversation_id: str | None = None,
    onboarding_session_id: str | None = None,
    input_mode: str | None = None,
    status: str = SESSION_ACTIVE,
    interaction_session_id: str | None = None,
) -> dict:
    """Create and return one interaction session."""

    normalized_session_id = interaction_session_id or f"isess_{uuid.uuid4().hex}"
    _validate_id(normalized_session_id, "interaction_session_id")
    normalized_workflow = _validate_workflow(workflow)
    normalized_conversation_id = _optional_string(conversation_id, "conversation_id")
    normalized_onboarding_session_id = _optional_string(
        onboarding_session_id,
        "onboarding_session_id",
    )
    normalized_input_mode = _optional_string(input_mode, "input_mode")
    normalized_status = _validate_session_status(status)
    now = _utc_now_iso()

    with db.transaction() as connection:
        run_migrations(connection)
        connection.execute(
            """
            INSERT INTO interaction_sessions (
                interaction_session_id,
                workflow,
                conversation_id,
                onboarding_session_id,
                status,
                input_mode,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_session_id,
                normalized_workflow,
                normalized_conversation_id,
                normalized_onboarding_session_id,
                normalized_status,
                normalized_input_mode,
                now,
                now,
            ),
        )

    return get_session(normalized_session_id)


def get_session(interaction_session_id: str) -> dict:
    """Return one interaction session by id."""

    _validate_id(interaction_session_id, "interaction_session_id")
    with db.transaction() as connection:
        run_migrations(connection)
        row = connection.execute(
            """
            SELECT
                interaction_session_id,
                workflow,
                conversation_id,
                onboarding_session_id,
                status,
                input_mode,
                created_at,
                updated_at
            FROM interaction_sessions
            WHERE interaction_session_id = ?
            """,
            (interaction_session_id,),
        ).fetchone()

    if row is None:
        raise InteractionSessionNotFoundError(
            f"unknown interaction session: {interaction_session_id}"
        )
    return _session_row_to_dict(row)


def update_session(
    interaction_session_id: str,
    *,
    status: str | None = None,
    onboarding_session_id: str | None = None,
    input_mode: str | None = None,
) -> dict:
    """Update mutable interaction session fields and return the saved row."""

    _validate_id(interaction_session_id, "interaction_session_id")
    updates: dict[str, Any] = {"updated_at": _utc_now_iso()}
    if status is not None:
        updates["status"] = _validate_session_status(status)
    if onboarding_session_id is not None:
        updates["onboarding_session_id"] = _optional_string(
            onboarding_session_id,
            "onboarding_session_id",
        )
    if input_mode is not None:
        updates["input_mode"] = _optional_string(input_mode, "input_mode")

    assignments = ", ".join(f"{column} = ?" for column in updates)
    values = list(updates.values())
    values.append(interaction_session_id)
    with db.transaction() as connection:
        run_migrations(connection)
        cursor = connection.execute(
            f"UPDATE interaction_sessions SET {assignments} WHERE interaction_session_id = ?",
            values,
        )
        if cursor.rowcount == 0:
            raise InteractionSessionNotFoundError(
                f"unknown interaction session: {interaction_session_id}"
            )
    return get_session(interaction_session_id)


def create_run(
    *,
    interaction_session_id: str,
    workflow: str,
    input_mode: str | None = None,
    transcript: str | None = None,
    request_id: str | None = None,
    onboarding_session_id: str | None = None,
    stage: int | None = None,
    status: str = RUN_RUNNING,
    run_id: str | None = None,
) -> dict:
    """Create and return one interaction run."""

    normalized_run_id = run_id or f"irun_{uuid.uuid4().hex}"
    _validate_id(normalized_run_id, "run_id")
    _validate_id(interaction_session_id, "interaction_session_id")
    normalized_workflow = _validate_workflow(workflow)
    normalized_input_mode = _optional_string(input_mode, "input_mode")
    normalized_transcript = _optional_string(transcript, "transcript")
    normalized_request_id = _optional_string(request_id, "request_id")
    normalized_onboarding_session_id = _optional_string(
        onboarding_session_id,
        "onboarding_session_id",
    )
    if stage is not None:
        _validate_stage(stage)
    normalized_status = _validate_run_status(status)
    now = _utc_now_iso()
    get_session(interaction_session_id)

    with db.transaction() as connection:
        run_migrations(connection)
        connection.execute(
            """
            INSERT INTO interaction_runs (
                run_id,
                interaction_session_id,
                workflow,
                input_mode,
                transcript,
                reply,
                status,
                error,
                request_id,
                onboarding_session_id,
                stage,
                playback_key,
                playback_status,
                playback_error,
                created_at,
                updated_at,
                completed_at
            )
            VALUES (?, ?, ?, ?, ?, '', ?, '', ?, ?, ?, '', ?, '', ?, ?, NULL)
            """,
            (
                normalized_run_id,
                interaction_session_id,
                normalized_workflow,
                normalized_input_mode,
                normalized_transcript,
                normalized_status,
                normalized_request_id,
                normalized_onboarding_session_id,
                stage,
                PLAYBACK_IDLE,
                now,
                now,
            ),
        )

    return get_run(normalized_run_id)


def get_run(run_id: str) -> dict:
    """Return one interaction run by id."""

    _validate_id(run_id, "run_id")
    with db.transaction() as connection:
        run_migrations(connection)
        row = connection.execute(
            """
            SELECT
                run_id,
                interaction_session_id,
                workflow,
                input_mode,
                transcript,
                reply,
                status,
                error,
                request_id,
                onboarding_session_id,
                stage,
                playback_key,
                playback_status,
                playback_error,
                created_at,
                updated_at,
                completed_at
            FROM interaction_runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()

    if row is None:
        raise InteractionRunNotFoundError(f"unknown interaction run: {run_id}")
    return _run_row_to_dict(row)


def update_run(
    run_id: str,
    *,
    status: str | None = None,
    transcript: str | None = None,
    reply: str | None = None,
    error: str | None = None,
    request_id: str | None = None,
    onboarding_session_id: str | None = None,
    stage: int | None = None,
    playback_key: str | None = None,
    playback_status: str | None = None,
    playback_error: str | None = None,
    completed_at: str | None = None,
) -> dict:
    """Update mutable interaction run fields and return the saved row."""

    _validate_id(run_id, "run_id")
    updates: dict[str, Any] = {"updated_at": _utc_now_iso()}
    if status is not None:
        updates["status"] = _validate_run_status(status)
        if status in {RUN_COMPLETED, RUN_FAILED} and completed_at is None:
            completed_at = _utc_now_iso()
    if transcript is not None:
        updates["transcript"] = _optional_string(transcript, "transcript")
    if reply is not None:
        updates["reply"] = _optional_string(reply, "reply")
    if error is not None:
        updates["error"] = _optional_string(error, "error")
    if request_id is not None:
        updates["request_id"] = _optional_string(request_id, "request_id")
    if onboarding_session_id is not None:
        updates["onboarding_session_id"] = _optional_string(
            onboarding_session_id,
            "onboarding_session_id",
        )
    if stage is not None:
        _validate_stage(stage)
        updates["stage"] = stage
    if playback_key is not None:
        updates["playback_key"] = _optional_string(playback_key, "playback_key")
    if playback_status is not None:
        updates["playback_status"] = _validate_playback_status(playback_status)
    if playback_error is not None:
        updates["playback_error"] = _optional_string(
            playback_error,
            "playback_error",
        )
    if completed_at is not None:
        updates["completed_at"] = completed_at

    assignments = ", ".join(f"{column} = ?" for column in updates)
    values = list(updates.values())
    values.append(run_id)
    with db.transaction() as connection:
        run_migrations(connection)
        cursor = connection.execute(
            f"UPDATE interaction_runs SET {assignments} WHERE run_id = ?",
            values,
        )
        if cursor.rowcount == 0:
            raise InteractionRunNotFoundError(f"unknown interaction run: {run_id}")
    return get_run(run_id)


def list_runs_for_session(
    interaction_session_id: str,
    *,
    limit: int = 50,
) -> list[dict]:
    """Return recent runs for one interaction session, oldest first."""

    _validate_id(interaction_session_id, "interaction_session_id")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    get_session(interaction_session_id)
    with db.transaction() as connection:
        run_migrations(connection)
        rows = connection.execute(
            """
            SELECT
                run_id,
                interaction_session_id,
                workflow,
                input_mode,
                transcript,
                reply,
                status,
                error,
                request_id,
                onboarding_session_id,
                stage,
                playback_key,
                playback_status,
                playback_error,
                created_at,
                updated_at,
                completed_at
            FROM interaction_runs
            WHERE interaction_session_id = ?
            ORDER BY created_at ASC, rowid ASC
            LIMIT ?
            """,
            (interaction_session_id, limit),
        ).fetchall()
    return [_run_row_to_dict(row) for row in rows]


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


def _session_row_to_dict(row: Any) -> dict:
    return {
        "interaction_session_id": row["interaction_session_id"],
        "workflow": row["workflow"],
        "conversation_id": row["conversation_id"] or "",
        "onboarding_session_id": row["onboarding_session_id"] or "",
        "status": row["status"],
        "input_mode": row["input_mode"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _run_row_to_dict(row: Any) -> dict:
    return {
        "run_id": row["run_id"],
        "interaction_session_id": row["interaction_session_id"],
        "workflow": row["workflow"],
        "input_mode": row["input_mode"] or "",
        "transcript": row["transcript"] or "",
        "reply": row["reply"] or "",
        "status": row["status"],
        "error": row["error"] or "",
        "request_id": row["request_id"] or "",
        "onboarding_session_id": row["onboarding_session_id"] or "",
        "stage": row["stage"],
        "playback_key": row["playback_key"] or "",
        "playback_status": row["playback_status"] or PLAYBACK_IDLE,
        "playback_error": row["playback_error"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def _validate_workflow(value: str) -> str:
    if not isinstance(value, str) or value not in VALID_WORKFLOWS:
        raise ValueError("workflow must be one of {'chat', 'onboarding'}")
    return value


def _validate_session_status(value: str) -> str:
    if not isinstance(value, str) or value not in VALID_SESSION_STATUSES:
        raise ValueError(
            "interaction session status must be one of "
            "{'active', 'completed', 'abandoned'}"
        )
    return value


def _validate_run_status(value: str) -> str:
    if not isinstance(value, str) or value not in VALID_RUN_STATUSES:
        raise ValueError(
            "interaction run status must be one of {'running', 'completed', 'failed'}"
        )
    return value


def _validate_playback_status(value: str) -> str:
    if not isinstance(value, str) or value not in VALID_PLAYBACK_STATUSES:
        raise ValueError("playback status must be one of {'idle', 'done', 'error'}")
    return value


def _validate_id(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


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


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
