"""Tests for the Memory Curator and Profile Consolidator.

The curator is intentionally LLM-only; every test injects a deterministic
fake ``ModelClient`` so the suite stays hermetic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import unittest
from unittest.mock import patch

from src.agents.memory_curator import (
    LLMMemoryExtractor,
    extract_memory_operations,
)
from src.agents.profile_consolidator import (
    AUTO_SECTION_MARKER,
    USER_PROFILE_HEADER,
    generate_user_profile_patch,
)


@dataclass
class FakeChatResponse:
    content: str


class FakeModelClient:
    """Records calls and returns a scripted JSON response."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def chat(self, messages: list[dict], **kwargs: Any) -> FakeChatResponse:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return FakeChatResponse(content=self.content)


class FailingModelClient:
    def chat(self, messages: list[dict], **kwargs: Any) -> FakeChatResponse:
        return FakeChatResponse(content="not json at all")


CONVERSATION_TURNS = [
    {
        "turn_id": "turn_user_1",
        "role": "user",
        "content": "记住我叫小明，我对花生过敏。",
        "created_at": "2026-05-12T11:00:00Z",
    },
    {
        "turn_id": "turn_assistant_1",
        "role": "assistant",
        "content": "好的，已经记下了。",
        "created_at": "2026-05-12T11:00:05Z",
    },
]


def _llm_response(operations: list[dict]) -> str:
    return json.dumps({"operations": operations}, ensure_ascii=False)


class MemoryCuratorTest(unittest.TestCase):
    def test_extract_memory_operations_passes_context_to_llm_and_returns_operations(self) -> None:
        scripted = _llm_response(
            [
                {
                    "operation": "create",
                    "target_id": None,
                    "payload": {
                        "summary": "用户名字：小明",
                        "content": "用户自我介绍叫小明。",
                        "memory_type": "profile_update",
                        "tags_json": ["identity"],
                        "metadata_json": {"source": "llm"},
                        "confidence": 0.9,
                        "importance": 0.9,
                        "sensitivity": "normal",
                        "status": "active",
                    },
                },
                {
                    "operation": "create",
                    "target_id": None,
                    "payload": {
                        "summary": "用户对花生过敏",
                        "content": "用户表示对花生过敏。",
                        "memory_type": "constraint",
                        "confidence": 0.9,
                        "importance": 0.9,
                        "sensitivity": "sensitive",
                    },
                },
            ]
        )
        client = FakeModelClient(content=scripted)

        result = extract_memory_operations(
            conversation_id="conv-1",
            turns=CONVERSATION_TURNS,
            existing_memory_candidates=[],
            model_client=client,
            model="my-test-model",
        )

        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["kwargs"]["temperature"] == 0
        assert call["kwargs"]["response_format"] == {"type": "json_object"}
        assert call["kwargs"]["model"] == "my-test-model"

        system_message, user_message = call["messages"]
        assert system_message["role"] == "system"
        assert "operations" in system_message["content"]
        assert user_message["role"] == "user"
        user_payload = json.loads(user_message["content"])
        assert user_payload["conversation_id"] == "conv-1"
        assert user_payload["turns"] == CONVERSATION_TURNS
        assert user_payload["existing_memory_candidates"] == []

        assert [op["operation"] for op in result["operations"]] == ["create", "create"]
        profile_op = result["operations"][0]
        assert profile_op["payload"]["memory_type"] == "profile_update"
        assert profile_op["payload"]["status"] == "active"

        constraint_op = result["operations"][1]
        assert constraint_op["payload"]["sensitivity"] == "sensitive"
        assert constraint_op["payload"]["memory_type"] == "constraint"


    def test_extract_memory_operations_returns_empty_for_no_signal(self) -> None:
        client = FakeModelClient(content=_llm_response([]))
        result = extract_memory_operations(
            conversation_id="conv-1",
            turns=[
                {"turn_id": "t1", "role": "user", "content": "你好", "created_at": "x"},
            ],
            existing_memory_candidates=[],
            model_client=client,
        )
        assert result == {"operations": []}


    def test_extract_memory_operations_validates_operation_payload(self) -> None:
        bad_response = _llm_response(
            [
                {
                    "operation": "create",
                    "target_id": None,
                    "payload": {"summary": "", "content": "missing summary"},
                }
            ]
        )
        client = FakeModelClient(content=bad_response)
        with self.assertRaises(ValueError):
            extract_memory_operations(
                conversation_id="conv-1",
                turns=CONVERSATION_TURNS,
                existing_memory_candidates=[],
                model_client=client,
            )


    def test_extract_memory_operations_rejects_unsupported_operation_name(self) -> None:
        client = FakeModelClient(
            content=_llm_response(
                [
                    {
                        "operation": "delete",
                        "target_id": None,
                        "payload": {"summary": "x", "content": "y"},
                    }
                ]
            )
        )
        with self.assertRaises(ValueError):
            extract_memory_operations(
                conversation_id="conv-1",
                turns=CONVERSATION_TURNS,
                existing_memory_candidates=[],
                model_client=client,
            )


    def test_extract_memory_operations_normalizes_optional_payload_fields(self) -> None:
        client = FakeModelClient(
            content=_llm_response(
                [
                    {
                        "operation": "create",
                        "target_id": None,
                        "payload": {
                            "summary": "用户喜欢喝茶",
                            "content": "用户提到自己喜欢喝绿茶。",
                        },
                    }
                ]
            )
        )
        result = extract_memory_operations(
            conversation_id="conv-1",
            turns=CONVERSATION_TURNS,
            existing_memory_candidates=[],
            model_client=client,
        )
        payload = result["operations"][0]["payload"]
        assert payload["memory_type"] == "event"
        assert payload["sensitivity"] == "normal"
        assert payload["status"] == "active"


    def test_extract_memory_operations_forwards_existing_candidates(self) -> None:
        client = FakeModelClient(content=_llm_response([]))
        existing = [
            {"id": 5, "summary": "用户喜欢喝茶", "memory_type": "preference"},
        ]
        extract_memory_operations(
            conversation_id="conv-1",
            turns=CONVERSATION_TURNS,
            existing_memory_candidates=existing,
            model_client=client,
        )
        user_payload = json.loads(client.calls[0]["messages"][1]["content"])
        assert user_payload["existing_memory_candidates"] == existing


    def test_extract_memory_operations_supports_update_operation(self) -> None:
        client = FakeModelClient(
            content=_llm_response(
                [
                    {
                        "operation": "update",
                        "target_id": 5,
                        "payload": {"summary": "用户更明确地表示喜欢喝绿茶"},
                    }
                ]
            )
        )
        result = extract_memory_operations(
            conversation_id="conv-1",
            turns=CONVERSATION_TURNS,
            existing_memory_candidates=[{"id": 5, "summary": "用户喜欢喝茶"}],
            model_client=client,
        )
        assert result["operations"][0]["operation"] == "update"
        assert result["operations"][0]["target_id"] == 5


    def test_extract_memory_operations_rejects_invalid_target_id(self) -> None:
        client = FakeModelClient(
            content=_llm_response(
                [
                    {
                        "operation": "update",
                        "target_id": -3,
                        "payload": {"summary": "x"},
                    }
                ]
            )
        )
        with self.assertRaises(ValueError):
            extract_memory_operations(
                conversation_id="conv-1",
                turns=CONVERSATION_TURNS,
                existing_memory_candidates=[],
                model_client=client,
            )


    def test_extract_memory_operations_raises_when_llm_returns_garbage(self) -> None:
        with self.assertRaises(ValueError):
            extract_memory_operations(
                conversation_id="conv-1",
                turns=CONVERSATION_TURNS,
                existing_memory_candidates=[],
                model_client=FailingModelClient(),
            )


    def test_extract_memory_operations_validates_inputs(self) -> None:
        client = FakeModelClient(content=_llm_response([]))
        with self.assertRaises(ValueError):
            extract_memory_operations("", CONVERSATION_TURNS, model_client=client)
        with self.assertRaises(ValueError):
            extract_memory_operations(
                "conv-1", "not a list", model_client=client  # type: ignore[arg-type]
            )
        with self.assertRaises(ValueError):
            extract_memory_operations(
                "conv-1",
                CONVERSATION_TURNS,
                existing_memory_candidates="not a list",  # type: ignore[arg-type]
                model_client=client,
            )


    def test_extractor_can_be_overridden_for_advanced_tests(self) -> None:
        class StubExtractor:
            def extract(self, input_data: dict) -> dict:
                return {
                    "operations": [
                        {
                            "operation": "archive",
                            "target_id": 9,
                            "payload": {},
                        }
                    ]
                }

        result = extract_memory_operations(
            conversation_id="conv-1",
            turns=CONVERSATION_TURNS,
            existing_memory_candidates=[],
            extractor=StubExtractor(),
        )
        assert result == {
            "operations": [{"operation": "archive", "target_id": 9, "payload": {}}]
        }


    def test_llm_extractor_uses_prompt_override_and_skips_model_kwarg(self) -> None:
        client = FakeModelClient(content=_llm_response([]))
        extractor = LLMMemoryExtractor(model_client=client, prompt="custom prompt")
        extractor.extract({"conversation_id": "c1", "turns": [], "existing_memory_candidates": []})

        call = client.calls[0]
        assert call["messages"][0]["content"] == "custom prompt"
        assert "model" not in call["kwargs"]


    def test_default_extractor_requires_model_api_key_when_no_client_available(self) -> None:
        """If neither model_client nor an extractor is provided, the curator should
        raise a clear configuration error instead of silently doing nothing.
        """

        with patch("src.models.chat.get_config_value", return_value=None):
            with self.assertRaises(ValueError):
                extract_memory_operations(
                    conversation_id="conv-1",
                    turns=CONVERSATION_TURNS,
                )


    # ---------------------------------------------------------------------------
    # Profile Consolidator
    # ---------------------------------------------------------------------------


    def test_profile_consolidator_returns_safe_default_with_no_inputs(self) -> None:
        result = generate_user_profile_patch(memory_items=[], current_user_profile="")
        assert result == {
            "should_update": False,
            "patch": None,
            "reason": "no long-term memory items reached the consolidation threshold",
        }


    def test_profile_consolidator_proposes_update_for_high_importance_profile(self) -> None:
        items = [
            {
                "id": 1,
                "summary": "用户名字：小明",
                "memory_type": "profile_update",
                "importance": 0.9,
                "confidence": 0.95,
                "status": "active",
            },
            {
                "id": 2,
                "summary": "用户对花生过敏",
                "memory_type": "constraint",
                "importance": 0.9,
                "confidence": 0.9,
                "status": "active",
            },
        ]
        result = generate_user_profile_patch(
            memory_items=items, current_user_profile=""
        )
        assert result["should_update"] is True
        assert result["patch"]["operation"] == "replace"
        new_content = result["patch"]["content"]
        assert USER_PROFILE_HEADER in new_content
        assert AUTO_SECTION_MARKER in new_content
        assert "用户名字：小明" in new_content
        assert "用户对花生过敏" in new_content


    def test_profile_consolidator_filters_low_importance_and_inactive_items(self) -> None:
        items = [
            {
                "summary": "短期偏好",
                "memory_type": "preference",
                "importance": 0.2,
                "status": "active",
            },
            {
                "summary": "已归档身份",
                "memory_type": "profile_update",
                "importance": 0.9,
                "status": "archived",
            },
        ]
        result = generate_user_profile_patch(items, "")
        assert result["should_update"] is False


    def test_profile_consolidator_returns_false_when_proposed_matches_current(self) -> None:
        items = [
            {
                "summary": "用户名字：小明",
                "memory_type": "profile_update",
                "importance": 0.9,
                "status": "active",
            }
        ]
        initial = generate_user_profile_patch(items, "")
        assert initial["should_update"] is True

        stable = initial["patch"]["content"]
        repeat = generate_user_profile_patch(items, stable)
        assert repeat["should_update"] is False
        assert repeat["patch"] is None


    def test_profile_consolidator_supports_custom_summarizer(self) -> None:
        def summarizer(items: list[dict], current: str) -> str:
            bullets = "\n".join(f"- {item['summary']}" for item in items)
            return f"# 自定义模板\n{bullets}\n"

        items = [
            {
                "summary": "用户偏好简洁回答",
                "memory_type": "preference",
                "importance": 0.8,
                "status": "active",
            }
        ]
        result = generate_user_profile_patch(items, "", summarizer=summarizer)
        assert result["should_update"] is True
        assert result["patch"]["content"].startswith("# 自定义模板")


    def test_profile_consolidator_validates_inputs(self) -> None:
        with self.assertRaises(ValueError):
            generate_user_profile_patch("not a list", "")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            generate_user_profile_patch([], 123)  # type: ignore[arg-type]
