import tempfile
import unittest
from pathlib import Path

from src.interaction import store
from src.memory import db


class InteractionStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db.set_database_path(Path(self.temp_dir.name) / "app.db")
        store.init_db()

    def tearDown(self):
        db.reset_database_path()
        self.temp_dir.cleanup()

    def test_create_get_and_update_session(self):
        created = store.create_session(
            workflow="chat",
            conversation_id="conv-1",
            input_mode="text",
            interaction_session_id="isess-test",
        )

        assert created["interaction_session_id"] == "isess-test"
        assert created["workflow"] == "chat"
        assert created["conversation_id"] == "conv-1"
        assert created["status"] == "active"
        assert created["input_mode"] == "text"

        updated = store.update_session("isess-test", status="completed")

        assert updated["status"] == "completed"

    def test_create_get_and_update_run(self):
        session = store.create_session(
            workflow="chat",
            conversation_id="conv-1",
        )

        run = store.create_run(
            interaction_session_id=session["interaction_session_id"],
            workflow="chat",
            input_mode="text",
            transcript="你好",
            run_id="irun-test",
        )

        assert run["run_id"] == "irun-test"
        assert run["interaction_session_id"] == session["interaction_session_id"]
        assert run["status"] == "running"
        assert run["transcript"] == "你好"

        updated = store.update_run(
            "irun-test",
            status="completed",
            reply="你好呀",
            request_id="req_1",
            playback_key="chat-tts-irun-test",
            playback_status="done",
        )

        assert updated["status"] == "completed"
        assert updated["reply"] == "你好呀"
        assert updated["request_id"] == "req_1"
        assert updated["playback_key"] == "chat-tts-irun-test"
        assert updated["playback_status"] == "done"
        assert updated["completed_at"]

    def test_list_runs_for_session_returns_recent_runs(self):
        first_session = store.create_session(workflow="chat", conversation_id="conv-1")
        second_session = store.create_session(workflow="onboarding", conversation_id="conv-2")
        first = store.create_run(
            interaction_session_id=first_session["interaction_session_id"],
            workflow="chat",
            transcript="first",
        )
        second = store.create_run(
            interaction_session_id=first_session["interaction_session_id"],
            workflow="chat",
            transcript="second",
        )
        store.create_run(
            interaction_session_id=second_session["interaction_session_id"],
            workflow="onboarding",
            transcript="other",
        )

        runs = store.list_runs_for_session(first_session["interaction_session_id"])

        assert [run["run_id"] for run in runs] == [
            first["run_id"],
            second["run_id"],
        ]
        assert [run["transcript"] for run in runs] == ["first", "second"]

    def test_missing_session_raises_not_found(self):
        with self.assertRaises(store.InteractionSessionNotFoundError):
            store.get_session("missing")

    def test_rejects_unknown_workflow(self):
        with self.assertRaises(ValueError):
            store.create_session(workflow="profile")


if __name__ == "__main__":
    unittest.main()
