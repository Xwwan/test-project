"""Tests for the API dispatch layer.

The tests work directly against :func:`dispatch` so no real socket is opened.
A single fake dependency container drives the underlying dialogue service.
"""

from __future__ import annotations

from typing import Any

import unittest

from src.api.routes import dispatch
from src.coordinator import request_coordinator
from src.services import DialogueDependencies


class FakeConversationStore:
    def __init__(self) -> None:
        self.turns: list[tuple[str, dict]] = []

    def append_turn(self, conversation_id: str, turn: dict) -> str:
        self.turns.append((conversation_id, dict(turn)))
        return turn["turn_id"]

    def get_recent_history(self, conversation_id: str, limit: int = 20) -> list[dict]:
        items = [turn for cid, turn in self.turns if cid == conversation_id]
        return items[-limit:]


class FakeMemoryStore:
    def __init__(self, lightweight: list[dict] | None = None) -> None:
        self.lightweight = list(lightweight or [])
        self.applied: list[list[dict]] = []

    def list_lightweight_memory_items(self, status: str = "active") -> list[dict]:
        return [dict(item) for item in self.lightweight if item.get("status", "active") == status]

    def get_memory_items_by_ids(self, ids: list[int]) -> list[dict]:
        by_id = {item["id"]: item for item in self.lightweight}
        return [dict(by_id[i]) for i in ids if i in by_id]

    def apply_memory_operations(self, operations: list[dict]) -> list[dict]:
        self.applied.append([dict(op) for op in operations])
        return [{"applied": True} for _ in operations]


def _build_dependencies(
    *,
    initial_reply: str = "你好",
    selected_ids: list[int] | None = None,
    followup: dict | None = None,
    operations: list[dict] | None = None,
    consolidator: dict | None = None,
    memory_store: FakeMemoryStore | None = None,
    conversation_store: FakeConversationStore | None = None,
) -> tuple[DialogueDependencies, FakeConversationStore, FakeMemoryStore]:
    convo = conversation_store or FakeConversationStore()
    memory = memory_store or FakeMemoryStore()
    selected_ids = selected_ids or []
    followup = followup or {"decision": "no_followup", "followup_type": "none", "reply": ""}
    operations = operations or []
    consolidator = consolidator or {"should_update": False, "patch": None, "reason": "fake"}

    def initial_reply_fn(input_data: dict, **kwargs: Any) -> dict:
        return {"request_id": input_data["request_id"], "reply": initial_reply}

    def retrieve_fn(**kwargs: Any) -> dict:
        return {
            "request_id": kwargs["request_id"],
            "selected_memory_ids": list(selected_ids),
            "retrieval_reason": "fake",
            "needs_full_load": bool(selected_ids),
            "strategy": "fake",
        }

    def followup_fn(input_data: dict, **kwargs: Any) -> dict:
        return {"request_id": input_data["request_id"], **followup}

    def extract_fn(**kwargs: Any) -> dict:
        return {"operations": list(operations)}

    def consolidator_fn(items: list[dict], current: str, **kwargs: Any) -> dict:
        return dict(consolidator)

    deps = DialogueDependencies(
        read_model_profile=lambda: "model-md",
        read_user_profile=lambda: "user-md",
        apply_user_profile_patch=lambda patch: patch.get("content", ""),
        append_turn=convo.append_turn,
        get_recent_history=convo.get_recent_history,
        get_compact_history=lambda _cid: "",
        update_compact_history=lambda _cid, _v: None,
        list_lightweight_memory_items=memory.list_lightweight_memory_items,
        get_memory_items_by_ids=memory.get_memory_items_by_ids,
        apply_memory_operations=memory.apply_memory_operations,
        generate_initial_reply=initial_reply_fn,
        generate_followup_reply=followup_fn,
        retrieve_relevant_memory_ids=retrieve_fn,
        extract_memory_operations=extract_fn,
        generate_user_profile_patch=consolidator_fn,
    )
    return deps, convo, memory


class ApiRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        request_coordinator.reset_store()

    def tearDown(self) -> None:
        request_coordinator.reset_store()

    def test_post_chat_returns_200_with_chat_response(self) -> None:
        deps, _, _ = _build_dependencies(initial_reply="你好，小明！")
        status, body = dispatch(
            "POST",
            "/chat",
            {"conversation_id": "conv-1", "message": "你好"},
            dependencies=deps,
        )
        assert status == 200
        assert body["reply"] == "你好，小明！"
        assert body["conversation_id"] == "conv-1"
        assert body["request_id"].startswith("req_")
        assert body["retrieval_status"] == "completed"


    def test_post_chat_validates_body(self) -> None:
        deps, _, _ = _build_dependencies()
        status, body = dispatch("POST", "/chat", {}, dependencies=deps)
        assert status == 400
        assert "conversation_id" in body["error"]["message"]


    def test_get_pending_followups_lists_retrieval_completed_requests(self) -> None:
        memory = FakeMemoryStore(lightweight=[{"id": 1, "summary": "x"}])
        deps, _, _ = _build_dependencies(memory_store=memory, selected_ids=[1])

        chat_status, chat_body = dispatch(
            "POST",
            "/chat",
            {"conversation_id": "conv-1", "message": "你好"},
            dependencies=deps,
        )
        assert chat_status == 200

        status, body = dispatch("GET", "/followups/pending", dependencies=deps)
        assert status == 200
        assert len(body["pending"]) == 1
        assert body["pending"][0]["request_id"] == chat_body["request_id"]


    def test_followup_run_returns_decision_and_clears_pending(self) -> None:
        memory = FakeMemoryStore(lightweight=[{"id": 1, "summary": "x"}])
        deps, _, _ = _build_dependencies(
            memory_store=memory,
            selected_ids=[1],
            followup={
                "decision": "followup",
                "followup_type": "supplement",
                "reply": "顺便补充：你之前提过这个。",
            },
        )

        _, chat_body = dispatch(
            "POST",
            "/chat",
            {"conversation_id": "conv-1", "message": "记得吗？"},
            dependencies=deps,
        )

        status, body = dispatch(
            "POST",
            f"/followups/{chat_body['request_id']}/run",
            None,
            dependencies=deps,
        )
        assert status == 200
        assert body["decision"] == "followup"
        assert body["reply"].startswith("顺便补充")

        _, pending = dispatch("GET", "/followups/pending", dependencies=deps)
        assert pending["pending"] == []


    def test_followup_run_returns_404_for_unknown_request(self) -> None:
        deps, _, _ = _build_dependencies()
        status, body = dispatch(
            "POST", "/followups/req_unknown/run", None, dependencies=deps
        )
        assert status == 404
        assert "unknown" in body["error"]["message"].lower()


    def test_memory_curate_returns_operations_and_applies_them(self) -> None:
        operations = [
            {
                "operation": "create",
                "target_id": None,
                "payload": {"summary": "用户喜欢喝茶", "content": "..."},
            }
        ]
        deps, convo, memory = _build_dependencies(operations=operations)
        convo.append_turn("conv-1", {"turn_id": "t1", "role": "user", "content": "记住"})

        status, body = dispatch(
            "POST",
            "/memory/curate",
            {"conversation_id": "conv-1"},
            dependencies=deps,
        )
        assert status == 200
        assert body["conversation_id"] == "conv-1"
        assert body["operations"] == operations
        assert memory.applied == [operations]


    def test_memory_profile_refresh_returns_consolidator_decision(self) -> None:
        deps, _, _ = _build_dependencies(
            consolidator={
                "should_update": True,
                "patch": {"operation": "replace", "content": "# Profile"},
                "reason": "fake",
            }
        )
        status, body = dispatch("POST", "/memory/profile/refresh", None, dependencies=deps)
        assert status == 200
        assert body["should_update"] is True
        assert body["new_profile"] == "# Profile"


    def test_healthz_returns_ok(self) -> None:
        status, body = dispatch("GET", "/healthz")
        assert status == 200
        assert body == {"status": "ok"}


    def test_unknown_route_returns_404(self) -> None:
        status, body = dispatch("GET", "/does-not-exist")
        assert status == 404
        assert "no route" in body["error"]["message"]


    def test_invalid_method_returns_404(self) -> None:
        deps, _, _ = _build_dependencies()
        status, body = dispatch("PUT", "/chat", {}, dependencies=deps)
        assert status == 404
