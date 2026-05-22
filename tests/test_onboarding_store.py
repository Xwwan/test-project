import tempfile
import unittest
from pathlib import Path

from src.memory import db
from src.onboarding import store


class OnboardingStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db.set_database_path(Path(self.temp_dir.name) / "app.db")
        store.init_db()

    def tearDown(self):
        db.reset_database_path()
        self.temp_dir.cleanup()

    def test_create_get_and_update_session(self):
        created = store.create_session(
            conversation_id="conv-1",
            session_id="onb-test",
            collected={"preferred_name": "李阿姨"},
            turns=[{"role": "assistant", "content": "你好"}],
            final_payload={"kind": "draft"},
        )

        assert created["session_id"] == "onb-test"
        assert created["conversation_id"] == "conv-1"
        assert created["status"] == "active"
        assert created["stage"] == 1
        assert created["collected"]["preferred_name"] == "李阿姨"
        assert created["final_payload"] == {"kind": "draft"}

        updated = store.update_session(
            "onb-test",
            stage=2,
            collected={"preferred_name": "李阿姨", "family_members": "老伴"},
            turns=[*created["turns"], {"role": "user", "content": "叫我李阿姨"}],
            final_payload={"kind": "complete", "collected": {"family_members": "老伴"}},
        )

        assert updated["stage"] == 2
        assert updated["collected"]["family_members"] == "老伴"
        assert len(updated["turns"]) == 2
        assert updated["final_payload"]["kind"] == "complete"

    def test_missing_session_raises_not_found(self):
        with self.assertRaises(store.OnboardingSessionNotFoundError):
            store.get_session("missing")

    def test_migration_adds_final_payload_column_to_existing_table(self):
        with db.transaction() as connection:
            connection.execute("DROP TABLE onboarding_sessions")
            connection.execute(
                """
                CREATE TABLE onboarding_sessions (
                    session_id TEXT PRIMARY KEY,
                    conversation_id TEXT,
                    status TEXT NOT NULL,
                    stage INTEGER NOT NULL,
                    collected_json TEXT DEFAULT '{}',
                    turns_json TEXT DEFAULT '[]',
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    completed_at DATETIME
                )
                """
            )

        store.init_db()
        created = store.create_session(session_id="old-schema")
        updated = store.update_session(
            created["session_id"],
            final_payload={"kind": "complete"},
        )

        assert updated["final_payload"] == {"kind": "complete"}


if __name__ == "__main__":
    unittest.main()
