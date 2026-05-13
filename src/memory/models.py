"""Memory Store schema constants and lightweight validation rules."""

from __future__ import annotations


MEMORY_TYPES = {
    "fact",
    "preference",
    "task",
    "event",
    "constraint",
    "profile_update",
}

MEMORY_STATUSES = {
    "active",
    "archived",
    "deprecated",
    "deleted",
}

SENSITIVITY_LEVELS = {
    "normal",
    "sensitive",
    "high",
    "high_risk",
    "private",
}

JSON_FIELDS = {
    "references_json",
    "tags_json",
    "metadata_json",
    "conflict_with_json",
}

LIST_JSON_FIELDS = {
    "references_json",
    "tags_json",
    "conflict_with_json",
}

DICT_JSON_FIELDS = {
    "metadata_json",
}

MEMORY_COLUMNS = [
    "id",
    "summary",
    "content",
    "memory_type",
    "references_json",
    "tags_json",
    "metadata_json",
    "source",
    "confidence",
    "importance",
    "sensitivity",
    "status",
    "superseded_by",
    "conflict_with_json",
    "created_at",
    "updated_at",
    "valid_from",
    "valid_until",
    "last_accessed_at",
    "last_verified_at",
]

INSERTABLE_COLUMNS = [
    column for column in MEMORY_COLUMNS if column != "id"
]

UPDATABLE_COLUMNS = [
    column for column in MEMORY_COLUMNS if column not in {"id", "created_at"}
]

LIGHTWEIGHT_COLUMNS = [
    "id",
    "summary",
    "tags_json",
    "memory_type",
    "references_json",
    "created_at",
    "importance",
]

DEFAULT_MEMORY_VALUES = {
    "memory_type": "event",
    "references_json": [],
    "tags_json": [],
    "metadata_json": {},
    "source": "conversation",
    "confidence": 0.8,
    "importance": 0.5,
    "sensitivity": "normal",
    "status": "active",
    "superseded_by": None,
    "conflict_with_json": [],
    "valid_from": None,
    "valid_until": None,
    "last_accessed_at": None,
    "last_verified_at": None,
}
