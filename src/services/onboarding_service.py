"""Isolated five-stage onboarding workflow.

This module owns the onboarding state machine. It intentionally does not share
control flow with ``dialogue_service`` because onboarding has a different
lifecycle: staged information gathering, completion checks, and a final
handoff artifact for downstream profile or memory agents.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable
import uuid

from src.agents.onboarding_agent import (
    generate_onboarding_question,
    normalize_onboarding_agent_result,
    run_onboarding_step,
)
from src.agents.onboarding_streaming_agent import generate_onboarding_reply_stream
from src.onboarding import store as onboarding_store


SESSION_ACTIVE = "active"
SESSION_COMPLETED = "completed"
MIN_USER_TURNS_PER_STAGE = 1
ONBOARDING_SECTION_START = "<!-- onboarding-profile:start -->"
ONBOARDING_SECTION_END = "<!-- onboarding-profile:end -->"
FINAL_PAYLOAD_KIND = "onboarding_collected_profile"


ONBOARDING_STAGES: list[dict] = [
    {
        "id": 1,
        "key": "greeting",
        "name": "认识你",
        "goal": "了解怎么称呼用户、大概年纪、住在哪里",
        "required_slots": ["preferred_name", "age_or_life_stage", "location"],
        "first_question": "你好，我是 燕聆。先认识一下你吧，我平时怎么称呼你比较好？",
    },
    {
        "id": 2,
        "key": "family",
        "name": "家庭情况",
        "goal": "了解家里有谁、子女情况、是否独居",
        "required_slots": ["family_members", "children", "living_situation"],
        "first_question": "我也想了解一下你的家里情况，平时家里都有谁陪着你？",
    },
    {
        "id": 3,
        "key": "daily",
        "name": "日常生活",
        "goal": "了解每天怎么过、有什么爱好、常做什么",
        "required_slots": ["daily_routine", "hobbies", "frequent_activities"],
        "first_question": "那你平时一天大概怎么过？有没有什么常做的事或者爱好？",
    },
    {
        "id": 4,
        "key": "health",
        "name": "健康关注",
        "goal": "了解身体怎么样、有什么不舒服、睡眠饮食情况",
        "required_slots": ["health_status", "discomforts", "sleep_diet"],
        "first_question": "我再关心一下你的身体，最近睡眠、胃口和身体感觉都还好吗？",
    },
    {
        "id": 5,
        "key": "wishes",
        "name": "心愿期望",
        "goal": "了解最近有什么开心或烦心的事、希望聊什么话题",
        "required_slots": ["recent_mood_events", "worries", "preferred_topics"],
        "first_question": "最后我想听听你最近的心情，有没有开心的事、烦心的事，或者以后想和我聊的话题？",
    },
]


FIELD_LABELS = {
    "preferred_name": "称呼",
    "age_or_life_stage": "年龄或人生阶段",
    "location": "常住地",
    "family_members": "家庭成员",
    "children": "子女情况",
    "living_situation": "居住情况",
    "daily_routine": "日常安排",
    "hobbies": "兴趣爱好",
    "frequent_activities": "常做事项",
    "health_status": "身体状况",
    "discomforts": "不适或关注",
    "sleep_diet": "睡眠饮食",
    "recent_mood_events": "近期心情与事件",
    "worries": "烦心事",
    "preferred_topics": "希望聊的话题",
}

SLOT_FOLLOWUP_QUESTIONS = {
    "preferred_name": "我平时怎么称呼你比较好？",
    "age_or_life_stage": "我还想了解一下，你大概多大年纪呀？",
    "location": "你平时住在哪个城市，或者哪个地方呀？",
    "family_members": "你家里平时都有谁，能和我说说吗？",
    "children": "你有没有孩子，或者常联系的晚辈呀？",
    "living_situation": "那你现在是自己住，还是和家人一起住呀？",
    "daily_routine": "你平时一天大概怎么过呀？",
    "hobbies": "你平时有没有喜欢做的事，或者爱听爱看的东西？",
    "frequent_activities": "你平时常做的事是什么，比如散步、买菜、看电视这些？",
    "health_status": "最近身体整体感觉怎么样？",
    "discomforts": "最近有没有哪里不太舒服，或者需要我多留意的地方？",
    "sleep_diet": "最近睡得怎么样，胃口还好吗？",
    "recent_mood_events": "最近有没有让你开心，或者印象比较深的事？",
    "worries": "最近有没有什么烦心事，或者让你惦记的事？",
    "preferred_topics": "以后你想多和我聊些什么话题？",
}


RunStep = Callable[..., dict]
GenerateQuestion = Callable[..., str]
GenerateReplyStream = Callable[..., Any]
ReadModelProfile = Callable[[], str]
AppendTurn = Callable[[str, dict], str]
CreateSession = Callable[..., dict]
GetSession = Callable[[str], dict]
UpdateSession = Callable[..., dict]


@dataclass
class OnboardingDependencies:
    """Collaborators used by the onboarding workflow."""

    create_session: CreateSession | None = None
    get_session: GetSession | None = None
    update_session: UpdateSession | None = None
    run_step: RunStep | None = None
    generate_question: GenerateQuestion | None = None
    generate_reply_stream: GenerateReplyStream | None = None
    read_model_profile: ReadModelProfile | None = None
    append_turn: AppendTurn | None = None
    model_client: Any | None = None

    def with_overrides(self, **overrides: Any) -> "OnboardingDependencies":
        return replace(self, **overrides)

    def resolved(self) -> "OnboardingDependencies":
        deps = self
        if deps.create_session is None:
            deps = deps.with_overrides(create_session=onboarding_store.create_session)
        if deps.get_session is None:
            deps = deps.with_overrides(get_session=onboarding_store.get_session)
        if deps.update_session is None:
            deps = deps.with_overrides(update_session=onboarding_store.update_session)
        if deps.run_step is None:
            deps = deps.with_overrides(run_step=run_onboarding_step)
        if deps.generate_question is None:
            deps = deps.with_overrides(generate_question=generate_onboarding_question)
        if deps.generate_reply_stream is None:
            deps = deps.with_overrides(generate_reply_stream=generate_onboarding_reply_stream)
        if deps.read_model_profile is None:
            from src.persona.file_manager import read_model_profile

            deps = deps.with_overrides(read_model_profile=read_model_profile)
        return deps


def start_onboarding(
    conversation_id: str | None = None,
    *,
    dependencies: OnboardingDependencies | None = None,
) -> dict:
    """Start a new onboarding session and return the first prompt."""

    if conversation_id is not None and not isinstance(conversation_id, str):
        raise ValueError("conversation_id must be a string")

    deps = (dependencies or OnboardingDependencies()).resolved()
    session = deps.create_session(
        conversation_id=conversation_id or "",
        stage=1,
        collected={},
        turns=[],
        final_payload={},
    )
    stage = _stage_by_number(session["stage"])
    model_profile = deps.read_model_profile()
    reply = _generate_stage_question(
        deps,
        stage=stage,
        collected={},
        turns=[],
        model_profile=model_profile,
    )
    turns = _append_assistant_turn(
        deps,
        session,
        [],
        reply,
        stage=session["stage"],
        event="start",
    )
    session = deps.update_session(session["session_id"], turns=turns)
    return _response_payload(session, reply=reply)


def handle_onboarding_message(
    session_id: str,
    message: str,
    *,
    dependencies: OnboardingDependencies | None = None,
) -> dict:
    """Handle one user answer and advance the onboarding state machine."""

    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id must be a non-empty string")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message must be a non-empty string")

    deps = (dependencies or OnboardingDependencies()).resolved()
    prepared = prepare_onboarding_step(
        session_id,
        message,
        dependencies=deps,
    )
    if prepared["event"] == "stage_transition":
        generated = _generate_stage_question(
            deps,
            stage=prepared["target_stage"],
            collected=prepared["collected"],
            turns=prepared["turns"],
            model_profile=prepared["reply_input"].get("model_profile", ""),
            transition_from_stage=prepared["stage"],
        )
        prepared["reply_target"]["target_text"] = generated
    return complete_onboarding_step(
        prepared,
        prepared["reply_target"]["target_text"],
        dependencies=deps,
    )


def prepare_onboarding_step(
    session_id: str,
    message: str,
    *,
    dependencies: OnboardingDependencies | None = None,
) -> dict:
    """Run the non-streaming control step and return a pending reply plan."""

    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id must be a non-empty string")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message must be a non-empty string")

    deps = (dependencies or OnboardingDependencies()).resolved()
    session = deps.get_session(session_id)
    stage_number = int(session["stage"])
    stage = _stage_by_number(stage_number)
    if session["status"] != SESSION_ACTIVE:
        reply_target = {
            "kind": "already_completed",
            "target_text": "这次引导已经完成了，我们可以直接继续聊天。",
        }
        turns = list(session.get("turns") or [])
        collected = dict(session.get("collected") or {})
        state = {
            "session_id": session["session_id"],
            "stage": stage,
            "target_stage": stage,
            "collected": collected,
            "missing_required_slots": [],
            "status_after_reply": session["status"],
        }
        return {
            "session": session,
            "turns": turns,
            "stage": stage,
            "target_stage": stage,
            "target_stage_number": stage_number,
            "event": "already_completed",
            "collected": collected,
            "final_payload": dict(session.get("final_payload") or {}),
            "control_result": {},
            "missing_required_slots": [],
            "reply_target": reply_target,
            "reply_input": {
                "session_id": session["session_id"],
                "user_message": message,
                "model_profile": deps.read_model_profile(),
                "turns": turns,
                "state": state,
                "control_result": {},
                "reply_target": reply_target,
            },
        }

    turns = list(session.get("turns") or [])
    turns = _append_user_turn(deps, session, turns, message, stage=stage_number)
    model_profile = deps.read_model_profile()
    raw_result = deps.run_step(
        stage=stage_number,
        stage_goal=stage,
        collected=dict(session.get("collected") or {}),
        turns=turns,
        user_message=message,
        model_profile=model_profile,
        model_client=deps.model_client,
    )
    result = normalize_onboarding_agent_result(raw_result)
    prepared = apply_onboarding_control(
        session=session,
        turns=turns,
        stage=stage,
        control_result=result,
        user_message=message,
        model_profile=model_profile,
    )
    return prepared


def apply_onboarding_control(
    *,
    session: dict,
    turns: list[dict],
    stage: dict,
    control_result: dict,
    user_message: str,
    model_profile: str,
) -> dict:
    """Apply service-owned gates to the model control result."""

    stage_number = int(stage["id"])
    collected = _merge_collected(
        session.get("collected") or {},
        control_result["collected_patch"],
    )
    collected = _store_stage_summary(collected, stage, control_result.get("summary", ""))
    user_turn_count = _count_user_turns(turns, stage_number)
    missing_required_slots = _missing_required_slots(stage, collected)
    stage_complete = (
        bool(control_result["stage_complete"])
        and user_turn_count >= MIN_USER_TURNS_PER_STAGE
        and not missing_required_slots
    )
    final_payload: dict = {}

    if not stage_complete:
        target_stage_number = stage_number
        target_stage = stage
        event = "followup"
        reply_target = {
            "kind": "followup",
            "target_text": _followup_reply(stage, control_result, missing_required_slots),
            "missing_required_slots": missing_required_slots,
        }
    elif stage_number < len(ONBOARDING_STAGES):
        target_stage_number = stage_number + 1
        target_stage = _stage_by_number(target_stage_number)
        event = "stage_transition"
        reply_target = {
            "kind": "stage_transition",
            "target_text": control_result["next_question"] or target_stage["first_question"],
            "from_stage": stage,
            "to_stage": target_stage,
        }
    else:
        target_stage_number = stage_number
        target_stage = stage
        event = "completed"
        final_payload = _build_final_payload(collected)
        reply_target = {
            "kind": "completed",
            "target_text": (
                control_result["next_question"]
                or "我已经了解得差不多了，以后聊天我会尽量记住这些。"
            ),
        }

    state = {
        "session_id": session["session_id"],
        "stage": stage,
        "target_stage": target_stage,
        "collected": collected,
        "missing_required_slots": missing_required_slots,
        "status_after_reply": (
            SESSION_COMPLETED if event == "completed" else SESSION_ACTIVE
        ),
    }
    reply_input = {
        "session_id": session["session_id"],
        "user_message": user_message,
        "model_profile": model_profile,
        "turns": turns,
        "state": state,
        "control_result": control_result,
        "reply_target": reply_target,
    }
    return {
        "session": session,
        "turns": turns,
        "stage": stage,
        "target_stage": target_stage,
        "target_stage_number": target_stage_number,
        "event": event,
        "collected": collected,
        "final_payload": final_payload,
        "control_result": control_result,
        "missing_required_slots": missing_required_slots,
        "reply_target": reply_target,
        "reply_input": reply_input,
    }


def stream_onboarding_reply(
    prepared_step: dict,
    *,
    dependencies: OnboardingDependencies | None = None,
):
    """Yield true streaming user-visible onboarding reply deltas."""

    deps = (dependencies or OnboardingDependencies()).resolved()
    yield from deps.generate_reply_stream(
        prepared_step["reply_input"],
        model_client=deps.model_client,
    )


def complete_onboarding_step(
    prepared_step: dict,
    reply: str,
    *,
    dependencies: OnboardingDependencies | None = None,
) -> dict:
    """Persist assistant reply and final onboarding state after streaming."""

    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("reply must be a non-empty string")

    deps = (dependencies or OnboardingDependencies()).resolved()
    session = dict(prepared_step["session"])
    if session["status"] != SESSION_ACTIVE:
        return _response_payload(session, reply=reply.strip())

    turns = _append_assistant_turn(
        deps,
        session,
        list(prepared_step["turns"]),
        reply.strip(),
        stage=prepared_step["target_stage_number"],
        event=prepared_step["event"],
    )
    update_payload = {
        "stage": prepared_step["target_stage_number"],
        "collected": prepared_step["collected"],
        "turns": turns,
    }
    if prepared_step["event"] == "completed":
        update_payload["status"] = SESSION_COMPLETED
        update_payload["final_payload"] = prepared_step["final_payload"]

    session = deps.update_session(
        session["session_id"],
        **update_payload,
    )
    return _response_payload(session, reply=reply.strip())


def get_onboarding_status(
    session_id: str,
    *,
    dependencies: OnboardingDependencies | None = None,
) -> dict:
    """Return the current onboarding session status."""

    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id must be a non-empty string")
    deps = (dependencies or OnboardingDependencies()).resolved()
    session = deps.get_session(session_id)
    return _response_payload(session, reply="")


def _stage_by_number(stage: int) -> dict:
    for item in ONBOARDING_STAGES:
        if item["id"] == stage:
            return dict(item)
    raise ValueError("stage must be between 1 and 5")


def _merge_collected(current: dict, patch: dict) -> dict:
    merged = dict(current)
    for key, value in patch.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
            continue
        merged[key] = value
    return merged


def _store_stage_summary(collected: dict, stage: dict, summary: str) -> dict:
    if not summary:
        return collected
    summaries = dict(collected.get("stage_summaries") or {})
    summaries[str(stage["id"])] = {
        "name": stage["name"],
        "summary": summary,
    }
    collected["stage_summaries"] = summaries
    return collected


def _append_user_turn(
    deps: OnboardingDependencies,
    session: dict,
    turns: list[dict],
    message: str,
    *,
    stage: int,
) -> list[dict]:
    turn = _build_turn("user", message, stage=stage)
    return _append_turn(deps, session, turns, turn)


def _append_assistant_turn(
    deps: OnboardingDependencies,
    session: dict,
    turns: list[dict],
    reply: str,
    *,
    stage: int,
    event: str,
) -> list[dict]:
    turn = _build_turn("assistant", reply, stage=stage, event=event)
    return _append_turn(deps, session, turns, turn)


def _append_turn(
    deps: OnboardingDependencies,
    session: dict,
    turns: list[dict],
    turn: dict,
) -> list[dict]:
    updated = [*turns, turn]
    conversation_id = session.get("conversation_id")
    if conversation_id:
        if deps.append_turn is None:
            return updated
        deps.append_turn(
            conversation_id,
            {
                "turn_id": turn["turn_id"],
                "role": turn["role"],
                "content": turn["content"],
                "created_at": turn["created_at"],
                "metadata": {
                    "turn_kind": "onboarding",
                    "onboarding_session_id": session["session_id"],
                    "stage": turn["stage"],
                    "event": turn.get("event", ""),
                },
            },
        )
    return updated


def _build_turn(
    role: str,
    content: str,
    *,
    stage: int,
    event: str = "",
) -> dict:
    return {
        "turn_id": f"turn_{uuid.uuid4().hex}",
        "role": role,
        "content": content,
        "stage": stage,
        "event": event,
        "created_at": _utc_now_iso(),
    }


def _count_user_turns(turns: list[dict], stage: int) -> int:
    return sum(
        1
        for turn in turns
        if turn.get("role") == "user" and turn.get("stage") == stage
    )


def _fallback_followup(stage: dict) -> str:
    return f"我想再多了解一点，{stage['goal']}。你愿意多和我说说吗？"


def _generate_stage_question(
    deps: OnboardingDependencies,
    *,
    stage: dict,
    collected: dict,
    turns: list[dict],
    model_profile: str,
    transition_from_stage: dict | None = None,
) -> str:
    try:
        question = deps.generate_question(
            stage=stage["id"],
            stage_goal=stage,
            collected=collected,
            turns=turns,
            transition_from_stage=transition_from_stage,
            model_profile=model_profile,
            model_client=deps.model_client,
        )
    except Exception:
        question = ""
    if isinstance(question, str) and question.strip():
        return question.strip()
    return stage["first_question"]


def _followup_reply(stage: dict, result: dict, missing_slots: list[str]) -> str:
    if missing_slots and result.get("stage_complete"):
        return _missing_slot_followup(missing_slots)
    if result.get("next_question"):
        return result["next_question"]
    if missing_slots:
        return _missing_slot_followup(missing_slots)
    return _fallback_followup(stage)


def _missing_required_slots(stage: dict, collected: dict) -> list[str]:
    missing = []
    for slot in stage.get("required_slots") or []:
        if not _has_slot_value(collected.get(slot)):
            missing.append(slot)
    return missing


def _has_slot_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_has_slot_value(item) for item in value)
    if isinstance(value, dict):
        return any(_has_slot_value(item) for item in value.values())
    return True


def _missing_slot_followup(missing_slots: list[str]) -> str:
    for slot in missing_slots:
        question = SLOT_FOLLOWUP_QUESTIONS.get(slot)
        if question:
            return question
    return "我还想再了解一点，你愿意多和我说说吗？"


def _build_final_payload(collected: dict) -> dict:
    """Build the handoff data for future profile or memory agents."""

    profile_section = _render_onboarding_profile_section(collected)
    return {
        "kind": FINAL_PAYLOAD_KIND,
        "version": 1,
        "status": "ready_for_downstream_agent",
        "collected": dict(collected),
        "suggested_user_profile_section": profile_section,
        "suggested_user_profile_patch": {
            "operation": "append_or_replace_onboarding_section",
            "section_start": ONBOARDING_SECTION_START,
            "section_end": ONBOARDING_SECTION_END,
            "content": profile_section,
        },
    }


def build_user_profile_patch_preview(current_profile: str, collected: dict) -> dict:
    """Return the User.md patch a downstream agent may choose to apply."""

    section = _render_onboarding_profile_section(collected)
    start = current_profile.find(ONBOARDING_SECTION_START)
    end = current_profile.find(ONBOARDING_SECTION_END)
    if start != -1 and end != -1 and end > start:
        old = current_profile[start : end + len(ONBOARDING_SECTION_END)]
        return {"replace": {"old": old, "new": section}}
    if current_profile.strip():
        return {"append": f"\n{section}"}
    return {"content": section}


def _render_onboarding_profile_section(collected: dict) -> str:
    lines = [
        ONBOARDING_SECTION_START,
        "## 前置引导信息",
    ]

    summaries = collected.get("stage_summaries")
    if isinstance(summaries, dict) and summaries:
        lines.append("### 阶段概要")
        for stage in ONBOARDING_STAGES:
            item = summaries.get(str(stage["id"]))
            if not isinstance(item, dict):
                continue
            summary = item.get("summary")
            if isinstance(summary, str) and summary.strip():
                lines.append(f"- {stage['name']}：{summary.strip()}")

    structured_lines = []
    for key, label in FIELD_LABELS.items():
        value = collected.get(key)
        rendered = _render_value(value)
        if rendered:
            structured_lines.append(f"- {label}：{rendered}")
    if structured_lines:
        lines.append("### 结构化信息")
        lines.extend(structured_lines)

    lines.append(ONBOARDING_SECTION_END)
    return "\n".join(lines).strip() + "\n"


def _render_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return "、".join(items)
    if isinstance(value, dict):
        items = [
            f"{key}={item}"
            for key, item in value.items()
            if item is not None and str(item).strip()
        ]
        return "；".join(items)
    return str(value).strip()


def _response_payload(
    session: dict,
    *,
    reply: str,
) -> dict:
    stage = _stage_by_number(int(session["stage"]))
    payload = {
        "session_id": session["session_id"],
        "conversation_id": session.get("conversation_id", ""),
        "stage": session["stage"],
        "stage_key": stage["key"],
        "stage_name": stage["name"],
        "status": session["status"],
        "reply": reply,
        "collected": dict(session.get("collected") or {}),
        "missing_required_slots": _missing_required_slots(
            stage,
            dict(session.get("collected") or {}),
        ),
        "onboarding_complete": session["status"] == SESSION_COMPLETED,
        "profile_updated": False,
    }
    final_payload = session.get("final_payload") or {}
    if final_payload:
        payload["final_payload"] = dict(final_payload)
    return payload


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
