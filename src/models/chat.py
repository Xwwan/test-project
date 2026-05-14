"""Provider-compatible one-shot chat calls.

It supports OpenAI Responses, Anthropic Messages, and OpenAI-compatible Chat
Completions endpoints. Default provider settings are read from
``config/app.yaml``. Provider connection settings live under ``providers``;
per-use-case model settings live under ``routes``. Secrets are still resolved
from environment variables or the project ``.env`` file.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Protocol
from urllib import error, request

from src.utils.config import load_app_config
from src.utils.env import get_config_value


ALLOWED_ROLES = {"system", "developer", "user", "assistant"}

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_OPENAI_MODEL = "gpt-5.5"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-7"
DEFAULT_ROUTE = "default.default"
ROUTE_CHAT_KWARGS = {
    "max_output_tokens",
    "max_tokens",
    "response_format",
    "temperature",
}


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
    route: str | None = None,
    config_path: str | Path | None = None,
    **kwargs: Any,
) -> ChatResponse:
    """Call exactly one chat completion using a provided or configured client."""

    normalized = [_coerce_message(message) for message in messages]
    if not normalized:
        raise ValueError("messages must contain at least one chat message")

    route_kwargs = _route_chat_kwargs(config_path, route) if route else {}
    active_client = client or build_default_client(config_path=config_path, route=route)
    return active_client.chat(normalized, **{**route_kwargs, **kwargs})


def build_default_client(
    config_path: str | Path | None = None,
    route: str | None = None,
) -> ModelClient:
    """Build a provider client for a configured route.

    Supported provider ``type`` values:
    - ``openai``: OpenAI Responses API.
    - ``anthropic``: Anthropic Messages API.
    - ``openai-compatible``: Chat Completions-compatible gateways.
    """

    app_config = load_app_config(config_path)
    route_path, route_config = _resolve_route_config(app_config, route)
    provider_name = _config_string(route_config, "provider", "")
    provider_config = _resolve_provider_config(app_config, provider_name)
    provider_type = _normalize_provider_type(
        _config_string(provider_config, "type", "openai")
    )
    timeout = _route_or_provider_float(
        route_config=route_config,
        provider_config=provider_config,
        key="timeout_seconds",
        default=DEFAULT_TIMEOUT_SECONDS,
        route_path=route_path,
        provider_name=provider_name,
    )

    if provider_type == "openai":
        return OpenAIResponsesClient(
            api_key=_api_key_from_config(
                provider_config,
                "OPENAI_API_KEY",
                "LLM_API_KEY",
            ),
            base_url=_config_string(
                provider_config,
                "base_url",
                DEFAULT_OPENAI_BASE_URL,
            ),
            default_model=_config_string(
                route_config,
                "model",
                DEFAULT_OPENAI_MODEL,
            ),
            timeout=timeout,
        )

    if provider_type == "anthropic":
        return AnthropicMessagesClient(
            api_key=_api_key_from_config(
                provider_config,
                "ANTHROPIC_API_KEY",
                "LLM_API_KEY",
            ),
            base_url=_config_string(
                provider_config,
                "base_url",
                DEFAULT_ANTHROPIC_BASE_URL,
            ),
            default_model=_config_string(
                route_config,
                "model",
                DEFAULT_ANTHROPIC_MODEL,
            ),
            timeout=timeout,
            anthropic_version=_config_string(
                provider_config,
                "anthropic_version",
                DEFAULT_ANTHROPIC_VERSION,
            ),
        )

    if provider_type == "openai_compatible":
        return OpenAIChatCompletionsClient(
            api_key=_api_key_from_config(
                provider_config,
                "OPENAI_COMPATIBLE_API_KEY",
                "OPENAI_API_KEY",
                "LLM_API_KEY",
            ),
            base_url=_config_string(
                provider_config,
                "base_url",
                DEFAULT_OPENAI_BASE_URL,
            ),
            default_model=_config_string(
                route_config,
                "model",
                DEFAULT_OPENAI_MODEL,
            ),
            timeout=timeout,
        )

    raise ValueError(
        "unsupported provider type in config/app.yaml; use openai, anthropic, "
        "or openai-compatible"
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

    def chat(self, messages: list[ChatMessage | dict], **kwargs: Any) -> ChatResponse:
        normalized = [_coerce_message(message) for message in messages]
        model = kwargs.get("model") or self.default_model
        payload: dict[str, Any] = {
            "model": model,
            "input": [message.to_dict() for message in normalized],
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

    def chat(self, messages: list[ChatMessage | dict], **kwargs: Any) -> ChatResponse:
        normalized = [_coerce_message(message) for message in messages]
        model = kwargs.get("model") or self.default_model
        payload: dict[str, Any] = {
            "model": model,
            "messages": [message.to_dict() for message in normalized],
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

    def chat(self, messages: list[ChatMessage | dict], **kwargs: Any) -> ChatResponse:
        normalized = [_coerce_message(message) for message in messages]
        model = kwargs.get("model") or self.default_model
        system_text, anthropic_messages = _split_anthropic_messages(normalized)

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


def _route_chat_kwargs(
    config_path: str | Path | None,
    route: str | None,
) -> dict[str, Any]:
    app_config = load_app_config(config_path)
    _, route_config = _resolve_route_config(app_config, route)
    return {
        key: route_config[key]
        for key in ROUTE_CHAT_KWARGS
        if key in route_config and route_config[key] is not None
    }


def _resolve_route_config(
    app_config: dict[str, Any],
    route: str | None,
) -> tuple[str, dict[str, Any]]:
    routes = _mapping_config(app_config.get("routes"), "routes")
    route_name, variant_name = _split_route_name(route or DEFAULT_ROUTE)
    route_group = routes.get(route_name)
    if route_group is None:
        raise ValueError(f"unknown model route: {route_name!r}")
    route_group = _mapping_config(route_group, f"routes.{route_name}")

    if "provider" in route_group:
        return f"routes.{route_name}", route_group

    route_config = route_group.get(variant_name)
    if route_config is None:
        raise ValueError(f"unknown model route: {route_name}.{variant_name}")
    return (
        f"routes.{route_name}.{variant_name}",
        _mapping_config(route_config, f"routes.{route_name}.{variant_name}"),
    )


def _split_route_name(route: str) -> tuple[str, str]:
    if not isinstance(route, str) or not route:
        raise ValueError("model route must be a non-empty string")
    if "." not in route:
        return route, "default"
    route_name, variant_name = route.split(".", 1)
    if not route_name or not variant_name:
        raise ValueError("model route must look like 'route' or 'route.variant'")
    return route_name, variant_name


def _resolve_provider_config(
    app_config: dict[str, Any],
    provider_name: str,
) -> dict[str, Any]:
    providers = _mapping_config(app_config.get("providers"), "providers")
    provider_config = providers.get(provider_name)
    if provider_config is None:
        raise ValueError(f"unknown model provider: {provider_name!r}")
    return _mapping_config(provider_config, f"providers.{provider_name}")


def _normalize_provider_type(provider_type: str) -> str:
    normalized = provider_type.lower().replace("-", "_")
    if normalized in {"openai_compatible", "chat_completions", "compatible"}:
        return "openai_compatible"
    return normalized


def _route_or_provider_float(
    *,
    route_config: dict[str, Any],
    provider_config: dict[str, Any],
    key: str,
    default: float,
    route_path: str,
    provider_name: str,
) -> float:
    if key in route_config:
        return _config_float(route_config, key, default, f"{route_path}.{key}")
    return _config_float(
        provider_config,
        key,
        default,
        f"providers.{provider_name}.{key}",
    )


def _mapping_config(value: Any, path: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a YAML mapping")
    return value


def _config_string(config: dict[str, Any], key: str, default: str) -> str:
    value = config.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError(f"llm config value {key!r} must be a non-empty string")
    return value


def _config_float(
    config: dict[str, Any],
    key: str,
    default: float,
    path: str,
) -> float:
    value = config.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{path} must be a number")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"{path} must be a number") from exc
    raise ValueError(f"{path} must be a number")


def _api_key_from_config(
    provider_config: dict[str, Any],
    *fallback_env_names: str,
) -> str:
    env_names = _api_key_env_names(provider_config, fallback_env_names)
    for env_name in env_names:
        value = get_config_value(env_name)
        if value:
            return value
    fallback = provider_config.get("api_key_fallback")
    if isinstance(fallback, str) and fallback:
        return fallback
    if fallback is not None:
        raise ValueError("api_key_fallback must be a non-empty string")
    joined = ", ".join(env_names)
    raise ValueError(f"missing model API key; set one of: {joined}")


def _api_key_env_names(
    provider_config: dict[str, Any],
    fallback_env_names: tuple[str, ...],
) -> tuple[str, ...]:
    configured = provider_config.get("api_key_env")
    if configured is None:
        return _unique_env_names(fallback_env_names)
    if isinstance(configured, str) and configured:
        return _unique_env_names((configured, *fallback_env_names))
    if isinstance(configured, list):
        names: list[str] = []
        for value in configured:
            if not isinstance(value, str) or not value:
                raise ValueError("api_key_env entries must be non-empty strings")
            names.append(value)
        return _unique_env_names((*names, *fallback_env_names))
    raise ValueError("api_key_env must be a non-empty string or list of strings")


def _unique_env_names(names: tuple[str, ...]) -> tuple[str, ...]:
    unique: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        unique.append(name)
        seen.add(name)
    return tuple(unique)
