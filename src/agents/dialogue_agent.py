"""Dialogue Agent for initial and follow-up replies."""

from __future__ import annotations

import json
import logging
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


logger = logging.getLogger("chat-service.agents.dialogue")


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

HIGH_RISK_MEMORY_DOMAINS = {
    "health",
    "medical",
    "medicine",
    "legal",
    "law",
    "finance",
    "financial",
    "investment",
    "stock",
    "fund",
    "tax",
    "loan",
    "insurance",
    "健康",
    "医疗",
    "用药",
    "法律",
    "财务",
    "金融",
    "投资",
    "股票",
    "基金",
    "税",
    "贷款",
    "保险",
}


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

    prompt = DEFAULT_DIALOGUE_PROMPT
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
    template = read_prompt(
        "dialogue_followup_decision.md",
        _DEFAULT_FOLLOWUP_DECISION_TEMPLATE,
    )
    original_context = input_data.get("original_context")
    if not isinstance(original_context, dict):
        original_context = {}
    latest_context = input_data.get("latest_context")
    if not isinstance(latest_context, dict):
        latest_context = {}

    replacements = {
        "request_id": input_data.get("request_id", ""),
        "conversation_id": input_data.get("conversation_id", ""),
        "parent_user_turn_id": input_data.get("parent_user_turn_id", ""),
        "parent_initial_reply_turn_id": input_data.get(
            "parent_initial_reply_turn_id",
            "",
        ),
        "current_conversation_state": input_data.get("current_conversation_state", ""),
        "original_user_query": input_data.get("original_user_query", ""),
        "initial_reply": input_data.get("initial_reply", ""),
        "original_model_profile": original_context.get(
            "model_profile",
            input_data.get("model_profile", ""),
        ),
        "original_user_profile": original_context.get(
            "user_profile",
            input_data.get("user_profile", ""),
        ),
        "original_compact_history": original_context.get(
            "compact_history",
            input_data.get("compact_history", ""),
        ),
        "original_recent_history": normalize_history(
            original_context.get("recent_history", input_data.get("recent_history", []))
        ),
        "retrieved_items": input_data.get("retrieved_items", []),
        "latest_recent_history": normalize_history(
            latest_context.get(
                "recent_history",
                input_data.get("latest_recent_history", []),
            )
        ),
        "newer_turns_since_original_request": normalize_history(
            latest_context.get(
                "newer_turns_since_original_request",
                input_data.get("newer_turns_since_original_request", []),
            )
        ),
    }

    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", _prompt_value(value))
    return rendered


_DEFAULT_FOLLOWUP_DECISION_TEMPLATE = """# Dialogue Follow-up Decision Prompt

你需要判断一次已经完成的 initial reply 是否需要基于异步检索到的记忆，向用户发送一条二次回复。

Original Request Context 是二次回复要服务的原始问题上下文。
Retrieved Events 只能用于判断是否应补充或纠正该原始问题的 initial reply。
Latest Conversation Context 只能用于判断二次回复的措辞、时机和是否需要明确指回原始问题。
不要用 Latest Conversation Context 改写、扩展或重新解释 Original User Query。
采用积极二次回复策略：如果 Retrieved Events 对原始问题有明确价值，即使用户已经切换到新话题，也可以发送简短二次回复。
健康、用药、法律、财务等高风险场景必须保守。

request_id: {{request_id}}
conversation_id: {{conversation_id}}
parent_user_turn_id: {{parent_user_turn_id}}
parent_initial_reply_turn_id: {{parent_initial_reply_turn_id}}
current_conversation_state: {{current_conversation_state}}

## Original Request Context
### Original User Query
{{original_user_query}}

### Initial Reply
{{initial_reply}}

### Original Model.md
{{original_model_profile}}

### Original User.md
{{original_user_profile}}

### Original Compact Memory
{{original_compact_history}}

### Original Recent History
{{original_recent_history}}

## Retrieved Events
{{retrieved_items}}

## Latest Conversation Context
### Latest Recent History
{{latest_recent_history}}

### Newer Turns Since Original Request
{{newer_turns_since_original_request}}

只返回 JSON object，字段为 decision、followup_type、reply。
"""


def _prompt_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return dumps_pretty(value)


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
    request_id = str(input_data.get("request_id", ""))
    query = str(input_data.get("original_user_query", ""))
    for keyword in HIGH_RISK_QUERY_KEYWORDS:
        if keyword in query:
            logger.info(
                "high risk followup triggered request_id=%s source=query_keyword keyword=%s",
                request_id,
                keyword,
            )
            return True

    for index, item in enumerate(input_data.get("retrieved_items", [])):
        if not isinstance(item, dict):
            continue
        item_id = item.get("id", "")
        sensitivity = str(item.get("sensitivity", "")).lower()
        if sensitivity in HIGH_RISK_SENSITIVITY:
            logger.info(
                "high risk followup triggered request_id=%s "
                "source=retrieved_item_sensitivity item_index=%d item_id=%s sensitivity=%s",
                request_id,
                index,
                item_id,
                sensitivity,
            )
            return True

        for tag in _memory_item_tags(item):
            if tag in HIGH_RISK_MEMORY_DOMAINS:
                logger.info(
                    "high risk followup triggered request_id=%s "
                    "source=retrieved_item_tag item_index=%d item_id=%s tag=%s",
                    request_id,
                    index,
                    item_id,
                    tag,
                )
                return True

        domain = _memory_item_domain(item)
        if domain in HIGH_RISK_MEMORY_DOMAINS:
            logger.info(
                "high risk followup triggered request_id=%s "
                "source=retrieved_item_domain item_index=%d item_id=%s domain=%s",
                request_id,
                index,
                item_id,
                domain,
            )
            return True
    return False


def _memory_item_tags(item: dict) -> list[str]:
    tags = item.get("tags_json", [])
    if isinstance(tags, str):
        try:
            parsed = json.loads(tags)
        except json.JSONDecodeError:
            parsed = [tags]
        tags = parsed
    if not isinstance(tags, (list, tuple, set)):
        return []
    return [str(tag).strip().lower() for tag in tags if str(tag).strip()]


def _memory_item_domain(item: dict) -> str:
    metadata = item.get("metadata_json", {})
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            return ""
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("domain", "")).strip().lower()


def _ensure_high_risk_caveat(reply: str) -> str:
    # if "不确定" in reply and "专业" in reply:
    #     return reply
    # caveat = "补充说明：以下内容只基于历史记忆，存在不确定性，不能替代专业意见。"
    # return f"{caveat}\n{reply}"
    return reply


def _validate_input_data(input_data: Any) -> None:
    if not isinstance(input_data, dict):
        raise ValueError("input_data must be a dictionary")


def _required_string(input_data: dict, key: str) -> str:
    value = input_data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"input_data.{key} must be a non-empty string")
    return value
