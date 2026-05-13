import unittest
from unittest.mock import patch

from src.models import (
    AnthropicMessagesClient,
    ChatMessage,
    ChatResponse,
    OpenAIResponsesClient,
    chat_once,
)


class RecordingClient:
    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return ChatResponse(content="model reply", raw={"ok": True}, model="fake-model")


class ModelClientTest(unittest.TestCase):
    def test_chat_once_delegates_structured_messages_to_client(self):
        client = RecordingClient()

        response = chat_once(
            [
                ChatMessage(role="system", content="You are concise."),
                {"role": "user", "content": "Hello"},
            ],
            client=client,
            model="fake-model",
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        self.assertEqual(response.content, "model reply")
        self.assertEqual(client.calls[0]["messages"][0].role, "system")
        self.assertEqual(client.calls[0]["messages"][1].content, "Hello")
        self.assertEqual(client.calls[0]["kwargs"]["model"], "fake-model")
        self.assertEqual(client.calls[0]["kwargs"]["temperature"], 0.2)
        self.assertEqual(
            client.calls[0]["kwargs"]["response_format"],
            {"type": "json_object"},
        )

    def test_chat_once_requires_non_empty_messages(self):
        with self.assertRaises(ValueError):
            chat_once([], client=RecordingClient())

    def test_chat_message_rejects_unsupported_roles(self):
        with self.assertRaises(ValueError):
            ChatMessage(role="tool", content="No tools in one-shot chat")

    def test_openai_responses_client_sends_latest_default_model_and_json_format(self):
        captured = {}

        def fake_post_json(url, payload, *, headers, timeout):
            captured.update(
                {
                    "url": url,
                    "payload": payload,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
            return {"output_text": "{\"ok\": true}", "model": payload["model"]}

        client = OpenAIResponsesClient(api_key="key", base_url="https://api.example/v1")

        with patch("src.models.chat._post_json", fake_post_json):
            response = client.chat(
                [ChatMessage(role="user", content="Return JSON")],
                response_format={"type": "json_object"},
            )

        self.assertEqual(response.content, "{\"ok\": true}")
        self.assertEqual(captured["url"], "https://api.example/v1/responses")
        self.assertEqual(captured["payload"]["model"], "gpt-5.5")
        self.assertEqual(
            captured["payload"]["input"],
            [{"role": "user", "content": "Return JSON"}],
        )
        self.assertEqual(
            captured["payload"]["text"],
            {"format": {"type": "json_object"}},
        )
        self.assertEqual(captured["headers"]["Authorization"], "Bearer key")

    def test_anthropic_client_splits_system_prompt_and_uses_latest_default_model(self):
        captured = {}

        def fake_post_json(url, payload, *, headers, timeout):
            captured.update(
                {
                    "url": url,
                    "payload": payload,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
            return {
                "content": [{"type": "text", "text": "Claude reply"}],
                "model": payload["model"],
            }

        client = AnthropicMessagesClient(api_key="key", base_url="https://api.example")

        with patch("src.models.chat._post_json", fake_post_json):
            response = client.chat(
                [
                    ChatMessage(role="system", content="System prompt"),
                    ChatMessage(role="developer", content="Developer prompt"),
                    ChatMessage(role="user", content="Hello"),
                ]
            )

        self.assertEqual(response.content, "Claude reply")
        self.assertEqual(captured["url"], "https://api.example/v1/messages")
        self.assertEqual(captured["payload"]["model"], "claude-opus-4-7")
        self.assertEqual(
            captured["payload"]["system"],
            "System prompt\n\nDeveloper prompt",
        )
        self.assertEqual(
            captured["payload"]["messages"],
            [{"role": "user", "content": "Hello"}],
        )
        self.assertEqual(captured["headers"]["x-api-key"], "key")


if __name__ == "__main__":
    unittest.main()
