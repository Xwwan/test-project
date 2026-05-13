"""Public Memory Store API for the rest of the application."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Iterable

from . import db, migrations
from .models import (
    DEFAULT_MEMORY_VALUES,
    DICT_JSON_FIELDS,
    INSERTABLE_COLUMNS,
    JSON_FIELDS,
    LIGHTWEIGHT_COLUMNS,
    LIST_JSON_FIELDS,
    MEMORY_COLUMNS,
    MEMORY_STATUSES,
    MEMORY_TYPES,
    SENSITIVITY_LEVELS,
    UPDATABLE_COLUMNS,
)


def init_db() -> None:
    """Create all Person 1 SQLite tables if needed."""

    migrations.init_db()


def create_memory_item(payload: dict) -> dict:
    """Insert a MemoryItem and return the complete row."""

    with db.transaction() as connection:
        migrations.run_migrations(connection)
        return _create_memory_item(connection, payload)


def update_memory_item(memory_id: int, payload: dict) -> dict:
    """Update a MemoryItem and return the complete row."""

    with db.transaction() as connection:
        migrations.run_migrations(connection)
        return _update_memory_item(connection, memory_id, payload)


def list_lightweight_memory_items(status: str = "active") -> list[dict]:
    """Return the lightweight fields used by Memory Retrieval Workflow."""

    with db.transaction() as connection:
        migrations.run_migrations(connection)
        columns = ", ".join(LIGHTWEIGHT_COLUMNS)
        if status is None:
            rows = connection.execute(
                f"SELECT {columns} FROM memory_items ORDER BY id ASC"
            ).fetchall()
        else:
            _validate_status(status)
            rows = connection.execute(
                f"SELECT {columns} FROM memory_items WHERE status = ? ORDER BY id ASC",
                (status,),
            ).fetchall()
        return [_row_to_memory_item(row, columns=LIGHTWEIGHT_COLUMNS) for row in rows]


def get_memory_items_by_ids(ids: list[int]) -> list[dict]:
    """Return complete MemoryItems in the same order as the requested IDs."""

    with db.transaction() as connection:
        migrations.run_migrations(connection)
        return _get_memory_items_by_ids(connection, ids)


def apply_memory_operations(operations: list[dict]) -> list[dict]:
    """Apply Memory Curator operations as one transaction."""

    if not isinstance(operations, list):
        raise ValueError("operations must be a list of operation dictionaries")

    results: list[dict] = []
    with db.transaction() as connection:
        migrations.run_migrations(connection)
        for operation in operations:
            if not isinstance(operation, dict):
                raise ValueError("each memory operation must be a dictionary")
            results.append(_apply_memory_operation(connection, operation))
    return results


def _apply_memory_operation(connection: sqlite3.Connection, operation: dict) -> dict:
    operation_name = operation.get("operation")
    payload = operation.get("payload") or {}

    if operation_name == "create":
        item = _create_memory_item(connection, payload)
        return _operation_result(operation_name, item)

    if operation_name == "merge":
        target_id = (
            _normalize_id(operation["target_id"], "target_id")
            if "target_id" in operation and operation["target_id"] is not None
            else None
        )
        return _merge_memory_items(connection, target_id, payload, operation)

    target_id = _require_target_id(operation)

    if operation_name == "update":
        item = _update_memory_item(connection, target_id, payload)
        return _operation_result(operation_name, item)

    if operation_name == "archive":
        item = _update_memory_item(connection, target_id, {"status": "archived"})
        return _operation_result(operation_name, item)

    if operation_name == "link":
        ids_to_link = _extract_id_list(
            operation,
            payload,
            keys=("references_json", "reference_ids", "linked_ids"),
        )
        item = _append_json_ids(connection, target_id, "references_json", ids_to_link)
        return _operation_result(operation_name, item)

    if operation_name == "conflict_mark":
        conflict_ids = _extract_id_list(
            operation,
            payload,
            keys=("conflict_with_json", "conflict_ids"),
        )
        item = _append_json_ids(
            connection,
            target_id,
            "conflict_with_json",
            conflict_ids,
        )
        return _operation_result(operation_name, item)

    raise ValueError(f"unsupported memory operation: {operation_name!r}")


def _merge_memory_items(
    connection: sqlite3.Connection,
    target_id: int | None,
    payload: dict,
    operation: dict,
) -> dict:
    update_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"merged_ids", "source_ids", "deprecated_ids"}
    }

    if target_id is None:
        primary = _create_memory_item(connection, update_payload)
    elif update_payload:
        primary = _update_memory_item(connection, target_id, update_payload)
    else:
        primary = _get_memory_item(connection, target_id)

    merged_ids = _extract_id_list(
        operation,
        payload,
        keys=("merged_ids", "source_ids", "deprecated_ids"),
        required=False,
    )
    deprecated_items = []
    for merged_id in merged_ids:
        if merged_id == primary["id"]:
            continue
        deprecated_items.append(
            _update_memory_item(
                connection,
                merged_id,
                {
                    "status": "deprecated",
                    "superseded_by": primary["id"],
                },
            )
        )

    return _operation_result(
        "merge",
        primary,
        deprecated_items=deprecated_items,
    )


def _operation_result(operation_name: str, item: dict, **extra: Any) -> dict:
    result = {
        "operation": operation_name,
        "memory_item": item,
    }
    result.update(item)
    result.update(extra)
    return result


def _create_memory_item(connection: sqlite3.Connection, payload: dict) -> dict:
    normalized = _normalize_insert_payload(payload)
    columns = list(normalized)
    placeholders = ", ".join("?" for _ in columns)
    column_sql = ", ".join(columns)
    values = [normalized[column] for column in columns]
    cursor = connection.execute(
        f"INSERT INTO memory_items ({column_sql}) VALUES ({placeholders})",
        values,
    )
    return _get_memory_item(connection, int(cursor.lastrowid))


def _update_memory_item(
    connection: sqlite3.Connection,
    memory_id: int,
    payload: dict,
) -> dict:
    memory_id = _normalize_id(memory_id, "memory_id")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dictionary")

    _get_memory_item(connection, memory_id)
    normalized = _normalize_update_payload(payload)
    assignments = ", ".join(f"{column} = ?" for column in normalized)
    values = [normalized[column] for column in normalized]
    values.append(memory_id)
    connection.execute(
        f"UPDATE memory_items SET {assignments} WHERE id = ?",
        values,
    )
    return _get_memory_item(connection, memory_id)


def _append_json_ids(
    connection: sqlite3.Connection,
    memory_id: int,
    field_name: str,
    ids_to_add: list[int],
) -> dict:
    item = _get_memory_item(connection, memory_id)
    current_ids = item[field_name]
    if not isinstance(current_ids, list):
        raise ValueError(f"{field_name} must contain a JSON list")

    merged_ids = _merge_unique_ids(current_ids, ids_to_add)
    return _update_memory_item(connection, memory_id, {field_name: merged_ids})


def _get_memory_items_by_ids(
    connection: sqlite3.Connection,
    ids: list[int],
) -> list[dict]:
    if not isinstance(ids, list):
        raise ValueError("ids must be a list of integers")
    normalized_ids = [_normalize_id(value, "ids") for value in ids]
    if not normalized_ids:
        return []

    unique_ids = list(dict.fromkeys(normalized_ids))
    placeholders = ", ".join("?" for _ in unique_ids)
    rows = connection.execute(
        f"SELECT * FROM memory_items WHERE id IN ({placeholders})",
        unique_ids,
    ).fetchall()
    by_id = {int(row["id"]): _row_to_memory_item(row) for row in rows}
    return [by_id[memory_id] for memory_id in unique_ids if memory_id in by_id]


def _get_memory_item(connection: sqlite3.Connection, memory_id: int) -> dict:
    row = connection.execute(
        "SELECT * FROM memory_items WHERE id = ?",
        (_normalize_id(memory_id, "memory_id"),),
    ).fetchone()
    if row is None:
        raise KeyError(f"memory item not found: {memory_id}")
    return _row_to_memory_item(row)


def _normalize_insert_payload(payload: dict) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dictionary")
    unknown_fields = set(payload) - set(INSERTABLE_COLUMNS)
    if unknown_fields:
        fields = ", ".join(sorted(unknown_fields))
        raise ValueError(f"unsupported memory fields: {fields}")
    if not payload.get("summary"):
        raise ValueError("summary is required")
    if not payload.get("content"):
        raise ValueError("content is required")

    now = _utc_now_iso()
    complete_payload = dict(DEFAULT_MEMORY_VALUES)
    complete_payload.update(payload)
    complete_payload.setdefault("created_at", now)
    complete_payload.setdefault("updated_at", now)
    complete_payload["created_at"] = complete_payload.get("created_at") or now
    complete_payload["updated_at"] = complete_payload.get("updated_at") or now

    normalized = {
        column: _normalize_column_value(column, complete_payload[column])
        for column in INSERTABLE_COLUMNS
        if column in complete_payload
    }
    return normalized


def _normalize_update_payload(payload: dict) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for column, value in payload.items():
        if column not in UPDATABLE_COLUMNS:
            raise ValueError(f"field cannot be updated: {column}")
        normalized[column] = _normalize_column_value(column, value)
    normalized["updated_at"] = _utc_now_iso()
    return normalized


def _normalize_column_value(column: str, value: Any) -> Any:
    if column == "memory_type":
        if value not in MEMORY_TYPES:
            raise ValueError(f"unsupported memory_type: {value!r}")
        return value
    if column == "status":
        _validate_status(value)
        return value
    if column == "sensitivity":
        if value not in SENSITIVITY_LEVELS:
            raise ValueError(f"unsupported sensitivity: {value!r}")
        return value
    if column in JSON_FIELDS:
        return _encode_json_field(column, value)
    if column in {"confidence", "importance"} and value is not None:
        return float(value)
    if column == "superseded_by" and value is not None:
        return _normalize_id(value, column)
    return value


def _encode_json_field(column: str, value: Any) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{column} must be valid JSON") from exc

    if column in LIST_JSON_FIELDS and not isinstance(value, list):
        raise ValueError(f"{column} must be a JSON list")
    if column in DICT_JSON_FIELDS and not isinstance(value, dict):
        raise ValueError(f"{column} must be a JSON object")

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _decode_json_field(column: str, value: Any) -> Any:
    default: Any = {} if column in DICT_JSON_FIELDS else []
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"database field {column} contains invalid JSON") from exc
    return decoded


def _row_to_memory_item(
    row: sqlite3.Row,
    columns: Iterable[str] | None = None,
) -> dict:
    selected_columns = list(columns) if columns is not None else MEMORY_COLUMNS
    item = {column: row[column] for column in selected_columns}
    for column in JSON_FIELDS.intersection(item):
        item[column] = _decode_json_field(column, item[column])
    return item


def _validate_status(status: str) -> None:
    if status not in MEMORY_STATUSES:
        raise ValueError(f"unsupported memory status: {status!r}")


def _require_target_id(operation: dict) -> int:
    if "target_id" not in operation:
        raise ValueError(f"target_id is required for operation {operation.get('operation')!r}")
    return _normalize_id(operation["target_id"], "target_id")


def _extract_id_list(
    operation: dict,
    payload: dict,
    keys: tuple[str, ...],
    required: bool = True,
) -> list[int]:
    raw_ids = None
    for key in keys:
        if key in payload:
            raw_ids = payload[key]
            break
        if key in operation:
            raw_ids = operation[key]
            break

    if raw_ids is None:
        if required:
            raise ValueError(f"one of {keys!r} is required")
        return []

    if isinstance(raw_ids, str):
        try:
            raw_ids = json.loads(raw_ids)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{keys[0]} must be a list of ids") from exc

    if not isinstance(raw_ids, list):
        raise ValueError(f"{keys[0]} must be a list of ids")
    return [_normalize_id(value, keys[0]) for value in raw_ids]


def _merge_unique_ids(existing: list[Any], additions: list[int]) -> list[int]:
    merged: list[int] = []
    for value in [*existing, *additions]:
        normalized = _normalize_id(value, "id")
        if normalized not in merged:
            merged.append(normalized)
    return merged


def _normalize_id(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if normalized <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return normalized


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
