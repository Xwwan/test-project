import json
import unittest
from unittest.mock import patch

from src.agents.onboarding_agent import (
    DEEPSEEK_BASE_URL,
    DEFAULT_MODEL,
    build_deepseek_client,
    generate_onboarding_question,
    normalize_onboarding_question_result,
    normalize_onboarding_agent_result,
    run_onboarding_step,
)
from src.agents.onboarding_streaming_agent import generate_onboarding_reply_stream
from src.models import OpenAIChatCompletionsClient
from src.models import ChatResponse


class FakeModelClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return ChatResponse(content=json.dumps(self.payload), raw={})


class FakeStreamingModelClient:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.calls = []

    def chat_stream(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        yield from self.chunks


class OnboardingAgentTest(unittest.TestCase):
    def test_normalize_onboarding_agent_result(self):
        result = normalize_onboarding_agent_result(
            {
                "stage_complete": True,
                "next_question": "接下来聊聊家里吧。",
                "collected_patch": {"preferred_name": "王叔"},
                "confidence": 2,
            }
        )

        assert result["stage_complete"] is True
        assert result["onboarding_complete"] is False
        assert result["next_question"] == "接下来聊聊家里吧。"
        assert result["collected_patch"] == {"preferred_name": "王叔"}
        assert result["confidence"] == 1.0

    def test_normalize_onboarding_question_result(self):
        result = normalize_onboarding_question_result(
            {"next_question": "你在北京住了挺久吧，平时家里谁陪你多一点？"}
        )

        assert result == "你在北京住了挺久吧，平时家里谁陪你多一点？"

    def test_run_onboarding_step_calls_model_with_json_format(self):
        client = FakeModelClient(
            {
                "stage_complete": False,
                "next_question": "你住在哪个城市呀？",
                "collected_patch": {"preferred_name": "王叔"},
            }
        )

        result = run_onboarding_step(
            stage=1,
            stage_goal={"name": "认识你", "goal": "了解称呼"},
            collected={},
            turns=[],
            user_message="叫我王叔",
            model_profile="你叫燕聆，回复要简短。",
            model_client=client,
        )

        assert result["next_question"] == "你住在哪个城市呀？"
        assert client.calls
        assert client.calls[0]["kwargs"]["response_format"] == {"type": "json_object"}
        system_prompt = client.calls[0]["messages"][0].content
        assert "Model.md" in system_prompt
        assert "你叫燕聆，回复要简短。" in system_prompt

    def test_generate_onboarding_question_uses_collected_context(self):
        client = FakeModelClient(
            {
                "next_question": "小熊，你在北京住着，那平时家里谁陪你多一点？",
                "reason": "承接所在地后进入家庭情况",
            }
        )

        question = generate_onboarding_question(
            stage=2,
            stage_goal={
                "name": "家庭情况",
                "goal": "了解家里有谁",
                "first_question": "我也想了解一下你的家里情况，平时家里都有谁陪着你？",
            },
            collected={"preferred_name": "小熊", "location": "北京"},
            turns=[{"role": "user", "content": "我住北京"}],
            transition_from_stage={"name": "认识你"},
            model_profile="你叫燕聆。",
            model_client=client,
        )

        assert question == "小熊，你在北京住着，那平时家里谁陪你多一点？"
        user_prompt = client.calls[0]["messages"][1].content
        assert "Question Generation Input" in user_prompt
        assert "北京" in user_prompt

    def test_generate_onboarding_reply_stream_yields_model_deltas(self):
        client = FakeStreamingModelClient(["王叔，", "你住在哪儿？"])

        chunks = list(
            generate_onboarding_reply_stream(
                {
                    "model_profile": "你叫燕聆。",
                    "turns": [{"role": "user", "content": "叫我王叔"}],
                    "user_message": "叫我王叔",
                    "state": {"stage": {"name": "认识你"}},
                    "control_result": {
                        "collected_patch": {"preferred_name": "王叔"},
                    },
                    "reply_target": {
                        "kind": "followup",
                        "target_text": "你住在哪儿？",
                    },
                },
                model_client=client,
            )
        )

        assert chunks == ["王叔，", "你住在哪儿？"]
        assert client.calls
        prompt = client.calls[0]["messages"][1].content
        assert "Reply Target" in prompt
        assert "你住在哪儿？" in prompt

    def test_build_deepseek_client_uses_official_api(self):
        with patch("src.agents.onboarding_agent.get_config_value", return_value="dk"):
            client = build_deepseek_client()

        assert isinstance(client, OpenAIChatCompletionsClient)
        assert client.api_key == "dk"
        assert client.base_url == DEEPSEEK_BASE_URL
        assert client.default_model == DEFAULT_MODEL
        assert client.provider_name == "deepseek_official"


if __name__ == "__main__":
    unittest.main()
