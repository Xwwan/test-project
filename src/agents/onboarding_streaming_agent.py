"""Streaming user-facing reply agent for onboarding."""

from __future__ import annotations

from typing import Any, Iterator

from src.agents._prompting import dump_prompt_debug, read_prompt, section
from src.models import ChatMessage, ModelClient, chat_stream


PROMPT_FILENAME = "onboarding_agent.md"
DEFAULT_ONBOARDING_STREAMING_PROMPT = """你是 燕聆 的前置引导回复 Agent。
你只负责生成用户听得到的自然中文回复。
不要输出 JSON，不要输出 markdown，不要提阶段、字段、档案或收集资料。
回复要口语、简短、温暖，适合语音播放。
"""


def generate_onboarding_reply_stream(
    input_data: dict,
    *,
    model_client: ModelClient | None = None,
    model: str | None = None,
) -> Iterator[str]:
    """Yield visible onboarding reply deltas from a streaming LLM."""

    _validate_input_data(input_data)
    prompt = "\n\n".join(
        [
            read_prompt(PROMPT_FILENAME, DEFAULT_ONBOARDING_STREAMING_PROMPT),
            DEFAULT_ONBOARDING_STREAMING_PROMPT,
            (
                "服务端已经完成结构化判断。你必须按 Reply Target 生成一句自然回复；"
                "不要自己改变阶段判断，不要补充额外问题。"
            ),
        ]
    )
    messages = [
        ChatMessage(role="system", content=prompt),
        ChatMessage(
            role="user",
            content="\n\n".join(
                [
                    section("Model.md", input_data.get("model_profile", "")),
                    section("Onboarding State", input_data.get("state", {})),
                    section("Recent Turns", input_data.get("turns", [])),
                    section("Latest User Message", input_data.get("user_message", "")),
                    section("Control Result", input_data.get("control_result", {})),
                    section("Reply Target", input_data.get("reply_target", {})),
                ]
            ),
        ),
    ]
    dump_prompt_debug(
        "onboarding.reply.stream",
        request_id="",
        recent_history=input_data.get("turns", []),
        messages=messages,
    )
    yield from chat_stream(
        messages,
        client=model_client,
        model=model,
        temperature=0.5,
    )


def _validate_input_data(input_data: Any) -> None:
    if not isinstance(input_data, dict):
        raise ValueError("input_data must be a dictionary")
    reply_target = input_data.get("reply_target")
    if not isinstance(reply_target, dict):
        raise ValueError("reply_target must be a dictionary")
    target_text = reply_target.get("target_text")
    if not isinstance(target_text, str) or not target_text.strip():
        raise ValueError("reply_target.target_text must be a non-empty string")
