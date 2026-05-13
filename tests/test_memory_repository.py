import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.memory import db
from src.memory.repository import (
    apply_memory_operations,
    create_memory_item,
    get_memory_items_by_ids,
    init_db,
    list_lightweight_memory_items,
    update_memory_item,
)


class MemoryRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "app.db"
        db.set_database_path(self.database_path)
        init_db()

    def tearDown(self):
        db.reset_database_path()
        self.temp_dir.cleanup()

    def test_init_db_creates_memory_items_schema(self):
        with sqlite3.connect(self.database_path) as connection:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(memory_items)").fetchall()
            }

        self.assertIn("summary", columns)
        self.assertIn("content", columns)
        self.assertIn("references_json", columns)
        self.assertIn("conflict_with_json", columns)

    def test_create_update_list_and_get_memory_items(self):
        first = create_memory_item(
            {
                "summary": "User prefers Chinese replies",
                "content": "The user prefers replies in Chinese.",
                "memory_type": "preference",
                "tags_json": ["language", "style"],
                "metadata_json": {"source_turn": "turn_1"},
                "importance": 0.9,
            }
        )
        second = create_memory_item(
            {
                "summary": "User is building a memory demo",
                "content": "The user is implementing Person 1 foundation work.",
                "memory_type": "task",
            }
        )

        self.assertEqual(first["tags_json"], ["language", "style"])
        self.assertEqual(first["metadata_json"], {"source_turn": "turn_1"})

        updated = update_memory_item(
            first["id"],
            {
                "summary": "User strongly prefers Chinese replies",
                "tags_json": ["language", "style", "stable"],
            },
        )
        self.assertEqual(updated["summary"], "User strongly prefers Chinese replies")
        self.assertEqual(updated["tags_json"], ["language", "style", "stable"])

        lightweight = list_lightweight_memory_items()
        self.assertEqual([item["id"] for item in lightweight], [first["id"], second["id"]])
        self.assertNotIn("content", lightweight[0])
        self.assertEqual(lightweight[0]["references_json"], [])

        by_ids = get_memory_items_by_ids([second["id"], first["id"]])
        self.assertEqual([item["id"] for item in by_ids], [second["id"], first["id"]])
        self.assertIn("content", by_ids[0])

    def test_apply_memory_operations_archive_link_conflict_and_merge(self):
        primary = create_memory_item(
            {
                "summary": "Primary task",
                "content": "The primary task is Person 1 foundation.",
                "memory_type": "task",
            }
        )
        related = create_memory_item(
            {
                "summary": "Related task",
                "content": "Related memory to link.",
                "memory_type": "task",
            }
        )
        conflicting = create_memory_item(
            {
                "summary": "Conflicting fact",
                "content": "Conflicting memory to mark.",
                "memory_type": "fact",
            }
        )

        results = apply_memory_operations(
            [
                {
                    "operation": "link",
                    "target_id": primary["id"],
                    "payload": {"reference_ids": [related["id"]]},
                },
                {
                    "operation": "conflict_mark",
                    "target_id": primary["id"],
                    "payload": {"conflict_ids": [conflicting["id"]]},
                },
                {
                    "operation": "archive",
                    "target_id": conflicting["id"],
                    "payload": {},
                },
                {
                    "operation": "merge",
                    "target_id": primary["id"],
                    "payload": {
                        "summary": "Merged primary task",
                        "source_ids": [related["id"]],
                    },
                },
            ]
        )

        self.assertEqual([result["operation"] for result in results], [
            "link",
            "conflict_mark",
            "archive",
            "merge",
        ])

        primary_after, related_after, conflicting_after = get_memory_items_by_ids(
            [primary["id"], related["id"], conflicting["id"]]
        )
        self.assertEqual(primary_after["references_json"], [related["id"]])
        self.assertEqual(primary_after["conflict_with_json"], [conflicting["id"]])
        self.assertEqual(primary_after["summary"], "Merged primary task")
        self.assertEqual(related_after["status"], "deprecated")
        self.assertEqual(related_after["superseded_by"], primary["id"])
        self.assertEqual(conflicting_after["status"], "archived")

    def test_merge_operation_can_create_primary_memory(self):
        source = create_memory_item(
            {
                "summary": "Source memory",
                "content": "This source will be merged.",
                "memory_type": "event",
            }
        )

        result = apply_memory_operations(
            [
                {
                    "operation": "merge",
                    "payload": {
                        "summary": "Created primary memory",
                        "content": "The merge operation created the primary item.",
                        "memory_type": "event",
                        "source_ids": [source["id"]],
                    },
                }
            ]
        )[0]

        self.assertEqual(result["operation"], "merge")
        self.assertEqual(result["summary"], "Created primary memory")

        source_after = get_memory_items_by_ids([source["id"]])[0]
        self.assertEqual(source_after["status"], "deprecated")
        self.assertEqual(source_after["superseded_by"], result["id"])

    def test_rejects_invalid_json_fields(self):
        with self.assertRaises(ValueError):
            create_memory_item(
                {
                    "summary": "Bad JSON",
                    "content": "This should fail.",
                    "tags_json": {"not": "a list"},
                }
            )


if __name__ == "__main__":
    unittest.main()
