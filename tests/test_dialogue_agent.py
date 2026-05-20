import json
import unittest

from src.agents.dialogue_agent import generate_followup_reply, generate_initial_reply
from src.models import ChatResponse


class JsonClient:
    def __init__(self, payload):
        self.payload = payload
        self.messages = None
        self.kwargs = None

    def chat(self, messages, **kwargs):
        self.messages = messages
        self.kwargs = kwargs
        if isinstance(self.payload, str):
            return ChatResponse(content=self.payload, raw={"text": self.payload})
        return ChatResponse(content=json.dumps(self.payload), raw=self.payload)


class DialogueAgentTest(unittest.TestCase):
    def test_generate_initial_reply_returns_request_id_and_model_text(self):
        client = JsonClient("你好，我会继续帮你推进这个框架。")

        result = generate_initial_reply(
            {
                "request_id": "req_initial",
                "turn_id": "turn_1",
                "model_profile": "Model profile text",
                "user_profile": "User profile text",
                "compact_history": "Compact memory text",
                "recent_history": [
                    {
                        "role": "user",
                        "content": "上一轮内容",
                        "created_at": "2026-05-12T12:00:00Z",
                    }
                ],
                "current_query": "继续实现 Person 2",
            },
            model_client=client,
        )

        self.assertEqual(
            result,
            {"request_id": "req_initial", "reply": "你好，我会继续帮你推进这个框架。"},
        )
        prompt_text = "\n".join(message.content for message in client.messages)
        expected_order = [
            "Model.md",
            "User.md",
            "Compact Memory",
            "Recent History",
            "Current User Query",
        ]
        positions = [prompt_text.index(section) for section in expected_order]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("Retrieved Events", client.messages[1].content)

    def test_generate_followup_reply_returns_no_followup_without_retrieved_items(self):
        client = JsonClient(
            {
                "decision": "followup",
                "followup_type": "supplement",
                "reply": "should not be used",
            }
        )

        result = generate_followup_reply(
            {
                "request_id": "req_none",
                "turn_id": "turn_1",
                "initial_reply": "初始回复",
                "current_conversation_state": "same topic",
                "original_user_query": "问题",
                "model_profile": "",
                "user_profile": "",
                "compact_history": "",
                "recent_history": [],
                "retrieved_items": [],
            },
            model_client=client,
        )

        self.assertEqual(
            result,
            {
                "request_id": "req_none",
                "decision": "no_followup",
                "followup_type": "none",
                "reply": "",
            },
        )
        self.assertIsNone(client.messages)

    def test_weakly_related_retrieval_can_return_no_followup(self):
        client = JsonClient(
            {
                "decision": "no_followup",
                "followup_type": "none",
                "reply": "ignored",
            }
        )

        result = generate_followup_reply(_followup_input(), model_client=client)

        self.assertEqual(result["decision"], "no_followup")
        self.assertEqual(result["followup_type"], "none")
        self.assertEqual(result["reply"], "")

    def test_correction_decision_is_preserved(self):
        client = JsonClient(
            {
                "decision": "followup",
                "followup_type": "correction",
                "reply": "需要修正：你之前的框架目录其实是 src/agents。",
            }
        )

        result = generate_followup_reply(_followup_input(), model_client=client)

        self.assertEqual(result["request_id"], "req_follow")
        self.assertEqual(result["decision"], "followup")
        self.assertEqual(result["followup_type"], "correction")
        self.assertIn("需要修正", result["reply"])
        self.assertEqual(client.kwargs["response_format"], {"type": "json_object"})
        prompt_text = "\n".join(message.content for message in client.messages)
        self.assertIn("Retrieved Events", prompt_text)
        self.assertIn("高风险", prompt_text)

    def test_supplement_decision_is_preserved(self):
        client = JsonClient(
            {
                "decision": "followup",
                "followup_type": "supplement",
                "reply": "补充一点：用户之前还要求回复使用中文。",
            }
        )

        result = generate_followup_reply(_followup_input(), model_client=client)

        self.assertEqual(result["decision"], "followup")
        self.assertEqual(result["followup_type"], "supplement")
        self.assertIn("补充", result["reply"])

    def test_high_risk_followup_gets_conservative_wording(self):
        client = JsonClient(
            {
                "decision": "followup",
                "followup_type": "supplement",
                "reply": "你上次用了这个药，所以这次也继续用。",
            }
        )

        input_data = _followup_input()
        input_data["original_user_query"] = "我现在能继续吃这个药吗？"
        input_data["retrieved_items"][0]["sensitivity"] = "high_risk"

        result = generate_followup_reply(input_data, model_client=client)

        self.assertEqual(result["decision"], "followup")
        self.assertIn("不确定", result["reply"])
        self.assertIn("专业", result["reply"])

    def test_high_risk_followup_logs_trigger_reason(self):
        client = JsonClient(
            {
                "decision": "followup",
                "followup_type": "supplement",
                "reply": "这个合同条款要按之前的信息处理。",
            }
        )

        input_data = _followup_input()
        input_data["retrieved_items"][0]["memory_type"] = "legal"

        with self.assertLogs("chat-service.agents.dialogue", level="INFO") as logs:
            result = generate_followup_reply(input_data, model_client=client)

        self.assertEqual(result["decision"], "followup")
        log_text = "\n".join(logs.output)
        self.assertIn("source=retrieved_item_memory_type", log_text)
        self.assertIn("item_id=1", log_text)
        self.assertIn("memory_type=legal", log_text)


def _followup_input():
    return {
        "request_id": "req_follow",
        "turn_id": "turn_1",
        "initial_reply": "可以按常规方式继续实现。",
        "current_conversation_state": "用户仍在讨论 Person 2 实现。",
        "original_user_query": "继续实现 Person 2",
        "model_profile": "Model profile",
        "user_profile": "User profile",
        "compact_history": "Compact memory",
        "recent_history": [],
        "retrieved_items": [
            {
                "id": 1,
                "summary": "用户正在实现 Person 2",
                "content": "Person 2 负责 Dialogue Agent 与 Memory Retrieval Workflow。",
                "memory_type": "task",
                "references_json": [],
                "tags_json": ["person-2"],
                "metadata_json": {},
                "confidence": 0.9,
                "importance": 0.8,
                "sensitivity": "normal",
                "status": "active",
                "created_at": "2026-05-12T00:00:00Z",
                "updated_at": "2026-05-12T00:00:00Z",
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
