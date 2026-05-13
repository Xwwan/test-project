"""Single-step chat model adapters.

The rest of the application should call :func:`chat_once` with fully prepared
messages. Provider-specific HTTP details stay inside this package.
"""

from .chat import (
    AnthropicMessagesClient,
    ChatMessage,
    ChatResponse,
    ModelClient,
    OpenAIChatCompletionsClient,
    OpenAIResponsesClient,
    build_default_client,
    chat_once,
)

__all__ = [
    "AnthropicMessagesClient",
    "ChatMessage",
    "ChatResponse",
    "ModelClient",
    "OpenAIChatCompletionsClient",
    "OpenAIResponsesClient",
    "build_default_client",
    "chat_once",
]
