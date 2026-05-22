"""LLM agent for the isolated onboarding workflow."""

from __future__ import annotations

from typing import Any

from src.agents._prompting import (
    dump_prompt_debug,
    parse_json_object,
    read_prompt,
    section,
)
from src.models import ChatMessage, ModelClient, OpenAIChatCompletionsClient, chat_once
from src.utils.env import get_config_value


PROMPT_FILENAME = "onboarding_agent.md"
DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"

DEFAULT_ONBOARDING_PROMPT = """你是 燕聆 的前置引导 Agent。
你的任务是通过自然中文对话收集长期用户信息，并只返回 JSON。
不要输出 markdown，不要输出 JSON 以外的文字。
"""


def run_onboarding_step(
    *,
    stage: int,
    stage_goal: dict,
    collected: dict,
    turns: list[dict],
    user_message: str,
    model_profile: str = "",
    model_client: ModelClient | None = None,
    model: str | None = None,
) -> dict:
    """Ask the model whether a stage is complete and what to ask next."""

    if not isinstance(stage, int) or stage < 1 or stage > 5:
        raise ValueError("stage must be between 1 and 5")
    if not isinstance(stage_goal, dict):
        raise ValueError("stage_goal must be a dictionary")
    if not isinstance(collected, dict):
        raise ValueError("collected must be a dictionary")
    if not isinstance(turns, list):
        raise ValueError("turns must be a list")
    if not isinstance(user_message, str) or not user_message.strip():
        raise ValueError("user_message must be a non-empty string")
    if not isinstance(model_profile, str):
        raise ValueError("model_profile must be a string")

    prompt = read_prompt(PROMPT_FILENAME, DEFAULT_ONBOARDING_PROMPT)
    system_prompt = "\n\n".join(
        [
            prompt,
            section("Model.md", model_profile),
        ]
    )
    payload = {
        "stage": stage,
        "stage_goal": stage_goal,
        "collected": collected,
        "turns": turns,
        "user_message": user_message,
    }
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(
            role="user",
            content="\n\n".join(
                [
                    section("Onboarding Input", payload),
                    section(
                        "Required JSON Output",
                        {
                            "stage_complete": False,
                            "onboarding_complete": False,
                            "next_question": "string",
                            "collected_patch": {},
                            "confidence": 0.8,
                            "reason": "string",
                            "summary": "string",
                        },
                    ),
                ]
            ),
        ),
    ]
    dump_prompt_debug(
        "onboarding.step",
        request_id="",
        recent_history=turns,
        messages=messages,
    )
    response = chat_once(
        messages,
        client=model_client or build_deepseek_client(),
        model=model or DEFAULT_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
    )
    payload = parse_json_object(response.content)
    return normalize_onboarding_agent_result(payload)


def generate_onboarding_question(
    *,
    stage: int,
    stage_goal: dict,
    collected: dict,
    turns: list[dict],
    transition_from_stage: dict | None = None,
    model_profile: str = "",
    model_client: ModelClient | None = None,
    model: str | None = None,
) -> str:
    """Generate a natural question for a new stage or transition."""

    if not isinstance(stage, int) or stage < 1 or stage > 5:
        raise ValueError("stage must be between 1 and 5")
    if not isinstance(stage_goal, dict):
        raise ValueError("stage_goal must be a dictionary")
    if not isinstance(collected, dict):
        raise ValueError("collected must be a dictionary")
    if not isinstance(turns, list):
        raise ValueError("turns must be a list")
    if transition_from_stage is not None and not isinstance(transition_from_stage, dict):
        raise ValueError("transition_from_stage must be a dictionary")
    if not isinstance(model_profile, str):
        raise ValueError("model_profile must be a string")

    prompt = read_prompt(PROMPT_FILENAME, DEFAULT_ONBOARDING_PROMPT)
    system_prompt = "\n\n".join(
        [
            prompt,
            section("Model.md", model_profile),
        ]
    )
    payload = {
        "stage": stage,
        "stage_goal": stage_goal,
        "transition_from_stage": transition_from_stage or {},
        "collected": collected,
        "turns": turns,
        "instruction": (
            "根据当前已收集信息和最近对话，生成一句自然、口语的下一问。"
            "要承接用户刚刚说的话，再轻轻带到当前阶段目标；不要照抄 first_question。"
        ),
    }
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(
            role="user",
            content="\n\n".join(
                [
                    section("Question Generation Input", payload),
                    section(
                        "Required JSON Output",
                        {
                            "next_question": "一句自然要问用户的话",
                            "reason": "简短说明",
                        },
                    ),
                ]
            ),
        ),
    ]
    dump_prompt_debug(
        "onboarding.question",
        request_id="",
        recent_history=turns,
        messages=messages,
    )
    response = chat_once(
        messages,
        client=model_client or build_deepseek_client(),
        model=model or DEFAULT_MODEL,
        temperature=0.4,
        response_format={"type": "json_object"},
    )
    payload = parse_json_object(response.content)
    return normalize_onboarding_question_result(payload)


def build_deepseek_client() -> ModelClient:
    """Build the onboarding-only DeepSeek official API client."""

    api_key = get_config_value(DEEPSEEK_API_KEY_ENV)
    if not api_key:
        raise ValueError(f"missing DeepSeek API key; set {DEEPSEEK_API_KEY_ENV}")
    return OpenAIChatCompletionsClient(
        api_key=api_key,
        base_url=DEEPSEEK_BASE_URL,
        default_model=DEFAULT_MODEL,
        provider_name="deepseek_official",
    )


def normalize_onboarding_question_result(raw: Any) -> str:
    if not isinstance(raw, dict):
        raise ValueError("onboarding question result must be a dictionary")
    question = raw.get("next_question") or raw.get("reply") or ""
    if not isinstance(question, str):
        raise ValueError("next_question must be a string")
    question = question.strip()
    if not question:
        raise ValueError("next_question must be a non-empty string")
    return question


def normalize_onboarding_agent_result(raw: Any) -> dict:
    """Validate and normalize model JSON into the service contract."""

    if not isinstance(raw, dict):
        raise ValueError("onboarding agent result must be a dictionary")

    collected_patch = raw.get("collected_patch") or {}
    if not isinstance(collected_patch, dict):
        raise ValueError("collected_patch must be a dictionary")

    next_question = raw.get("next_question") or raw.get("reply") or ""
    if not isinstance(next_question, str):
        raise ValueError("next_question must be a string")

    confidence = raw.get("confidence", 0.0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = 0.0
    confidence = max(0.0, min(1.0, float(confidence)))

    reason = raw.get("reason") or ""
    if not isinstance(reason, str):
        reason = str(reason)

    summary = raw.get("summary") or ""
    if not isinstance(summary, str):
        summary = str(summary)

    return {
        "stage_complete": bool(raw.get("stage_complete", False)),
        "onboarding_complete": bool(raw.get("onboarding_complete", False)),
        "next_question": next_question.strip(),
        "collected_patch": collected_patch,
        "confidence": confidence,
        "reason": reason.strip(),
        "summary": summary.strip(),
    }
