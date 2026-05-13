import json
import unittest

from src.agents.memory_retrieval_workflow import retrieve_relevant_memory_ids
from src.models import ChatResponse


class JsonClient:
    def __init__(self, payload):
        self.payload = payload
        self.messages = None
        self.kwargs = None

    def chat(self, messages, **kwargs):
        self.messages = messages
        self.kwargs = kwargs
        return ChatResponse(content=json.dumps(self.payload), raw=self.payload)


class MemoryRetrievalWorkflowTest(unittest.TestCase):
    def test_empty_memory_list_returns_no_ids_without_model_call(self):
        client = JsonClient({"selected_memory_ids": [1]})

        result = retrieve_relevant_memory_ids(
            request_id="req_empty",
            current_query="帮我继续写项目",
            compact_history="",
            recent_history=[],
            user_profile="",
            lightweight_memory_items=[],
            model_client=client,
        )

        self.assertEqual(
            result,
            {
                "request_id": "req_empty",
                "selected_memory_ids": [],
                "retrieval_reason": "no lightweight memory items",
                "needs_full_load": False,
                "strategy": "llm_direct_judgement",
            },
        )
        self.assertIsNone(client.messages)

    def test_model_selected_ids_are_filtered_to_available_memory_ids(self):
        client = JsonClient(
            {
                "selected_memory_ids": [2, 999, "1", "bad"],
                "retrieval_reason": "项目相关记忆",
                "needs_full_load": True,
            }
        )

        result = retrieve_relevant_memory_ids(
            request_id="req_1",
            current_query="继续我之前的大模型聊天框架",
            compact_history="用户正在做一个聊天框架。",
            recent_history=[
                {
                    "role": "user",
                    "content": "昨天我在做 memory retrieval",
                    "created_at": "2026-05-12T10:00:00Z",
                }
            ],
            user_profile="用户使用中文。",
            lightweight_memory_items=[
                {
                    "id": 1,
                    "summary": "用户偏好中文回答",
                    "tags_json": ["language"],
                    "memory_type": "preference",
                    "references_json": [],
                    "created_at": "2026-05-01T00:00:00Z",
                    "importance": 0.7,
                },
                {
                    "id": 2,
                    "summary": "用户正在实现大模型聊天框架",
                    "tags_json": ["project", "llm"],
                    "memory_type": "task",
                    "references_json": [],
                    "created_at": "2026-05-12T00:00:00Z",
                    "importance": 0.9,
                },
            ],
            model_client=client,
        )

        self.assertEqual(result["request_id"], "req_1")
        self.assertEqual(result["selected_memory_ids"], [2, 1])
        self.assertEqual(result["retrieval_reason"], "项目相关记忆")
        self.assertTrue(result["needs_full_load"])
        self.assertEqual(result["strategy"], "llm_direct_judgement")
        self.assertEqual(client.kwargs["response_format"], {"type": "json_object"})
        prompt_text = "\n".join(message.content for message in client.messages)
        self.assertIn("Current User Query", prompt_text)
        self.assertIn("Lightweight Memory Items", prompt_text)
        self.assertIn("用户正在实现大模型聊天框架", prompt_text)

    def test_invalid_model_json_falls_back_to_empty_selection(self):
        class BadClient:
            def chat(self, messages, **kwargs):
                return ChatResponse(content="not json", raw={})

        result = retrieve_relevant_memory_ids(
            request_id="req_bad",
            current_query="hello",
            compact_history="",
            recent_history=[],
            user_profile="",
            lightweight_memory_items=[
                {
                    "id": 10,
                    "summary": "unrelated",
                    "tags_json": [],
                    "memory_type": "event",
                    "references_json": [],
                    "created_at": "2026-01-01T00:00:00Z",
                    "importance": 0.1,
                }
            ],
            model_client=BadClient(),
        )

        self.assertEqual(result["selected_memory_ids"], [])
        self.assertFalse(result["needs_full_load"])
        self.assertIn("invalid model JSON", result["retrieval_reason"])


if __name__ == "__main__":
    unittest.main()
