"""Tests for the Request Coordinator state machine and pending queue."""

from __future__ import annotations

import re

import unittest

from src.coordinator import (
    PendingFollowupQueue,
    REQUEST_STATUSES,
    RequestNotFoundError,
    RequestStateError,
    create_request,
    get_pending_followup_requests,
    get_request,
    mark_failed,
    mark_followup_decision,
    mark_followup_delivered,
    mark_followup_failed,
    mark_initial_reply,
    mark_retrieval_completed,
    mark_retrieval_failed,
    mark_retrieval_pending,
    reset_store,
)


REQUEST_ID_PATTERN = re.compile(r"^req_[0-9a-f]{32}$")
TURN_ID_PATTERN = re.compile(r"^turn_[0-9a-f]{32}$")


class RequestCoordinatorTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_store()

    def tearDown(self) -> None:
        reset_store()

    def test_create_request_returns_unique_ids_and_received_status(self) -> None:
        first = create_request("conv-1", "hello")
        second = create_request("conv-1", "hello again")

        assert REQUEST_ID_PATTERN.match(first["request_id"])
        assert TURN_ID_PATTERN.match(first["turn_id"])
        assert first["status"] == "received"
        assert first["conversation_id"] == "conv-1"
        assert first["user_message"] == "hello"

        assert first["request_id"] != second["request_id"]
        assert first["turn_id"] != second["turn_id"]


    def test_create_request_validates_inputs(self) -> None:
        with self.assertRaises(ValueError):
            create_request("", "hi")
        with self.assertRaises(ValueError):
            create_request("conv", "")


    def test_status_transitions_advance_through_initial_reply_to_completion(self) -> None:
        record = create_request("conv-1", "hello")
        request_id = record["request_id"]

        mark_initial_reply(request_id, "hi there")
        after_reply = get_request(request_id)
        assert after_reply["status"] == "initial_reply_generated"
        assert after_reply["initial_reply"] == "hi there"

        mark_retrieval_pending(request_id)
        assert get_request(request_id)["status"] == "retrieval_pending"

        mark_retrieval_completed(
            request_id,
            [{"id": 1, "summary": "likes tea"}],
        )
        after_retrieval = get_request(request_id)
        assert after_retrieval["status"] == "retrieval_completed"
        assert after_retrieval["retrieved_items"] == [{"id": 1, "summary": "likes tea"}]

        mark_followup_decision(
            request_id,
            {"decision": "no_followup", "followup_type": "none", "reply": ""},
        )
        final = get_request(request_id)
        assert final["status"] == "no_followup_needed"
        assert final["followup_decision"]["decision"] == "no_followup"
        assert final["retrieval_status"] == "completed"
        assert final["followup_status"] == "no_followup"


    def test_retrieval_completed_without_pending_step_is_allowed(self) -> None:
        record = create_request("conv-1", "hello")
        request_id = record["request_id"]
        mark_initial_reply(request_id, "hi")
        mark_retrieval_completed(request_id, [])

        assert get_request(request_id)["status"] == "retrieval_completed"


    def test_pending_followup_queue_lists_only_retrieval_completed_requests(self) -> None:
        record_a = create_request("conv-1", "msg-a")
        record_b = create_request("conv-2", "msg-b")

        for record in (record_a, record_b):
            mark_initial_reply(record["request_id"], "reply")
            mark_retrieval_completed(record["request_id"], [])

        pending = get_pending_followup_requests()
        pending_ids = [request["request_id"] for request in pending]
        assert pending_ids == [record_a["request_id"], record_b["request_id"]]

        mark_followup_decision(
            record_a["request_id"],
            {"decision": "no_followup", "followup_type": "none", "reply": ""},
        )

        pending_after = get_pending_followup_requests()
        assert [request["request_id"] for request in pending_after] == [record_b["request_id"]]


    def test_followup_decision_followup_branch_records_followup_status(self) -> None:
        record = create_request("conv-1", "hello")
        request_id = record["request_id"]
        mark_initial_reply(request_id, "hi")
        mark_retrieval_completed(request_id, [{"id": 7, "summary": "likes coffee"}])

        mark_followup_decision(
            request_id,
            {
                "decision": "followup",
                "followup_type": "supplement",
                "reply": "I remember you like coffee.",
            },
        )
        final = get_request(request_id)
        assert final["status"] == "followup_generated"
        assert final["followup_decision"]["decision"] == "followup"
        assert final["followup_decision"]["reply"] == "I remember you like coffee."
        assert final["delivery_status"] == "pending"

        mark_followup_delivered(request_id)
        assert get_request(request_id)["delivery_status"] == "delivered"


    def test_retrieval_and_followup_failures_do_not_use_global_failed_status(self) -> None:
        retrieval_record = create_request("conv-1", "hello")
        mark_initial_reply(retrieval_record["request_id"], "hi")
        mark_retrieval_pending(retrieval_record["request_id"])
        mark_retrieval_failed(retrieval_record["request_id"], "retrieval timeout")

        failed_retrieval = get_request(retrieval_record["request_id"])
        assert failed_retrieval["status"] == "retrieval_failed"
        assert failed_retrieval["retrieval_status"] == "failed"
        assert failed_retrieval["retrieval_error"] == "retrieval timeout"

        followup_record = create_request("conv-1", "hello again")
        mark_initial_reply(followup_record["request_id"], "hi")
        mark_retrieval_completed(followup_record["request_id"], [])
        mark_followup_failed(followup_record["request_id"], "followup timeout")

        failed_followup = get_request(followup_record["request_id"])
        assert failed_followup["status"] == "followup_failed"
        assert failed_followup["retrieval_status"] == "completed"
        assert failed_followup["followup_status"] == "failed"
        assert failed_followup["followup_error"] == "followup timeout"


    def test_mark_failed_clears_pending_followup_and_records_reason(self) -> None:
        record = create_request("conv-1", "hello")
        request_id = record["request_id"]
        mark_initial_reply(request_id, "hi")
        mark_retrieval_completed(request_id, [])

        mark_failed(request_id, "model timeout")
        final = get_request(request_id)
        assert final["status"] == "failed"
        assert final["error"] == "model timeout"
        assert get_pending_followup_requests() == []


    def test_invalid_transitions_raise_request_state_error(self) -> None:
        record = create_request("conv-1", "hello")
        request_id = record["request_id"]

        with self.assertRaises(RequestStateError):
            mark_retrieval_pending(request_id)

        mark_initial_reply(request_id, "hi")
        with self.assertRaises(RequestStateError):
            mark_initial_reply(request_id, "hi again")

        with self.assertRaises(RequestStateError):
            mark_followup_decision(
                request_id,
                {"decision": "no_followup", "followup_type": "none", "reply": ""},
            )


    def test_unknown_request_id_raises_not_found(self) -> None:
        with self.assertRaises(RequestNotFoundError):
            get_request("req_unknown")
        with self.assertRaises(RequestNotFoundError):
            mark_initial_reply("req_unknown", "hi")


    def test_followup_decision_validates_payload(self) -> None:
        record = create_request("conv-1", "hello")
        request_id = record["request_id"]
        mark_initial_reply(request_id, "hi")
        mark_retrieval_completed(request_id, [])

        with self.assertRaises(ValueError):
            mark_followup_decision(request_id, {"decision": "maybe"})
        with self.assertRaises(ValueError):
            mark_followup_decision(request_id, "no_followup")  # type: ignore[arg-type]


    def test_request_status_history_grows_with_each_transition(self) -> None:
        record = create_request("conv-1", "hello")
        request_id = record["request_id"]
        mark_initial_reply(request_id, "hi")
        mark_retrieval_pending(request_id)
        mark_retrieval_completed(request_id, [])
        mark_followup_decision(
            request_id,
            {"decision": "no_followup", "followup_type": "none", "reply": ""},
        )
        final = get_request(request_id)
        statuses = [event["status"] for event in final["status_history"]]
        assert statuses == [
            "received",
            "initial_reply_generated",
            "retrieval_pending",
            "retrieval_completed",
            "no_followup_needed",
        ]


    def test_request_statuses_constant_contains_documented_values(self) -> None:
        assert set(REQUEST_STATUSES) >= {
            "received",
            "initial_reply_generated",
            "retrieval_pending",
            "retrieval_completed",
            "retrieval_failed",
            "followup_generated",
            "no_followup_needed",
            "followup_failed",
            "completed",
            "failed",
        }


    def test_pending_followup_queue_deduplicates_and_pops_in_order(self) -> None:
        queue = PendingFollowupQueue()
        assert queue.enqueue("req_a") is True
        assert queue.enqueue("req_b") is True
        assert queue.enqueue("req_a") is False

        assert queue.snapshot() == ["req_a", "req_b"]
        assert queue.pop() == "req_a"
        assert queue.snapshot() == ["req_b"]
        assert queue.remove("req_b") is True
        assert queue.pop() is None
