import tempfile
import unittest
from pathlib import Path

from src.conversation.compact_store import (
    get_compact_history,
    update_compact_history,
)
from src.conversation.history_store import append_turn, get_recent_history
from src.memory import db
from src.memory.repository import init_db


class ConversationStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "app.db"
        db.set_database_path(self.database_path)
        init_db()

    def tearDown(self):
        db.reset_database_path()
        self.temp_dir.cleanup()

    def test_append_turn_and_get_recent_history(self):
        append_turn(
            "conv_1",
            {
                "turn_id": "turn_1",
                "role": "user",
                "content": "hello",
                "created_at": "2026-05-12T11:30:00Z",
            },
        )
        append_turn(
            "conv_1",
            {
                "turn_id": "turn_2",
                "role": "assistant",
                "content": "hi",
                "created_at": "2026-05-12T11:31:00Z",
            },
        )
        append_turn(
            "conv_1",
            {
                "turn_id": "turn_3",
                "role": "user",
                "content": "continue",
                "created_at": "2026-05-12T11:32:00Z",
            },
        )

        recent = get_recent_history("conv_1", limit=2)

        self.assertEqual([turn["turn_id"] for turn in recent], ["turn_2", "turn_3"])
        self.assertEqual([turn["role"] for turn in recent], ["assistant", "user"])

    def test_compact_history_can_be_updated_and_read(self):
        self.assertEqual(get_compact_history("conv_1"), "")

        update_compact_history("conv_1", "The user is testing local state.")
        self.assertEqual(
            get_compact_history("conv_1"),
            "The user is testing local state.",
        )

        update_compact_history("conv_1", "Updated compact history.")
        self.assertEqual(get_compact_history("conv_1"), "Updated compact history.")

    def test_append_turn_rejects_invalid_role(self):
        with self.assertRaises(ValueError):
            append_turn(
                "conv_1",
                {
                    "turn_id": "turn_bad",
                    "role": "developer",
                    "content": "bad role",
                    "created_at": "2026-05-12T11:30:00Z",
                },
            )


if __name__ == "__main__":
    unittest.main()
