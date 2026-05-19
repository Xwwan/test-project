"""Dialogue Agent for initial and follow-up replies."""

from __future__ import annotations

from typing import Any

from src.agents._prompting import (
    dump_prompt_debug,
    dumps_pretty,
    normalize_history,
    parse_json_object,
    read_prompt,
    section,
)
from src.models import ChatMessage, ModelClient, chat_once, chat_stream


DEFAULT_DIALOGUE_PROMPT = """You are the Dialogue Agent.
Use the provided Model.md, User.md, compact memory, recent history, and current query.
For initial replies, answer directly without using retrieved events.
For follow-up decisions, return JSON only using decision, followup_type, and reply."""

NO_FOLLOWUP = {
    "decision": "no_followup",
    "followup_type": "none",
    "reply": "",
}

HIGH_RISK_QUERY_KEYWORDS = {
    "健康",
    "用药",
    "药",
    "吃药",
    "医疗",
    "医生",
    "诊断",
    "法律",
    "律师",
    "合同",
    "诉讼",
    "财务",
    "投资",
    "股票",
    "基金",
    "税",
    "贷款",
    "保险",
}

HIGH_RISK_SENSITIVITY = {"sensitive", "high", "high_risk"}


def generate_initial_reply(
    input_data: dict,
    *,
    model_client: ModelClient | None = None,
    model: str | None = None,
) -> dict:
    """Generate the immediate user-facing reply through an API LLM."""

    _validate_input_data(input_data)
    request_id = _required_string(input_data, "request_id")

    prompt = read_prompt("dialogue_agent.md", DEFAULT_DIALOGUE_PROMPT)
    messages = [
        ChatMessage(role="system", content=prompt),
        ChatMessage(role="user", content=_build_initial_context(input_data)),
    ]
    dump_prompt_debug(
        "dialogue.initial",
        request_id=request_id,
        recent_history=input_data.get("recent_history", []),
        messages=messages,
    )
    response = chat_once(
        messages,
        client=model_client,
        route="dialogue.initial",
        model=model,
    )
    return {
        "request_id": request_id,
        "reply": response.content.strip(),
    }


def generate_initial_reply_stream(
    input_data: dict,
    *,
    model_client: ModelClient | None = None,
    model: str | None = None,
):
    """Yield immediate user-facing reply text deltas through a streaming LLM."""

    _validate_input_data(input_data)

    prompt = read_prompt("dialogue_agent.md", DEFAULT_DIALOGUE_PROMPT)
    messages = [
        ChatMessage(role="system", content=prompt),
        ChatMessage(role="user", content=_build_initial_context(input_data)),
    ]
    dump_prompt_debug(
        "dialogue.initial.stream",
        request_id=_required_string(input_data, "request_id"),
        recent_history=input_data.get("recent_history", []),
        messages=messages,
    )
    yield from chat_stream(
        messages,
        client=model_client,
        route="dialogue.initial",
        model=model,
    )


def generate_followup_reply(
    input_data: dict,
    *,
    model_client: ModelClient | None = None,
    model: str | None = None,
) -> dict:
    """Decide whether retrieved memories justify a second user-facing reply."""

    _validate_input_data(input_data)
    request_id = _required_string(input_data, "request_id")
    retrieved_items = input_data.get("retrieved_items", [])
    if not retrieved_items:
        return {"request_id": request_id, **NO_FOLLOWUP}
    if not isinstance(retrieved_items, list):
        raise ValueError("retrieved_items must be a list")

    prompt = read_prompt("dialogue_agent.md", DEFAULT_DIALOGUE_PROMPT)
    messages = [
        ChatMessage(role="system", content=prompt),
        ChatMessage(role="user", content=_build_followup_context(input_data)),
    ]
    dump_prompt_debug(
        "dialogue.followup",
        request_id=request_id,
        recent_history=input_data.get("recent_history", []),
        messages=messages,
    )
    response = chat_once(
        messages,
        client=model_client,
        route="dialogue.followup",
        model=model,
    )

    try:
        payload = parse_json_object(response.content)
    except Exception:
        return {"request_id": request_id, **NO_FOLLOWUP}

    normalized = _normalize_followup_payload(payload)
    if normalized["decision"] == "followup" and _is_high_risk(input_data):
        normalized["reply"] = _ensure_high_risk_caveat(normalized["reply"])
    return {"request_id": request_id, **normalized}


def _build_initial_context(input_data: dict) -> str:
    return "\n\n".join(
        [
            section("Model.md", input_data.get("model_profile", "")),
            section("User.md", input_data.get("user_profile", "")),
            section("Compact Memory", input_data.get("compact_history", "")),
            section("Recent History", normalize_history(input_data.get("recent_history", []))),
            section("Current User Query", input_data.get("current_query", "")),
        ]
    )


def _build_followup_context(input_data: dict) -> str:
    output_schema = {
        "decision": "followup",
        "followup_type": "supplement",
        "reply": "short user-facing follow-up text",
    }
    rules = [
        "Return no_followup when retrieved items are empty or weakly related.",
        "Return correction when memory would change or correct the initial reply.",
        "Return supplement when memory adds useful context without correcting the answer.",
        "For 高风险 domains including 健康、用药、法律、财务, be conservative: "
        "do not treat old memory as current fact, state uncertainty, and do not "
        "replace professional advice.",
        "If the user has moved to another topic, return no_followup.",
    ]
    return "\n\n".join(
        [
            section("Initial Reply", input_data.get("initial_reply", "")),
            section(
                "Current Conversation State",
                input_data.get("current_conversation_state", ""),
            ),
            section("Original User Query", input_data.get("original_user_query", "")),
            section("Model.md", input_data.get("model_profile", "")),
            section("User.md", input_data.get("user_profile", "")),
            section("Compact Memory", input_data.get("compact_history", "")),
            section("Recent History", normalize_history(input_data.get("recent_history", []))),
            section("Retrieved Events", input_data.get("retrieved_items", [])),
            section("Follow-up Decision Rules", rules),
            section(
                "Output JSON Requirements",
                "Return JSON only. decision must be followup or no_followup. "
                "followup_type must be supplement, correction, or none. "
                "Use this shape:\n"
                f"{dumps_pretty(output_schema)}",
            ),
        ]
    )


def _normalize_followup_payload(payload: dict) -> dict:
    decision = payload.get("decision")
    followup_type = payload.get("followup_type")
    reply = payload.get("reply")
    reply = reply.strip() if isinstance(reply, str) else ""

    if decision != "followup":
        return dict(NO_FOLLOWUP)

    if followup_type not in {"supplement", "correction"}:
        followup_type = "supplement" if reply else "none"

    if followup_type == "none" or not reply:
        return dict(NO_FOLLOWUP)

    return {
        "decision": "followup",
        "followup_type": followup_type,
        "reply": reply,
    }


def _is_high_risk(input_data: dict) -> bool:
    query = str(input_data.get("original_user_query", ""))
    if any(keyword in query for keyword in HIGH_RISK_QUERY_KEYWORDS):
        return True

    for item in input_data.get("retrieved_items", []):
        if not isinstance(item, dict):
            continue
        sensitivity = str(item.get("sensitivity", "")).lower()
        if sensitivity in HIGH_RISK_SENSITIVITY:
            return True
        memory_type = str(item.get("memory_type", "")).lower()
        if memory_type in {"medical", "legal", "financial"}:
            return True
    return False


def _ensure_high_risk_caveat(reply: str) -> str:
    if "不确定" in reply and "专业" in reply:
        return reply
    caveat = "补充说明：以下内容只基于历史记忆，存在不确定性，不能替代专业意见。"
    return f"{caveat}\n{reply}"


def _validate_input_data(input_data: Any) -> None:
    if not isinstance(input_data, dict):
        raise ValueError("input_data must be a dictionary")


def _required_string(input_data: dict, key: str) -> str:
    value = input_data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"input_data.{key} must be a non-empty string")
    return value
