"""Dialogue Agent for initial and follow-up replies."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, Iterator

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

SYSTEM_MEMORY_PHRASES = {
    "数据库显示": "我记得你提过",
    "记忆库里有": "我记得你提过",
    "检索结果显示": "我记得你提过",
}
FOLLOWUP_MEMORY_REFERENCE_OPENERS = (
    "我记得你以前说过",
    "我记得你以前提过",
    "我记得你之前说过",
    "我记得你之前提过",
    "我记得你提过",
    "你以前说过",
    "你以前提过",
    "你之前说过",
    "你之前提过",
)
FOLLOWUP_OPENER_VARIANTS = (
    "那份旧日子的滋味还在，",
    "刚才那句话落到这儿，",
    "心里那点牵挂还在，",
    "这段回忆接到这里，",
    "那股认真劲儿还在，",
    "眼前这个画面一出来，",
    "旧日子里的那点暖意还在，",
    "这份心思一直没淡，",
)
FOLLOWUP_TOPIC_BRIDGE_OPENERS = (
    "说到",
    "提到",
    "说起",
)

DEFAULT_INITIAL_TAGS = "[emo:idle][act:😁]"
DEFAULT_FOLLOWUP_TAGS = "[emo:idle][act:😁]"
INITIAL_REPLY_SENTENCE_LIMIT = 2
INITIAL_STREAM_TAG_BUFFER_LIMIT = 96
FOLLOWUP_PROFILE_CHAR_LIMIT = 1200
FOLLOWUP_HISTORY_CHAR_LIMIT = 1200
FOLLOWUP_RETRIEVED_ITEM_LIMIT = 5
FOLLOWUP_RETRIEVED_SUMMARY_CHAR_LIMIT = 220
_INITIAL_TAG_PATTERN = re.compile(
    r"^\s*\[\s*emo\s*[:：]\s*[^\]\r\n]+?\s*\]\s*"
    r"\[\s*act\s*[:：]\s*[^\]\r\n]+?\s*\]",
    re.IGNORECASE,
)
_LEADING_REPLY_TAGS_PATTERN = re.compile(
    r"^\s*(?:\[\s*(?:emo|act)\s*[:：]\s*[^\]\r\n]+?\s*\]\s*)+",
    re.IGNORECASE,
)
_SENTENCE_PATTERN = re.compile(r"[^。！？!?]+[。！？!?]?")
_SENTENCE_ENDINGS = set("。！？!?")


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
        "reply": _normalize_initial_reply(response.content),
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
    raw_stream = chat_stream(
        messages,
        client=model_client,
        route="dialogue.initial",
        model=model,
    )
    yield from _normalize_initial_reply_stream(raw_stream)


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

    normalized = _normalize_followup_payload(payload, request_id=request_id)
    if normalized["decision"] == "followup" and _is_high_risk(input_data):
        normalized["reply"] = _ensure_high_risk_caveat(normalized["reply"])
    return {"request_id": request_id, **normalized}


def _normalize_initial_reply(reply: str) -> str:
    if not isinstance(reply, str):
        raise TypeError("initial reply must be a string")
    text = reply.strip()
    if not text:
        return text

    tag_match = _INITIAL_TAG_PATTERN.match(text)
    if tag_match:
        tags = tag_match.group(0).replace(" ", "")
        body = text[tag_match.end() :].strip()
    else:
        tags = DEFAULT_INITIAL_TAGS
        body = text

    body = _first_sentences(body, INITIAL_REPLY_SENTENCE_LIMIT)
    body = _ensure_initial_high_risk_guidance(body)
    return f"{tags}{body}".strip()


def _normalize_initial_reply_stream(deltas: Iterable[str]) -> Iterator[str]:
    tag_buffer = ""
    tags_emitted = False
    body_started = False
    sentence_count = 0

    def emit_body(text: str) -> Iterator[str]:
        nonlocal body_started, sentence_count
        if not body_started:
            text = text.lstrip()
            if text:
                body_started = True
        if not text:
            return

        out: list[str] = []
        for char in text:
            out.append(char)
            if char in _SENTENCE_ENDINGS:
                sentence_count += 1
                if sentence_count >= INITIAL_REPLY_SENTENCE_LIMIT:
                    break
        if out:
            yield "".join(out)

    for delta in deltas:
        if not isinstance(delta, str):
            raise TypeError("initial reply stream must yield strings")
        if not delta:
            continue
        if sentence_count >= INITIAL_REPLY_SENTENCE_LIMIT:
            return

        if not tags_emitted:
            tag_buffer += delta
            tag_buffer = tag_buffer.lstrip()
            if not tag_buffer:
                continue

            tag_match = _INITIAL_TAG_PATTERN.match(tag_buffer)
            if tag_match:
                tags = tag_match.group(0).replace(" ", "")
                yield tags
                tags_emitted = True
                body_text = tag_buffer[tag_match.end() :]
                tag_buffer = ""
                yield from emit_body(body_text)
                continue

            if _should_wait_for_initial_tags(tag_buffer):
                continue

            yield DEFAULT_INITIAL_TAGS
            tags_emitted = True
            body_text = tag_buffer
            tag_buffer = ""
            yield from emit_body(body_text)
            continue

        yield from emit_body(delta)

    if not tags_emitted and tag_buffer.strip():
        yield DEFAULT_INITIAL_TAGS
        yield from emit_body(tag_buffer)


def _should_wait_for_initial_tags(text: str) -> bool:
    compact = re.sub(r"\s+", "", text.lstrip()).lower()
    if not compact:
        return True
    if len(text) >= INITIAL_STREAM_TAG_BUFFER_LIMIT:
        return False
    if any(target.startswith(compact) for target in ("[emo:", "[emo：")):
        return True
    if compact.startswith("[emo:") or compact.startswith("[emo："):
        return compact.count("]") < 2
    return False


def _first_sentences(text: str, limit: int) -> str:
    clean = " ".join(str(text).strip().split())
    if not clean or limit <= 0:
        return ""

    sentences: list[str] = []
    for match in _SENTENCE_PATTERN.finditer(clean):
        sentence = match.group(0).strip()
        if not sentence:
            continue
        sentences.append(sentence)
        if len(sentences) >= limit:
            break
    if not sentences:
        return clean
    return "".join(sentences).strip()


def _ensure_initial_high_risk_guidance(body: str) -> str:
    text = str(body or "").strip()
    if not text:
        return text
    if any(keyword in text for keyword in ("摔", "头晕", "站不稳", "急救", "120")):
        if not any(keyword in text for keyword in ("家人", "医生", "急救", "120")):
            return f"{text} 先别硬撑，马上联系家人或医生，必要时打 120。"
    if any(keyword in text for keyword in ("稳赚不赔", "理财", "投资", "买之前")):
        if not any(keyword in text for keyword in ("家里人", "家人", "专业", "不要轻信")):
            return f"{text} 这种事先别急着答应，和家人或专业人士确认清楚再说。"
    if any(keyword in text for keyword in ("协议", "房子", "法律", "律师")):
        if not any(keyword in text for keyword in ("律师", "可信家人", "专业")):
            return f"{text} 最好请律师或可信家人一起把关，别自己匆忙定下来。"
    return text


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
    replacements = _compact_followup_replacements(replacements)

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


def _compact_followup_replacements(replacements: dict[str, Any]) -> dict[str, Any]:
    compacted = dict(replacements)
    for key in ("original_model_profile", "original_user_profile", "original_compact_history"):
        compacted[key] = _truncate_text(compacted.get(key, ""), FOLLOWUP_PROFILE_CHAR_LIMIT)
    for key in (
        "original_recent_history",
        "latest_recent_history",
        "newer_turns_since_original_request",
    ):
        compacted[key] = _truncate_prompt_value(compacted.get(key), FOLLOWUP_HISTORY_CHAR_LIMIT)
    compacted["retrieved_items"] = _compact_retrieved_items(
        compacted.get("retrieved_items", []),
        limit=FOLLOWUP_RETRIEVED_ITEM_LIMIT,
    )
    return compacted


def _compact_retrieved_items(value: Any, *, limit: int) -> list[dict]:
    if not isinstance(value, list):
        return []
    compacted = []
    for item in value[:limit]:
        if not isinstance(item, dict):
            continue
        compacted_item = {
            key: item.get(key)
            for key in ("id", "summary", "memory_type", "tags_json", "importance", "sensitivity")
            if key in item
        }
        if isinstance(compacted_item.get("summary"), str):
            compacted_item["summary"] = _truncate_text(
                compacted_item["summary"],
                FOLLOWUP_RETRIEVED_SUMMARY_CHAR_LIMIT,
            )
        compacted.append(compacted_item)
    return compacted


def _truncate_prompt_value(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return _truncate_text(value, limit)
    if isinstance(value, list):
        return [
            _truncate_turn(turn, limit)
            if isinstance(turn, dict)
            else _truncate_text(str(turn), limit)
            for turn in value
        ]
    return value


def _truncate_turn(turn: dict, limit: int) -> dict:
    return {
        key: _truncate_text(value, limit) if isinstance(value, str) else value
        for key, value in turn.items()
    }


def _truncate_text(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else str(value or "")
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _normalize_followup_payload(payload: dict, *, request_id: str = "") -> dict:
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
        "reply": _normalize_followup_reply_text(reply, request_id=request_id),
    }


def _normalize_followup_reply_text(reply: str, *, request_id: str = "") -> str:
    tags, body = _split_leading_reply_tags(reply)
    softened = _soften_system_memory_phrases(body)
    diversified = _diversify_memory_reference_opener(softened, request_id=request_id)
    diversified = _diversify_topic_bridge_opener(diversified, request_id=request_id)
    body = _first_sentences(diversified, 2)
    return f"{tags}{body}".strip()


def _soften_system_memory_phrases(reply: str) -> str:
    softened = reply
    for phrase, replacement in SYSTEM_MEMORY_PHRASES.items():
        softened = softened.replace(phrase, replacement)
    return softened


def _split_leading_reply_tags(reply: str) -> tuple[str, str]:
    tag_match = _INITIAL_TAG_PATTERN.match(reply)
    if not tag_match:
        body = _LEADING_REPLY_TAGS_PATTERN.sub("", reply).strip()
        return DEFAULT_FOLLOWUP_TAGS, body
    tags = tag_match.group(0).replace(" ", "")
    body = reply[tag_match.end() :].strip()
    return tags, body


def _diversify_memory_reference_opener(reply: str, *, request_id: str = "") -> str:
    stripped = reply.lstrip()
    leading_space = reply[: len(reply) - len(stripped)]
    for opener in FOLLOWUP_MEMORY_REFERENCE_OPENERS:
        if not stripped.startswith(opener):
            continue
        remainder = stripped[len(opener) :].lstrip("，,。:：；; ")
        variant = _select_followup_opener_variant(f"{request_id}|{remainder or stripped}")
        return f"{leading_space}{variant}{remainder}".strip()
    return reply


def _diversify_topic_bridge_opener(reply: str, *, request_id: str = "") -> str:
    stripped = reply.lstrip()
    leading_space = reply[: len(reply) - len(stripped)]
    for opener in FOLLOWUP_TOPIC_BRIDGE_OPENERS:
        if not stripped.startswith(opener):
            continue
        split_match = re.search(r"[，,。！？!?；;：:\s]", stripped)
        if not split_match:
            return reply
        remainder = stripped[split_match.end() :].lstrip()
        if not remainder:
            return reply
        variant = _select_followup_opener_variant(f"{request_id}|{remainder}")
        return f"{leading_space}{variant}{remainder}".strip()
    return reply


def _select_followup_opener_variant(seed: str) -> str:
    total = sum(ord(char) for char in seed)
    return FOLLOWUP_OPENER_VARIANTS[total % len(FOLLOWUP_OPENER_VARIANTS)]


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
    if "不确定" in reply and "专业" in reply:
        return reply
    caveat = "补充说明：以下内容只基于历史记忆，存在不确定性，不能替代专业意见。"
    tags, body = _split_leading_reply_tags(reply)
    return f"{tags}{caveat}{body}".strip()


def _validate_input_data(input_data: Any) -> None:
    if not isinstance(input_data, dict):
        raise ValueError("input_data must be a dictionary")


def _required_string(input_data: dict, key: str) -> str:
    value = input_data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"input_data.{key} must be a non-empty string")
    return value
