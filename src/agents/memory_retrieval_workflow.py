"""Memory Retrieval Workflow for Person 2."""

from __future__ import annotations

import json
import re
from typing import Any

from src.agents._prompting import (
    dump_prompt_debug,
    dumps_pretty,
    normalize_history,
    parse_json_object,
    read_prompt,
    section,
)
from src.models import ChatMessage, ModelClient, chat_once


STRATEGY = "llm_direct_judgement"
MAX_LIGHTWEIGHT_MEMORY_CANDIDATES = 80
MIN_CANDIDATE_SCORE = 2
_CHINESE_TEXT_PATTERN = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_TEXT_PATTERN = re.compile(r"[a-zA-Z0-9_]+")
_CHINESE_STOP_CHARS = set("的一是在了和有就都而及与着这那我你他她它们啊呀呢吧吗")

_TOPIC_EXPANSIONS = (
    (
        ("小时候", "童年", "济南", "家里", "不宽裕", "小时", "小时"),
        (
            "童年",
            "济南",
            "清平街",
            "升官街",
            "家境",
            "叔父",
            "母亲",
            "奶奶",
            "穷",
            "贫困",
            "高粱",
            "馒头",
            "庙会",
            "铁圈",
        ),
    ),
    (
        ("贪玩", "出去", "不想回家", "玩"),
        ("童年", "贪玩", "升官街", "铁圈", "庙会", "翻墙", "杂耍", "济南"),
    ),
    (
        ("书", "读", "翻", "眼睛", "舍不得放下"),
        ("读书", "闲书", "偷看", "手电筒", "藏书", "书", "眼睛", "写作"),
    ),
    (
        ("写文章", "下笔", "写作", "文章"),
        ("写作", "散文", "文章", "下笔", "封笔", "勤奋", "不辍"),
    ),
    (
        ("饭", "饭菜", "吃", "味道", "简单的饭菜"),
        ("饭", "馒头", "白面", "锅饼", "豆腐脑", "奶奶", "吃", "饮食"),
    ),
    (
        ("管闲事", "少管", "未必", "年纪"),
        ("晚年", "封笔", "继续", "写作", "服老", "勤奋", "不敢懈怠", "人生"),
    ),
    (
        ("晚上", "安静", "一辈子", "真快", "孤独"),
        ("母亲", "离家", "想家", "孤独", "亲人", "晚年", "人生", "回忆"),
    ),
    (
        ("荷花", "荷塘", "荷", "院子"),
        ("荷", "荷塘月色", "清塘荷韵", "自然", "散文", "晚年"),
    ),
    (
        ("猫", "通人情", "养的猫"),
        ("猫", "波斯猫", "季荷", "生活", "晚年"),
    ),
    (
        ("住院", "医生", "护士", "医护", "病房"),
        ("住院", "医生", "护士", "医护", "病房", "朋友", "三〇一医院"),
    ),
    (
        ("父亲", "婶母", "叔父", "母亲", "奶奶"),
        ("父亲", "婶母", "叔父", "母亲", "奶奶", "家境", "济南", "童年"),
    ),
)

_GREETING_ONLY_PATTERNS = (
    re.compile(r"^\s*(早啊|早上好|你好|在吗|你在吗)[，。！？!?\s]*(今天)?(你)?(在吗)?[，。！？!?\s]*$"),
)
_HIGH_RISK_NO_MEMORY_KEYWORDS = (
    "药量",
    "加一点药",
    "自己加药",
    "能不能自己",
    "要不要停",
    "停掉",
    "停药",
    "吃着有点犯困",
    "摔了一跤",
    "头晕",
    "站不稳",
    "不太站得稳",
    "打120",
    "急救",
    "稳赚不赔",
    "理财产品",
    "能买吗",
    "房子的事",
    "写个协议",
)
_REPEAT_SELF_REFERENCE_PHRASES = (
    "是不是又说过",
    "又说过这些",
    "说过这些旧事",
    "刚才是不是又说过",
)
_WEAK_OBSERVATION_KEYWORDS = ("窗外", "风", "树叶")
_WEAK_SMALL_TALK_KEYWORDS = ("泡了杯茶", "杯茶", "喝茶", "茶")
_PERSONAL_MEMORY_ANCHORS = (
    "想起",
    "小时",
    "童年",
    "以前",
    "当年",
    "记得",
    "家里",
    "老家",
    "小时候",
)

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

    if _should_skip_memory_retrieval(current_query):
        return {
            "request_id": request_id,
            "selected_memory_ids": [],
            "retrieval_reason": "query does not need memory retrieval",
            "needs_full_load": False,
            "strategy": STRATEGY,
        }

    candidate_items = _select_memory_candidates(
        current_query,
        lightweight_memory_items,
        limit=MAX_LIGHTWEIGHT_MEMORY_CANDIDATES,
    )
    if not candidate_items:
        return {
            "request_id": request_id,
            "selected_memory_ids": [],
            "retrieval_reason": "no locally relevant lightweight memory candidates",
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
                lightweight_memory_items=candidate_items,
            ),
        ),
    ]
    dump_prompt_debug(
        "memory.retrieval",
        request_id=request_id,
        recent_history=recent_history,
        messages=messages,
    )

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
        candidate_items,
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


def _should_skip_memory_retrieval(query: str) -> bool:
    normalized = " ".join(query.split())
    if not normalized:
        return True
    if any(pattern.match(normalized) for pattern in _GREETING_ONLY_PATTERNS):
        return True
    if any(phrase in normalized for phrase in _REPEAT_SELF_REFERENCE_PHRASES):
        return True
    if any(keyword in normalized for keyword in _HIGH_RISK_NO_MEMORY_KEYWORDS):
        return True
    if (
        any(keyword in normalized for keyword in _WEAK_OBSERVATION_KEYWORDS)
        and not any(anchor in normalized for anchor in _PERSONAL_MEMORY_ANCHORS)
    ):
        return True
    if (
        any(keyword in normalized for keyword in _WEAK_SMALL_TALK_KEYWORDS)
        and not any(anchor in normalized for anchor in _PERSONAL_MEMORY_ANCHORS)
    ):
        return True
    return False


def _select_memory_candidates(
    query: str,
    lightweight_memory_items: list[dict],
    *,
    limit: int,
) -> list[dict]:
    if len(lightweight_memory_items) <= limit:
        return lightweight_memory_items

    query_terms = _query_terms(query)
    if not query_terms:
        return []

    scored: list[tuple[float, int, dict]] = []
    for index, item in enumerate(lightweight_memory_items):
        if not isinstance(item, dict):
            continue
        score = _score_memory_candidate(query_terms, item)
        if score < MIN_CANDIDATE_SCORE:
            continue
        scored.append((score, index, item))

    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [item for _, _, item in scored[:limit]]


def _query_terms(query: str) -> set[str]:
    normalized = query.lower()
    terms: set[str] = set()
    for match in _CHINESE_TEXT_PATTERN.finditer(normalized):
        chunk = match.group(0)
        terms.update(
            chunk[index : index + length]
            for length in (2, 3, 4)
            for index in range(0, max(0, len(chunk) - length + 1))
            if _useful_chinese_term(chunk[index : index + length])
        )
    terms.update(_ASCII_TEXT_PATTERN.findall(normalized))

    for triggers, expansions in _TOPIC_EXPANSIONS:
        if any(trigger in query for trigger in triggers):
            terms.update(term.lower() for term in expansions)
    return terms


def _useful_chinese_term(term: str) -> bool:
    if not term:
        return False
    return any(char not in _CHINESE_STOP_CHARS for char in term)


def _score_memory_candidate(query_terms: set[str], item: dict) -> float:
    memory_text = _memory_search_text(item)
    if not memory_text:
        return 0

    score = 0.0
    for term in query_terms:
        if term and term in memory_text:
            score += 3 if len(term) >= 3 else 2

    memory_type = str(item.get("memory_type", "")).strip().lower()
    if memory_type in {"event", "relationship", "emotion_state", "belief_or_value", "work", "health"}:
        score += 0.25
    importance = item.get("importance")
    if isinstance(importance, (int, float)):
        score += min(max(float(importance), 0.0), 1.0)
    return score


def _memory_search_text(item: dict) -> str:
    parts: list[str] = []
    for key in ("summary", "memory_type"):
        value = item.get(key)
        if isinstance(value, str):
            parts.append(value)

    tags = item.get("tags_json", [])
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except json.JSONDecodeError:
            tags = [tags]
    if isinstance(tags, (list, tuple, set)):
        parts.extend(str(tag) for tag in tags)

    return " ".join(parts).lower()


def _is_int_like(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, str) and value.isdigit()


def _validate_string(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
