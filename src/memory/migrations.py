"""Idempotent SQLite migrations for Person 1 local state."""

from __future__ import annotations

import sqlite3

from . import db


MEMORY_ITEMS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS memory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    memory_type TEXT DEFAULT 'event',
    references_json TEXT DEFAULT '[]',
    tags_json TEXT DEFAULT '[]',
    metadata_json TEXT DEFAULT '{}',
    source TEXT DEFAULT 'conversation',
    confidence REAL DEFAULT 0.8,
    importance REAL DEFAULT 0.5,
    sensitivity TEXT DEFAULT 'normal',
    status TEXT DEFAULT 'active',
    superseded_by INTEGER,
    conflict_with_json TEXT DEFAULT '[]',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    valid_from DATETIME,
    valid_until DATETIME,
    last_accessed_at DATETIME,
    last_verified_at DATETIME
);
"""

CONVERSATION_TURNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS conversation_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    metadata_json TEXT DEFAULT '{}',
    UNIQUE(conversation_id, turn_id)
);
"""

COMPACT_HISTORIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS compact_histories (
    conversation_id TEXT PRIMARY KEY,
    compact TEXT NOT NULL DEFAULT '',
    updated_at DATETIME NOT NULL
);
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_memory_items_status ON memory_items(status);",
    "CREATE INDEX IF NOT EXISTS idx_memory_items_type ON memory_items(memory_type);",
    "CREATE INDEX IF NOT EXISTS idx_conversation_turns_lookup ON conversation_turns(conversation_id, id);",
]


def run_migrations(connection: sqlite3.Connection) -> None:
    """Create all local state tables if they do not already exist."""

    connection.execute(MEMORY_ITEMS_TABLE_SQL)
    connection.execute(CONVERSATION_TURNS_TABLE_SQL)
    connection.execute(COMPACT_HISTORIES_TABLE_SQL)
    for statement in INDEX_SQL:
        connection.execute(statement)


def init_db() -> None:
    """Initialize the configured SQLite database."""

    with db.transaction() as connection:
        run_migrations(connection)
