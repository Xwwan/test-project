"""Provider-compatible one-shot chat calls.

This module intentionally uses only the Python standard library so the MVP can
run without SDK installation. It supports OpenAI Responses, Anthropic Messages,
and OpenAI-compatible Chat Completions endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Protocol
from urllib import error, request


ALLOWED_ROLES = {"system", "developer", "user", "assistant"}

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_OPENAI_MODEL = "gpt-5.5"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-7"


@dataclass(frozen=True)
class ChatMessage:
    """One message sent to a chat model."""

    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in ALLOWED_ROLES:
            raise ValueError(f"unsupported chat role: {self.role!r}")
        if not isinstance(self.content, str):
            raise ValueError("chat message content must be a string")

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class ChatResponse:
    """Normalized single-response output from any supported provider."""

    content: str
    raw: dict
    model: str | None = None
    provider: str | None = None
    usage: dict | None = None


class ModelClient(Protocol):
    """Protocol implemented by all model clients and test fakes."""

    def chat(self, messages: list[ChatMessage], **kwargs: Any) -> ChatResponse:
        """Return one model response for the provided messages."""


def chat_once(
    messages: list[ChatMessage | dict],
    *,
    client: ModelClient | None = None,
    **kwargs: Any,
) -> ChatResponse:
    """Call exactly one chat completion using a provided or env-configured client."""

    normalized = [_coerce_message(message) for message in messages]
    if not normalized:
        raise ValueError("messages must contain at least one chat message")

    active_client = client or build_default_client()
    return active_client.chat(normalized, **kwargs)


def build_default_client() -> ModelClient:
    """Build a provider client from environment variables.

    Supported provider values:
    - ``openai``: OpenAI Responses API.
    - ``anthropic``: Anthropic Messages API.
    - ``openai_compatible``: Chat Completions-compatible gateways.
    """

    provider = (
        os.getenv("LLM_PROVIDER")
        or os.getenv("MODEL_PROVIDER")
        or _infer_provider_from_env()
    ).lower()

    timeout = _env_float("LLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)

    if provider == "openai":
        return OpenAIResponsesClient(
            api_key=_env_required("OPENAI_API_KEY", "LLM_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
            default_model=_first_env("OPENAI_MODEL", "LLM_MODEL") or DEFAULT_OPENAI_MODEL,
            timeout=timeout,
        )

    if provider == "anthropic":
        return AnthropicMessagesClient(
            api_key=_env_required("ANTHROPIC_API_KEY", "LLM_API_KEY"),
            base_url=os.getenv("ANTHROPIC_BASE_URL", DEFAULT_ANTHROPIC_BASE_URL),
            default_model=_first_env("ANTHROPIC_MODEL", "LLM_MODEL")
            or DEFAULT_ANTHROPIC_MODEL,
            timeout=timeout,
            anthropic_version=os.getenv(
                "ANTHROPIC_VERSION",
                DEFAULT_ANTHROPIC_VERSION,
            ),
        )

    if provider in {"openai_compatible", "chat_completions", "compatible"}:
        return OpenAIChatCompletionsClient(
            api_key=_env_required(
                "OPENAI_COMPATIBLE_API_KEY",
                "OPENAI_API_KEY",
                "LLM_API_KEY",
            ),
            base_url=os.getenv(
                "OPENAI_COMPATIBLE_BASE_URL",
                os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
            ),
            default_model=_first_env("OPENAI_COMPATIBLE_MODEL", "OPENAI_MODEL", "LLM_MODEL")
            or DEFAULT_OPENAI_MODEL,
            timeout=timeout,
        )

    raise ValueError(
        "unsupported model provider; set LLM_PROVIDER to openai, anthropic, "
        "or openai_compatible"
    )


class OpenAIResponsesClient:
    """OpenAI Responses API adapter."""

    provider = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        default_model: str = DEFAULT_OPENAI_MODEL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.timeout = timeout

    def chat(self, messages: list[ChatMessage], **kwargs: Any) -> ChatResponse:
        model = kwargs.get("model") or self.default_model
        payload: dict[str, Any] = {
            "model": model,
            "input": [message.to_dict() for message in messages],
        }

        _copy_if_present(kwargs, payload, "temperature")
        _copy_if_present(kwargs, payload, "max_output_tokens")
        if "max_tokens" in kwargs and "max_output_tokens" not in payload:
            payload["max_output_tokens"] = kwargs["max_tokens"]

        response_format = kwargs.get("response_format")
        if response_format:
            payload["text"] = {"format": response_format}

        raw = _post_json(
            f"{self.base_url}/responses",
            payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )
        return ChatResponse(
            content=_extract_openai_responses_text(raw),
            raw=raw,
            model=raw.get("model", model),
            provider=self.provider,
            usage=raw.get("usage"),
        )


class OpenAIChatCompletionsClient:
    """OpenAI-compatible Chat Completions adapter."""

    provider = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        default_model: str = DEFAULT_OPENAI_MODEL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.timeout = timeout

    def chat(self, messages: list[ChatMessage], **kwargs: Any) -> ChatResponse:
        model = kwargs.get("model") or self.default_model
        payload: dict[str, Any] = {
            "model": model,
            "messages": [message.to_dict() for message in messages],
        }

        _copy_if_present(kwargs, payload, "temperature")
        _copy_if_present(kwargs, payload, "max_tokens")
        _copy_if_present(kwargs, payload, "response_format")

        raw = _post_json(
            f"{self.base_url}/chat/completions",
            payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )
        return ChatResponse(
            content=_extract_chat_completions_text(raw),
            raw=raw,
            model=raw.get("model", model),
            provider=self.provider,
            usage=raw.get("usage"),
        )


class AnthropicMessagesClient:
    """Anthropic Messages API adapter."""

    provider = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_ANTHROPIC_BASE_URL,
        default_model: str = DEFAULT_ANTHROPIC_MODEL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        anthropic_version: str = DEFAULT_ANTHROPIC_VERSION,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.timeout = timeout
        self.anthropic_version = anthropic_version

    def chat(self, messages: list[ChatMessage], **kwargs: Any) -> ChatResponse:
        model = kwargs.get("model") or self.default_model
        system_text, anthropic_messages = _split_anthropic_messages(messages)

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": kwargs.get("max_tokens") or kwargs.get("max_output_tokens") or 4096,
            "messages": anthropic_messages,
        }
        if system_text:
            payload["system"] = system_text
        _copy_if_present(kwargs, payload, "temperature")

        raw = _post_json(
            f"{self.base_url}/v1/messages",
            payload,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": self.anthropic_version,
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )
        return ChatResponse(
            content=_extract_anthropic_text(raw),
            raw=raw,
            model=raw.get("model", model),
            provider=self.provider,
            usage=raw.get("usage"),
        )


def _coerce_message(message: ChatMessage | dict) -> ChatMessage:
    if isinstance(message, ChatMessage):
        return message
    if isinstance(message, dict):
        return ChatMessage(role=message.get("role"), content=message.get("content"))
    raise ValueError("each message must be a ChatMessage or a dictionary")


def _post_json(
    url: str,
    payload: dict,
    *,
    headers: dict[str, str],
    timeout: float,
) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    http_request = request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"model API HTTP {exc.code}: {error_body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"model API request failed: {exc.reason}") from exc

    try:
        decoded = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("model API returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("model API returned a non-object JSON response")
    return decoded


def _split_anthropic_messages(messages: list[ChatMessage]) -> tuple[str, list[dict]]:
    system_parts: list[str] = []
    api_messages: list[dict] = []
    for message in messages:
        if message.role in {"system", "developer"}:
            system_parts.append(message.content)
            continue
        api_messages.append({"role": message.role, "content": message.content})

    if not api_messages:
        api_messages.append({"role": "user", "content": ""})
    return "\n\n".join(part for part in system_parts if part), api_messages


def _extract_openai_responses_text(raw: dict) -> str:
    output_text = raw.get("output_text")
    if isinstance(output_text, str):
        return output_text

    parts: list[str] = []
    for item in raw.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") in {
                "output_text",
                "text",
            }:
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


def _extract_chat_completions_text(raw: dict) -> str:
    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    return content if isinstance(content, str) else ""


def _extract_anthropic_text(raw: dict) -> str:
    parts: list[str] = []
    for content in raw.get("content", []):
        if isinstance(content, dict) and content.get("type") == "text":
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _copy_if_present(source: dict[str, Any], target: dict[str, Any], key: str) -> None:
    if key in source and source[key] is not None:
        target[key] = source[key]


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _env_required(*names: str) -> str:
    value = _first_env(*names)
    if value:
        return value
    joined = ", ".join(names)
    raise ValueError(f"missing model API key; set one of: {joined}")


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _infer_provider_from_env() -> str:
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "openai"
