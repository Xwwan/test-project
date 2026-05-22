import unittest

from src.services.onboarding_service import (
    FINAL_PAYLOAD_KIND,
    ONBOARDING_SECTION_START,
    OnboardingDependencies,
    handle_onboarding_message,
    start_onboarding,
)


class InMemoryOnboardingStore:
    def __init__(self) -> None:
        self.sessions = {}

    def create_session(self, **kwargs):
        session_id = kwargs.get("session_id") or "onb-1"
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


class ScriptedAgent:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        stage = kwargs["stage"]
        return {
            "stage_complete": True,
            "onboarding_complete": stage == 5,
            "next_question": "",
            "collected_patch": {f"stage_{stage}": kwargs["user_message"]},
            "confidence": 0.9,
            "summary": f"stage {stage} done",
        }


class SlotAwareAgent:
    STAGE_PATCHES = {
        1: {
            "preferred_name": "小熊",
            "age_or_life_stage": "80岁",
            "location": "北京",
        },
        2: {
            "family_members": "儿子",
            "children": "一个儿子",
            "living_situation": "独居",
        },
        3: {
            "daily_routine": "散步",
            "hobbies": "京剧",
            "frequent_activities": "小区转转",
        },
        4: {
            "health_status": "整体还好",
            "discomforts": "无明显不适",
            "sleep_diet": "睡眠一般，胃口好",
        },
        5: {
            "recent_mood_events": "最近平稳",
            "worries": "没有烦心事",
            "preferred_topics": "京剧",
        },
    }

    def __init__(self) -> None:
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        stage = kwargs["stage"]
        return {
            "stage_complete": True,
            "onboarding_complete": stage == 5,
            "next_question": "",
            "collected_patch": dict(self.STAGE_PATCHES[stage]),
            "confidence": 0.9,
            "summary": f"stage {stage} done",
        }


class RecordingQuestionGenerator:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        stage = kwargs["stage"]
        if stage == 1:
            return "你好，我是燕聆。以后我怎么称呼你比较亲切？"
        if stage == 2:
            return "小熊，你在北京住着，那平时家里谁陪你多一点？"
        return f"natural question {stage}"


class OnboardingServiceTest(unittest.TestCase):
    def test_starts_and_completes_five_stage_workflow_without_profile_write(self):
        store = InMemoryOnboardingStore()
        agent = SlotAwareAgent()
        question_generator = RecordingQuestionGenerator()
        conversation_turns = []

        deps = OnboardingDependencies(
            create_session=store.create_session,
            get_session=store.get_session,
            update_session=store.update_session,
            run_step=agent,
            generate_question=question_generator,
            read_model_profile=lambda: "# Model\n你是燕聆。",
            append_turn=lambda conversation_id, turn: conversation_turns.append(
                (conversation_id, dict(turn))
            )
            or turn["turn_id"],
        )

        started = start_onboarding("conv-1", dependencies=deps)

        assert started["stage"] == 1
        assert started["reply"] == "你好，我是燕聆。以后我怎么称呼你比较亲切？"

        latest = started
        for index in range(1, 6):
            latest = handle_onboarding_message(
                started["session_id"],
                f"answer {index}",
                dependencies=deps,
            )

        assert latest["status"] == "completed"
        assert latest["onboarding_complete"] is True
        assert latest["profile_updated"] is False
        assert latest["collected"]["preferred_topics"] == "京剧"
        assert latest["final_payload"]["kind"] == FINAL_PAYLOAD_KIND
        assert latest["final_payload"]["status"] == "ready_for_downstream_agent"
        assert ONBOARDING_SECTION_START in latest["final_payload"]["suggested_user_profile_section"]
        assert agent.calls[0]["model_profile"] == "# Model\n你是燕聆。"
        assert question_generator.calls[1]["stage"] == 2
        assert question_generator.calls[1]["transition_from_stage"]["name"] == "认识你"
        assert question_generator.calls[1]["collected"]["location"] == "北京"
        assert conversation_turns
        assert conversation_turns[0][0] == "conv-1"
        assert conversation_turns[0][1]["metadata"]["turn_kind"] == "onboarding"

    def test_default_does_not_append_to_conversation_history(self):
        store = InMemoryOnboardingStore()
        deps = OnboardingDependencies(
            create_session=store.create_session,
            get_session=store.get_session,
            update_session=store.update_session,
            run_step=ScriptedAgent(),
            generate_question=lambda **kwargs: "自然开场",
            read_model_profile=lambda: "",
        )

        started = start_onboarding("conv-1", dependencies=deps)

        assert started["conversation_id"] == "conv-1"
        assert store.sessions[started["session_id"]]["turns"]

    def test_model_cannot_complete_stage_when_required_slots_are_missing(self):
        store = InMemoryOnboardingStore()

        def premature_agent(**kwargs):
            return {
                "stage_complete": True,
                "next_question": "我们接着聊家里吧。",
                "collected_patch": {"preferred_name": "小熊"},
                "confidence": 0.9,
                "summary": "用户叫小熊",
            }

        deps = OnboardingDependencies(
            create_session=store.create_session,
            get_session=store.get_session,
            update_session=store.update_session,
            run_step=premature_agent,
            generate_question=lambda **kwargs: "自然开场",
            read_model_profile=lambda: "",
        )

        started = start_onboarding(dependencies=deps)
        latest = handle_onboarding_message(
            started["session_id"],
            "我叫小熊",
            dependencies=deps,
        )

        assert latest["stage"] == 1
        assert latest["status"] == "active"
        assert latest["onboarding_complete"] is False
        assert latest["collected"]["preferred_name"] == "小熊"
        assert latest["missing_required_slots"] == ["age_or_life_stage", "location"]
        assert "多大年纪" in latest["reply"]

    def test_stage_transition_uses_generated_question_instead_of_static_first_question(self):
        store = InMemoryOnboardingStore()

        def stage_one_agent(**kwargs):
            return {
                "stage_complete": True,
                "next_question": "",
                "collected_patch": {
                    "preferred_name": "小熊",
                    "age_or_life_stage": "80岁",
                    "location": "北京",
                },
                "confidence": 0.9,
                "summary": "用户叫小熊，80岁，住北京",
            }

        question_generator = RecordingQuestionGenerator()
        deps = OnboardingDependencies(
            create_session=store.create_session,
            get_session=store.get_session,
            update_session=store.update_session,
            run_step=stage_one_agent,
            generate_question=question_generator,
            read_model_profile=lambda: "",
        )

        started = start_onboarding(dependencies=deps)
        latest = handle_onboarding_message(
            started["session_id"],
            "我叫小熊，80岁，住北京",
            dependencies=deps,
        )

        assert latest["stage"] == 2
        assert latest["reply"] == "小熊，你在北京住着，那平时家里谁陪你多一点？"
        assert latest["reply"] != "我也想了解一下你的家里情况，平时家里都有谁陪着你？"


if __name__ == "__main__":
    unittest.main()
