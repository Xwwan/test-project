import tempfile
import unittest
from pathlib import Path

from src.interaction import store as interaction_store
from src.memory import db
from src.services.interaction_service import (
    create_interaction_session,
    iter_text_interaction_events,
)
from src.services.onboarding_service import OnboardingDependencies


class InMemoryOnboardingStore:
    def __init__(self) -> None:
        self.sessions = {}

    def create_session(self, **kwargs):
        session_id = kwargs.get("session_id") or f"onb-{len(self.sessions) + 1}"
        session = {
            "session_id": session_id,
            "conversation_id": kwargs.get("conversation_id") or "",
            "status": "active",
            "stage": kwargs.get("stage", 1),
            "collected": dict(kwargs.get("collected") or {}),
            "turns": list(kwargs.get("turns") or []),
            "final_payload": dict(kwargs.get("final_payload") or {}),
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "completed_at": None,
        }
        self.sessions[session_id] = session
        return dict(session)

    def get_session(self, session_id):
        return dict(self.sessions[session_id])

    def update_session(self, session_id, **kwargs):
        session = dict(self.sessions[session_id])
        for key in ("status", "stage", "collected", "turns", "final_payload"):
            if key in kwargs and kwargs[key] is not None:
                session[key] = kwargs[key]
        self.sessions[session_id] = session
        return dict(session)


class RecordingReplyStreamer:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.calls = []

    def __call__(self, input_data: dict, **kwargs):
        self.calls.append(input_data)
        yield from self.chunks


class InteractionServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db.set_database_path(Path(self.temp_dir.name) / "app.db")
        interaction_store.init_db()

    def tearDown(self):
        db.reset_database_path()
        self.temp_dir.cleanup()

    def test_onboarding_text_interaction_uses_streaming_reply_and_updates_state(self):
        onboarding_store = InMemoryOnboardingStore()
        streamer = RecordingReplyStreamer(["王叔，", "你住在哪儿？"])

        def control_agent(**kwargs):
            return {
                "stage_complete": False,
                "onboarding_complete": False,
                "next_question": "你住在哪儿？",
                "collected_patch": {"preferred_name": "王叔"},
                "confidence": 0.9,
                "summary": "用户希望被称为王叔",
            }

        deps = OnboardingDependencies(
            create_session=onboarding_store.create_session,
            get_session=onboarding_store.get_session,
            update_session=onboarding_store.update_session,
            run_step=control_agent,
            generate_question=lambda **kwargs: "你好，我平时怎么称呼你？",
            generate_reply_stream=streamer,
            read_model_profile=lambda: "# Model\n你是燕聆。",
        )
        session = create_interaction_session(
            workflow="onboarding",
            conversation_id="conv-1",
            onboarding_dependencies=deps,
        )

        events = list(
            iter_text_interaction_events(
                interaction_session_id=session["interaction_session_id"],
                workflow="onboarding",
                message="叫我王叔",
                onboarding_dependencies=deps,
            )
        )

        assert [event["event"] for event in events] == [
            "meta",
            "delta",
            "delta",
            "state_delta",
            "done",
        ]
        assert events[0]["data"]["workflow"] == "onboarding"
        assert events[0]["data"]["onboarding_session_id"] == session["onboarding_session_id"]
        assert events[1]["data"]["delta"] == "王叔，"
        assert events[2]["data"]["delta"] == "你住在哪儿？"
        assert events[-1]["data"]["reply"] == "王叔，你住在哪儿？"
        assert events[-1]["data"]["stage"] == 1
        assert events[-1]["data"]["status"] == "active"
        assert events[-1]["data"]["collected"]["preferred_name"] == "王叔"
        assert "retrieval_status" not in events[-1]["data"]
        assert "request_id" not in events[-1]["data"]

        assert streamer.calls
        assert streamer.calls[0]["reply_target"]["target_text"] == "你住在哪儿？"
        assert streamer.calls[0]["control_result"]["collected_patch"] == {
            "preferred_name": "王叔"
        }

        run = interaction_store.get_run(events[0]["data"]["run_id"])
        assert run["workflow"] == "onboarding"
        assert run["status"] == "completed"
        assert run["reply"] == "王叔，你住在哪儿？"
        assert run["stage"] == 1


if __name__ == "__main__":
    unittest.main()
