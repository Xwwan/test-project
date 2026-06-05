import json
import re
import unittest

from src.agents.dialogue_agent import (
    generate_followup_reply,
    generate_initial_reply,
    generate_initial_reply_stream,
)
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


class StreamClient:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.messages = None
        self.kwargs = None

    def chat_stream(self, messages, **kwargs):
        self.messages = messages
        self.kwargs = kwargs
        yield from self.chunks


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
            {
                "request_id": "req_initial",
                "reply": "[emo:idle][act:😁]你好，我会继续帮你推进这个框架。",
            },
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

    def test_initial_reply_is_normalized_to_tags_and_two_sentences(self):
        client = JsonClient("听起来你小时候很爱玩。那会儿一定有不少有意思的事。后来你还常想起这些吗？")

        result = generate_initial_reply(
            {
                "request_id": "req_initial",
                "model_profile": "",
                "user_profile": "",
                "compact_history": "",
                "recent_history": [],
                "current_query": "我小时候可贪玩了",
            },
            model_client=client,
        )

        self.assertRegex(result["reply"], r"^\[emo:[^\]]+\]\[act:[^\]]+\]")
        body = re.sub(r"^\[emo:[^\]]+\]\[act:[^\]]+\]", "", result["reply"])
        sentence_count = len([part for part in re.split(r"[。！？!?]", body) if part.strip()])
        self.assertLessEqual(sentence_count, 2)
        self.assertNotIn("后来你还常想起这些吗", result["reply"])

    def test_initial_reply_preserves_existing_leading_tags(self):
        client = JsonClient("[emo:sad][act:😭]你这么说，听起来心里有点沉。先别急，我在这儿听你慢慢说。")

        result = generate_initial_reply(
            {
                "request_id": "req_tagged",
                "model_profile": "",
                "user_profile": "",
                "compact_history": "",
                "recent_history": [],
                "current_query": "今天心里空落落的",
            },
            model_client=client,
        )

        self.assertTrue(result["reply"].startswith("[emo:sad][act:😭]"))

    def test_initial_reply_stream_adds_tags_and_limits_to_two_sentences(self):
        client = StreamClient(["听起来你小时候很爱玩。", "那会儿一定有不少有意思的事。", "后来还想吗？"])

        chunks = list(
            generate_initial_reply_stream(
                {
                    "request_id": "req_stream",
                    "model_profile": "",
                    "user_profile": "",
                    "compact_history": "",
                    "recent_history": [],
                    "current_query": "我小时候可贪玩了",
                },
                model_client=client,
            )
        )

        reply = "".join(chunks)
        self.assertTrue(reply.startswith("[emo:idle][act:😁]"))
        self.assertIn("听起来你小时候很爱玩。", reply)
        self.assertIn("那会儿一定有不少有意思的事。", reply)
        self.assertNotIn("后来还想吗", reply)

    def test_initial_reply_stream_preserves_split_leading_tags(self):
        client = StreamClient(["[emo:", "sad][act:", "😭]心里有点沉。", "我在这儿听你说。"])

        chunks = list(
            generate_initial_reply_stream(
                {
                    "request_id": "req_stream_tagged",
                    "model_profile": "",
                    "user_profile": "",
                    "compact_history": "",
                    "recent_history": [],
                    "current_query": "今天心里空落落的",
                },
                model_client=client,
            )
        )

        reply = "".join(chunks)
        self.assertTrue(reply.startswith("[emo:sad][act:😭]"))
        self.assertIn("心里有点沉。", reply)

    def test_initial_prompt_tells_agent_a_not_to_invent_specific_facts(self):
        client = JsonClient("[emo:excited][act:😁]听起来你小时候很有活力呀。")

        generate_initial_reply(
            {
                "request_id": "req_prompt",
                "model_profile": "",
                "user_profile": "",
                "compact_history": "",
                "recent_history": [],
                "current_query": "我小时候可贪玩了",
            },
            model_client=client,
        )

        prompt_text = "\n".join(message.content for message in client.messages)
        self.assertIn("Agent A", prompt_text)
        self.assertIn("1-2 句", prompt_text)
        self.assertIn("[emo:key][act:key]", prompt_text)
        self.assertIn("不要编造", prompt_text)
        self.assertIn("给后续记忆增强续接留空间", prompt_text)

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
        self.assertIn("像同一轮回答的自然第二段", prompt_text)
        self.assertIn("先接 Initial Reply", prompt_text)
        self.assertIn("[emo:key][act:key]", prompt_text)

    def test_followup_prompt_compacts_large_profiles_and_retrieved_items(self):
        client = JsonClient(
            {
                "decision": "followup",
                "followup_type": "supplement",
                "reply": "补充一点：你以前提过济南。",
            }
        )
        input_data = _followup_input()
        input_data["original_context"] = {
            "model_profile": input_data["model_profile"],
            "user_profile": input_data["user_profile"],
            "compact_history": input_data["compact_history"],
            "recent_history": input_data["recent_history"],
        }
        input_data["original_context"]["model_profile"] = "甲" * 2000
        input_data["retrieved_items"] = [
            {
                "id": index,
                "summary": "乙" * 300,
                "tags_json": ["济南"],
                "memory_type": "event",
                "importance": 0.5,
            }
            for index in range(1, 8)
        ]

        generate_followup_reply(input_data, model_client=client)

        prompt_text = "\n".join(message.content for message in client.messages)
        self.assertLess(prompt_text.count("甲"), 1300)
        self.assertIn('"id": 5', prompt_text)
        self.assertNotIn('"id": 6', prompt_text)
        self.assertLess(prompt_text.count("乙"), 1200)

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

    def test_followup_reply_softens_system_memory_phrases(self):
        client = JsonClient(
            {
                "decision": "followup",
                "followup_type": "supplement",
                "reply": "数据库显示，你之前提过也喜欢季羡林；检索结果显示这点挺重要。",
            }
        )

        result = generate_followup_reply(_followup_input(), model_client=client)

        self.assertEqual(result["decision"], "followup")
        self.assertNotIn("数据库显示", result["reply"])
        self.assertNotIn("检索结果显示", result["reply"])
        self.assertIn("这点挺重要", result["reply"])

    def test_followup_reply_diversifies_memory_reference_opener(self):
        client = JsonClient(
            {
                "decision": "followup",
                "followup_type": "supplement",
                "reply": "我记得你以前说过，小时候在升官街滚铁圈。",
            }
        )

        result = generate_followup_reply(_followup_input(), model_client=client)

        self.assertEqual(result["decision"], "followup")
        self.assertRegex(result["reply"], r"^\[emo:[^\]]+\]\[act:[^\]]+\]")
        body = re.sub(r"^\[emo:[^\]]+\]\[act:[^\]]+\]", "", result["reply"])
        self.assertNotRegex(body, r"^(我记得|你以前|你之前)")
        self.assertIn("升官街滚铁圈", result["reply"])

    def test_followup_reply_diversifies_topic_bridge_opener(self):
        client = JsonClient(
            {
                "decision": "followup",
                "followup_type": "supplement",
                "reply": "说到读书，我记得你从小就是个书迷。",
            }
        )

        result = generate_followup_reply(_followup_input(), model_client=client)

        self.assertEqual(result["decision"], "followup")
        self.assertRegex(result["reply"], r"^\[emo:[^\]]+\]\[act:[^\]]+\]")
        body = re.sub(r"^\[emo:[^\]]+\]\[act:[^\]]+\]", "", result["reply"])
        self.assertFalse(body.startswith("说到"))
        self.assertIn("我记得你从小就是个书迷", result["reply"])

    def test_followup_reply_preserves_leading_expression_tags(self):
        client = JsonClient(
            {
                "decision": "followup",
                "followup_type": "supplement",
                "reply": "[emo:warm][act:😁]你以前提过济南。",
            }
        )

        result = generate_followup_reply(_followup_input(), model_client=client)

        self.assertEqual(result["decision"], "followup")
        self.assertTrue(result["reply"].startswith("[emo:warm][act:😁]"))
        body = re.sub(r"^\[emo:[^\]]+\]\[act:[^\]]+\]", "", result["reply"])
        self.assertNotRegex(body, r"^(我记得|你以前|你之前)")
        self.assertIn("济南", result["reply"])

    def test_followup_reply_adds_expression_tags_when_missing(self):
        client = JsonClient(
            {
                "decision": "followup",
                "followup_type": "supplement",
                "reply": "补充一点：这个记忆能接上刚才的话。",
            }
        )

        result = generate_followup_reply(_followup_input(), model_client=client)

        self.assertEqual(result["decision"], "followup")
        self.assertTrue(result["reply"].startswith("[emo:idle][act:😁]"))

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
        self.assertRegex(result["reply"], r"^\[emo:[^\]]+\]\[act:[^\]]+\]")
        self.assertIn("不确定", result["reply"])
        self.assertIn("专业", result["reply"])

    def test_high_risk_followup_logs_tag_trigger_reason(self):
        client = JsonClient(
            {
                "decision": "followup",
                "followup_type": "supplement",
                "reply": "这个合同条款要按之前的信息处理。",
            }
        )

        input_data = _followup_input()
        input_data["retrieved_items"][0]["tags_json"] = ["legal", "contract"]

        with self.assertLogs("chat-service.agents.dialogue", level="INFO") as logs:
            result = generate_followup_reply(input_data, model_client=client)

        self.assertEqual(result["decision"], "followup")
        log_text = "\n".join(logs.output)
        self.assertIn("source=retrieved_item_tag", log_text)
        self.assertIn("item_id=1", log_text)
        self.assertIn("tag=legal", log_text)

    def test_high_risk_followup_logs_domain_trigger_reason(self):
        client = JsonClient(
            {
                "decision": "followup",
                "followup_type": "supplement",
                "reply": "这个信息和之前的财务偏好有关。",
            }
        )

        input_data = _followup_input()
        input_data["retrieved_items"][0]["metadata_json"] = {"domain": "financial"}

        with self.assertLogs("chat-service.agents.dialogue", level="INFO") as logs:
            result = generate_followup_reply(input_data, model_client=client)

        self.assertEqual(result["decision"], "followup")
        log_text = "\n".join(logs.output)
        self.assertIn("source=retrieved_item_domain", log_text)
        self.assertIn("item_id=1", log_text)
        self.assertIn("domain=financial", log_text)

    def test_memory_type_domain_value_does_not_trigger_high_risk(self):
        client = JsonClient(
            {
                "decision": "followup",
                "followup_type": "supplement",
                "reply": "补充一点：这是普通记忆补充。",
            }
        )

        input_data = _followup_input()
        input_data["retrieved_items"][0]["memory_type"] = "financial"
        input_data["retrieved_items"][0]["sensitivity"] = "normal"
        input_data["retrieved_items"][0]["tags_json"] = []
        input_data["retrieved_items"][0]["metadata_json"] = {}

        result = generate_followup_reply(input_data, model_client=client)

        self.assertEqual(result["decision"], "followup")
        self.assertEqual(result["reply"], "[emo:idle][act:😁]补充一点：这是普通记忆补充。")
        self.assertNotIn("不确定", result["reply"])
        self.assertNotIn("专业", result["reply"])


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
