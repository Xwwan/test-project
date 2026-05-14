"""Memory Retrieval Workflow for Person 2."""

from __future__ import annotations

from typing import Any

from src.agents._prompting import (
    dumps_pretty,
    normalize_history,
    parse_json_object,
    read_prompt,
    section,
)
from src.models import ChatMessage, ModelClient, chat_once


STRATEGY = "llm_direct_judgement"

DEFAULT_RETRIEVAL_PROMPT = """You are the Memory Retrieval Workflow.
Select only memory IDs that are strongly relevant to the current user query.
Return one JSON object with selected_memory_ids, retrieval_reason, and needs_full_load.
Do not invent memory IDs."""


def retrieve_relevant_memory_ids(
    request_id: str,
    current_query: str,
    compact_history: str,
    recent_history: list[dict],
    user_profile: str,
    lightweight_memory_items: list[dict],
    *,
    model_client: ModelClient | None = None,
    model: str | None = None,
) -> dict:
    """Return MemoryItem IDs selected by an API LLM judgement step."""

    _validate_string(request_id, "request_id")
    _validate_string(current_query, "current_query")
    if not isinstance(lightweight_memory_items, list):
        raise ValueError("lightweight_memory_items must be a list")

    if not lightweight_memory_items:
        return {
            "request_id": request_id,
            "selected_memory_ids": [],
            "retrieval_reason": "no lightweight memory items",
            "needs_full_load": False,
            "strategy": STRATEGY,
        }

    prompt = read_prompt("memory_retrieval_workflow.md", DEFAULT_RETRIEVAL_PROMPT)
    messages = [
        ChatMessage(role="system", content=prompt),
        ChatMessage(
            role="user",
            content=_build_retrieval_context(
                request_id=request_id,
                current_query=current_query,
                compact_history=compact_history,
                recent_history=recent_history,
                user_profile=user_profile,
                lightweight_memory_items=lightweight_memory_items,
            ),
        ),
    ]

    response = chat_once(
        messages,
        client=model_client,
        route="memory.retrieval",
        model=model,
    )

    try:
        payload = parse_json_object(response.content)
    except Exception as exc:
        return {
            "request_id": request_id,
            "selected_memory_ids": [],
            "retrieval_reason": f"invalid model JSON: {exc}",
            "needs_full_load": False,
            "strategy": STRATEGY,
        }

    selected_ids = _normalize_selected_ids(
        payload.get("selected_memory_ids", []),
        lightweight_memory_items,
    )
    reason = payload.get("retrieval_reason")
    if not isinstance(reason, str) or not reason.strip():
        reason = "model did not provide a retrieval reason"

    return {
        "request_id": request_id,
        "selected_memory_ids": selected_ids,
        "retrieval_reason": reason.strip(),
        "needs_full_load": bool(selected_ids),
        "strategy": STRATEGY,
    }


def _build_retrieval_context(
    *,
    request_id: str,
    current_query: str,
    compact_history: str,
    recent_history: list[dict],
    user_profile: str,
    lightweight_memory_items: list[dict],
) -> str:
    output_schema = {
        "request_id": request_id,
        "selected_memory_ids": [1, 2],
        "retrieval_reason": "short explanation",
        "needs_full_load": True,
        "strategy": STRATEGY,
    }
    return "\n\n".join(
        [
            section("Request ID", request_id),
            section("Current User Query", current_query),
            section("User Profile", user_profile or ""),
            section("Compact History", compact_history or ""),
            section("Recent History", normalize_history(recent_history)),
            section("Lightweight Memory Items", lightweight_memory_items),
            section(
                "Output JSON Requirements",
                "Return JSON only. selected_memory_ids must be a subset of the "
                "provided Lightweight Memory Items IDs. Use this shape:\n"
                f"{dumps_pretty(output_schema)}",
            ),
        ]
    )


def _normalize_selected_ids(raw_ids: Any, lightweight_memory_items: list[dict]) -> list[int]:
    available_ids = {
        int(item["id"])
        for item in lightweight_memory_items
        if isinstance(item, dict) and _is_int_like(item.get("id"))
    }
    if not isinstance(raw_ids, list):
        return []

    selected: list[int] = []
    seen: set[int] = set()
    for raw_id in raw_ids:
        if not _is_int_like(raw_id):
            continue
        memory_id = int(raw_id)
        if memory_id not in available_ids or memory_id in seen:
            continue
        selected.append(memory_id)
        seen.add(memory_id)
    return selected


def _is_int_like(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, str) and value.isdigit()


def _validate_string(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
