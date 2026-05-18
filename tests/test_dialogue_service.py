"""Tests for the Dialogue Service / Memory Service orchestration."""

from __future__ import annotations

from typing import Any

import unittest

from src.coordinator import request_coordinator
from src.services import (
    DialogueDependencies,
    apply_operations,
    curate_conversation_memory,
    handle_chat_message,
    handle_chat_message_stream,
    handle_followup,
    refresh_user_profile,
)


# ---------------------------------------------------------------------------
# In-memory fakes for Person 1 / Person 2 collaborators
# ---------------------------------------------------------------------------


class FakeConversationStore:
    def __init__(self) -> None:
        self.turns: list[tuple[str, dict]] = []
        self.compact_history: dict[str, str] = {}

    def append_turn(self, conversation_id: str, turn: dict) -> str:
        self.turns.append((conversation_id, dict(turn)))
        return turn["turn_id"]

    def get_recent_history(self, conversation_id: str, limit: int = 20) -> list[dict]:
        items = [turn for cid, turn in self.turns if cid == conversation_id]
        return items[-limit:]

    def get_compact_history(self, conversation_id: str) -> str:
        return self.compact_history.get(conversation_id, "")

    def update_compact_history(self, conversation_id: str, compact: str) -> None:
        self.compact_history[conversation_id] = compact


class FakeMemoryStore:
    def __init__(self, lightweight: list[dict] | None = None) -> None:
        self.lightweight = list(lightweight or [])
        self.full_items_by_id = {item["id"]: item for item in self.lightweight}
        self.applied_operations: list[list[dict]] = []

    def list_lightweight_memory_items(self, status: str = "active") -> list[dict]:
        return [dict(item) for item in self.lightweight if item.get("status", "active") == status]

    def get_memory_items_by_ids(self, ids: list[int]) -> list[dict]:
        return [dict(self.full_items_by_id[i]) for i in ids if i in self.full_items_by_id]

    def apply_memory_operations(self, operations: list[dict]) -> list[dict]:
        self.applied_operations.append([dict(op) for op in operations])
        return [{"operation": op["operation"], "memory_item": op.get("payload", {})}
                for op in operations]


class FakePersona:
    def __init__(self, model_profile: str = "model-md", user_profile: str = "user-md") -> None:
        self.model_profile = model_profile
        self.user_profile = user_profile
        self.applied_patches: list[dict] = []

    def read_model_profile(self) -> str:
        return self.model_profile

    def read_user_profile(self) -> str:
        return self.user_profile

    def apply_user_profile_patch(self, patch: dict) -> str:
        self.applied_patches.append(dict(patch))
        if "content" in patch:
            self.user_profile = patch["content"]
        return self.user_profile


class RecordingInitialReplyFn:
    """Behaves like Person 2's generate_initial_reply but is deterministic."""

    def __init__(self, reply: str = "hi there") -> None:
        self.reply = reply
        self.last_input: dict | None = None

    def __call__(self, input_data: dict, *, model_client: Any = None, **kwargs: Any) -> dict:
        self.last_input = input_data
        return {"request_id": input_data["request_id"], "reply": self.reply}


class RecordingInitialReplyStreamFn:
    """Behaves like Person 2's generate_initial_reply_stream."""

    def __init__(self, chunks: list[str] | None = None) -> None:
        self.chunks = chunks or ["hi", " there"]
        self.last_input: dict | None = None

    def __call__(self, input_data: dict, *, model_client: Any = None, **kwargs: Any):
        self.last_input = input_data
        yield from self.chunks


class RecordingRetrievalFn:
    def __init__(self, selected_ids: list[int]) -> None:
        self.selected_ids = list(selected_ids)
        self.last_kwargs: dict | None = None

    def __call__(self, **kwargs: Any) -> dict:
        self.last_kwargs = kwargs
        return {
            "request_id": kwargs["request_id"],
            "selected_memory_ids": list(self.selected_ids),
            "retrieval_reason": "matched fake heuristic",
            "needs_full_load": bool(self.selected_ids),
            "strategy": "fake",
        }


class RecordingFollowupFn:
    def __init__(self, decision: dict) -> None:
        self.decision = dict(decision)
        self.last_input: dict | None = None

    def __call__(self, input_data: dict, *, model_client: Any = None, **kwargs: Any) -> dict:
        self.last_input = input_data
        return {"request_id": input_data["request_id"], **self.decision}


def _build_deps(
    *,
    persona: FakePersona | None = None,
    conversation_store: FakeConversationStore | None = None,
    memory_store: FakeMemoryStore | None = None,
    initial_reply_fn: Any = None,
    initial_reply_stream_fn: Any = None,
    retrieval_fn: Any = None,
    followup_fn: Any = None,
    extract_fn: Any = None,
    consolidator_fn: Any = None,
    model_client: Any = None,
) -> DialogueDependencies:
    persona = persona or FakePersona()
    conversation_store = conversation_store or FakeConversationStore()
    memory_store = memory_store or FakeMemoryStore()
    initial_reply_fn = initial_reply_fn or RecordingInitialReplyFn()
    retrieval_fn = retrieval_fn or RecordingRetrievalFn(selected_ids=[])
    followup_fn = followup_fn or RecordingFollowupFn(
        {"decision": "no_followup", "followup_type": "none", "reply": ""}
    )
    extract_fn = extract_fn or (lambda **kwargs: {"operations": []})
    consolidator_fn = consolidator_fn or (
        lambda items, current, **kwargs: {
            "should_update": False,
            "patch": None,
            "reason": "default fake",
        }
    )

    return DialogueDependencies(
        read_model_profile=persona.read_model_profile,
        read_user_profile=persona.read_user_profile,
        apply_user_profile_patch=persona.apply_user_profile_patch,
        append_turn=conversation_store.append_turn,
        get_recent_history=conversation_store.get_recent_history,
        get_compact_history=conversation_store.get_compact_history,
        update_compact_history=conversation_store.update_compact_history,
        list_lightweight_memory_items=memory_store.list_lightweight_memory_items,
        get_memory_items_by_ids=memory_store.get_memory_items_by_ids,
        apply_memory_operations=memory_store.apply_memory_operations,
        generate_initial_reply=initial_reply_fn,
        generate_initial_reply_stream=initial_reply_stream_fn,
        generate_followup_reply=followup_fn,
        retrieve_relevant_memory_ids=retrieval_fn,
        extract_memory_operations=extract_fn,
        generate_user_profile_patch=consolidator_fn,
        model_client=model_client,
        recent_history_limit=20,
    )


# ---------------------------------------------------------------------------
# handle_chat_message
# ---------------------------------------------------------------------------


class DialogueServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        request_coordinator.reset_store()

    def tearDown(self) -> None:
        request_coordinator.reset_store()

    def test_handle_chat_message_returns_first_reply_and_binds_ids(self) -> None:
        initial = RecordingInitialReplyFn(reply="你好，小明！")
        deps = _build_deps(initial_reply_fn=initial)

        response = handle_chat_message("conv-1", "你好", dependencies=deps)

        assert response["conversation_id"] == "conv-1"
        assert response["reply"] == "你好，小明！"
        assert response["retrieval_status"] == "completed"
        assert response["request_id"].startswith("req_")
        assert response["turn_id"].startswith("turn_")
        assert response["retrieved_memory_ids"] == []

        final_record = request_coordinator.get_request(response["request_id"])
        assert final_record["status"] == "retrieval_completed"
        assert final_record["initial_reply"] == "你好，小明！"
        assert final_record["retrieved_items"] == []

        assert initial.last_input is not None
        assert initial.last_input["request_id"] == response["request_id"]
        assert initial.last_input["current_query"] == "你好"


    def test_handle_chat_message_saves_user_and_assistant_turns(self) -> None:
        convo = FakeConversationStore()
        deps = _build_deps(conversation_store=convo)

        response = handle_chat_message("conv-1", "你好", dependencies=deps)

        assert [turn["role"] for cid, turn in convo.turns] == ["user", "assistant"]
        user_turn = convo.turns[0][1]
        assistant_turn = convo.turns[1][1]
        assert user_turn["turn_id"] == response["turn_id"]
        assert user_turn["content"] == "你好"
        assert assistant_turn["content"] == response["reply"]
        assert assistant_turn["metadata_json"]["turn_kind"] == "initial"

    def test_handle_chat_message_stream_yields_deltas_and_final_payload(self) -> None:
        convo = FakeConversationStore()
        memory = FakeMemoryStore(lightweight=[{"id": 2, "summary": "x"}])
        initial_stream = RecordingInitialReplyStreamFn(chunks=["你", "好"])
        retrieval = RecordingRetrievalFn(selected_ids=[2])
        deps = _build_deps(
            conversation_store=convo,
            memory_store=memory,
            initial_reply_stream_fn=initial_stream,
            retrieval_fn=retrieval,
        )

        events = list(handle_chat_message_stream("conv-1", "你好", dependencies=deps))

        assert [event["event"] for event in events] == ["meta", "delta", "delta", "done"]
        assert events[1]["data"] == {"delta": "你"}
        assert events[2]["data"] == {"delta": "好"}
        assert events[-1]["data"]["reply"] == "你好"
        assert events[-1]["data"]["retrieved_memory_ids"] == [2]
        assert [turn["role"] for _, turn in convo.turns] == ["user", "assistant"]
        assert convo.turns[-1][1]["content"] == "你好"
        assert initial_stream.last_input is not None
        assert initial_stream.last_input["current_query"] == "你好"

    def test_handle_chat_message_stream_falls_back_to_one_shot_reply(self) -> None:
        deps = _build_deps(initial_reply_fn=RecordingInitialReplyFn(reply="完整回复"))

        events = list(handle_chat_message_stream("conv-1", "你好", dependencies=deps))

        assert [event["event"] for event in events] == ["meta", "delta", "done"]
        assert events[1]["data"] == {"delta": "完整回复"}
        assert events[-1]["data"]["reply"] == "完整回复"

    def test_handle_chat_message_runs_retrieval_when_lightweight_items_exist(self) -> None:
        memory = FakeMemoryStore(
            lightweight=[
                {"id": 1, "summary": "用户喜欢喝绿茶", "memory_type": "preference"},
                {"id": 2, "summary": "用户对花生过敏", "memory_type": "constraint"},
            ]
        )
        retrieval = RecordingRetrievalFn(selected_ids=[2])
        deps = _build_deps(memory_store=memory, retrieval_fn=retrieval)

        response = handle_chat_message("conv-1", "今天可以吃花生酱吗？", dependencies=deps)

        assert response["retrieved_memory_ids"] == [2]
        assert retrieval.last_kwargs is not None
        assert retrieval.last_kwargs["current_query"] == "今天可以吃花生酱吗？"
        # The lightweight payload from Person 1 is forwarded to Person 2.
        assert [item["id"] for item in retrieval.last_kwargs["lightweight_memory_items"]] == [1, 2]


    def test_handle_chat_message_marks_failed_when_initial_reply_raises(self) -> None:
        def failing_initial(input_data: dict, *, model_client: Any = None, **kwargs: Any) -> dict:
            raise RuntimeError("model unavailable")

        deps = _build_deps(initial_reply_fn=failing_initial)

        with self.assertRaises(RuntimeError):
            handle_chat_message("conv-1", "你好", dependencies=deps)

        all_requests = request_coordinator.list_requests()
        assert len(all_requests) == 1
        failed = all_requests[0]
        assert failed["status"] == "failed"
        assert "model unavailable" in (failed["error"] or "")


    def test_handle_chat_message_rejects_empty_inputs(self) -> None:
        deps = _build_deps()
        with self.assertRaises(ValueError):
            handle_chat_message("", "hello", dependencies=deps)
        with self.assertRaises(ValueError):
            handle_chat_message("conv-1", "", dependencies=deps)


    # ---------------------------------------------------------------------------
    # handle_followup
    # ---------------------------------------------------------------------------


    def test_handle_followup_returns_no_followup_when_no_retrieved_items(self) -> None:
        deps = _build_deps()
        chat_response = handle_chat_message("conv-1", "你好", dependencies=deps)

        decision = handle_followup(chat_response["request_id"], dependencies=deps)
        assert decision["decision"] == "no_followup"
        assert decision["reply"] == ""

        record = request_coordinator.get_request(chat_response["request_id"])
        assert record["status"] == "no_followup_needed"


    def test_handle_followup_saves_assistant_turn_when_followup_decision_returned(self) -> None:
        convo = FakeConversationStore()
        memory = FakeMemoryStore(
            lightweight=[{"id": 9, "summary": "用户对花生过敏", "memory_type": "constraint"}]
        )
        retrieval = RecordingRetrievalFn(selected_ids=[9])
        followup = RecordingFollowupFn(
            {
                "decision": "followup",
                "followup_type": "correction",
                "reply": "提醒一下，你之前告诉我对花生过敏。",
            }
        )
        deps = _build_deps(
            conversation_store=convo,
            memory_store=memory,
            retrieval_fn=retrieval,
            followup_fn=followup,
        )

        chat_response = handle_chat_message("conv-1", "今天吃花生酱吗？", dependencies=deps)
        decision = handle_followup(chat_response["request_id"], dependencies=deps)

        assert decision["decision"] == "followup"
        assert decision["followup_type"] == "correction"
        assistant_turns = [turn for cid, turn in convo.turns if turn["role"] == "assistant"]
        assert assistant_turns[-1]["content"] == decision["reply"]
        assert assistant_turns[-1]["metadata_json"]["turn_kind"] == "followup"

        record = request_coordinator.get_request(chat_response["request_id"])
        assert record["status"] == "followup_generated"


    def test_handle_followup_normalizes_decision_with_empty_reply_to_no_followup(self) -> None:
        memory = FakeMemoryStore(
            lightweight=[{"id": 1, "summary": "用户喜欢喝绿茶", "memory_type": "preference"}]
        )
        retrieval = RecordingRetrievalFn(selected_ids=[1])
        followup = RecordingFollowupFn(
            {"decision": "followup", "followup_type": "supplement", "reply": "   "}
        )
        deps = _build_deps(memory_store=memory, retrieval_fn=retrieval, followup_fn=followup)

        chat_response = handle_chat_message("conv-1", "推荐一种饮料", dependencies=deps)
        decision = handle_followup(chat_response["request_id"], dependencies=deps)

        assert decision["decision"] == "no_followup"
        assert decision["followup_type"] == "none"
        assert decision["reply"] == ""


    def test_handle_followup_validates_request_state(self) -> None:
        deps = _build_deps()
        created = request_coordinator.create_request("conv-1", "hi")
        with self.assertRaises(request_coordinator.RequestStateError):
            handle_followup(created["request_id"], dependencies=deps)


    # ---------------------------------------------------------------------------
    # Memory Service
    # ---------------------------------------------------------------------------


    def test_apply_operations_forwards_to_memory_store(self) -> None:
        memory = FakeMemoryStore()
        deps = _build_deps(memory_store=memory)
        operations = [
            {
                "operation": "create",
                "target_id": None,
                "payload": {"summary": "用户喜欢喝茶", "content": "..."},
            }
        ]
        apply_operations(operations, dependencies=deps)
        assert memory.applied_operations == [operations]


    def test_curate_conversation_memory_reads_history_and_applies_operations(self) -> None:
        convo = FakeConversationStore()
        memory = FakeMemoryStore()
        persona = FakePersona()
        # Seed a couple of turns
        convo.append_turn("conv-1", {"turn_id": "t1", "role": "user", "content": "记住我叫小明"})
        convo.append_turn(
            "conv-1",
            {"turn_id": "t2", "role": "assistant", "content": "记下了"},
        )

        captured: dict = {}

        def extract_fn(**kwargs):
            captured.update(kwargs)
            return {
                "operations": [
                    {
                        "operation": "create",
                        "target_id": None,
                        "payload": {
                            "summary": "用户名字：小明",
                            "content": "用户自我介绍。",
                            "memory_type": "profile_update",
                        },
                    }
                ]
            }

        deps = _build_deps(
            persona=persona,
            conversation_store=convo,
            memory_store=memory,
            extract_fn=extract_fn,
        )

        result = curate_conversation_memory(
            "conv-1", dependencies=deps, model_client="dummy-client"
        )

        assert result["conversation_id"] == "conv-1"
        assert [op["operation"] for op in result["operations"]] == ["create"]
        assert memory.applied_operations[0] == result["operations"]

        # The curator received the conversation turns and was given the dummy client.
        assert captured["conversation_id"] == "conv-1"
        assert [t["turn_id"] for t in captured["turns"]] == ["t1", "t2"]
        assert captured["model_client"] == "dummy-client"


    def test_refresh_user_profile_writes_when_consolidator_recommends_update(self) -> None:
        persona = FakePersona(user_profile="")
        memory = FakeMemoryStore(
            lightweight=[
                {
                    "id": 1,
                    "summary": "用户名字：小明",
                    "memory_type": "profile_update",
                    "importance": 0.9,
                    "confidence": 0.9,
                    "status": "active",
                }
            ]
        )

        def consolidator_fn(items, current, **kwargs):
            assert items[0]["summary"] == "用户名字：小明"
            return {
                "should_update": True,
                "patch": {"operation": "replace", "content": "# User\n- 名字：小明\n"},
                "reason": "fake",
            }

        deps = _build_deps(persona=persona, memory_store=memory, consolidator_fn=consolidator_fn)

        result = refresh_user_profile(dependencies=deps)
        assert result["should_update"] is True
        assert result["new_profile"].startswith("# User")
        assert persona.applied_patches == [{"operation": "replace", "content": "# User\n- 名字：小明\n"}]


    def test_refresh_user_profile_returns_without_writing_when_no_update_needed(self) -> None:
        persona = FakePersona(user_profile="existing")
        memory = FakeMemoryStore(lightweight=[])
        deps = _build_deps(persona=persona, memory_store=memory)

        result = refresh_user_profile(dependencies=deps)
        assert result["should_update"] is False
        assert result["new_profile"] is None
        assert persona.applied_patches == []
