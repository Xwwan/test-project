import unittest
from unittest.mock import patch

from src.models import (
    AnthropicMessagesClient,
    build_default_client,
    ChatMessage,
    ChatResponse,
    OpenAIChatCompletionsClient,
    OpenAIResponsesClient,
    chat_once,
    chat_stream,
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

    def test_chat_stream_falls_back_to_one_shot_client(self):
        client = RecordingClient()

        chunks = list(
            chat_stream(
                [ChatMessage(role="user", content="Hello")],
                client=client,
            )
        )

        self.assertEqual(chunks, ["model reply"])

    def test_chat_stream_uses_client_stream_method(self):
        class StreamingClient(RecordingClient):
            def chat_stream(self, messages, **kwargs):
                self.calls.append({"messages": messages, "kwargs": kwargs})
                yield "你"
                yield "好"

        client = StreamingClient()

        chunks = list(
            chat_stream(
                [ChatMessage(role="user", content="Hello")],
                client=client,
                model="fake-model",
            )
        )

        self.assertEqual(chunks, ["你", "好"])
        self.assertEqual(client.calls[0]["kwargs"]["model"], "fake-model")

    def test_chat_message_rejects_unsupported_roles(self):
        with self.assertRaises(ValueError):
            ChatMessage(role="tool", content="No tools in one-shot chat")

    def test_build_default_client_uses_openai_yaml_config(self):
        config_path = self._write_config(
            """
providers:
  openai_test:
    type: openai
    api_key_env: TEST_OPENAI_API_KEY
    base_url: https://openai.example/v1
    timeout_seconds: 12
routes:
  default:
    default:
      provider: openai_test
      model: test-openai-model
"""
        )

        with patch("src.models.chat.get_config_value", return_value="test-key"):
            client = build_default_client(config_path)

        self.assertIsInstance(client, OpenAIResponsesClient)
        self.assertEqual(client.api_key, "test-key")
        self.assertEqual(client.base_url, "https://openai.example/v1")
        self.assertEqual(client.default_model, "test-openai-model")
        self.assertEqual(client.timeout, 12.0)

    def test_build_default_client_uses_anthropic_yaml_config(self):
        config_path = self._write_config(
            """
providers:
  anthropic_test:
    type: anthropic
    api_key_env: TEST_ANTHROPIC_API_KEY
    base_url: https://anthropic.example
    anthropic_version: "2024-01-01"
    timeout_seconds: 15
routes:
  default:
    default:
      provider: anthropic_test
      model: test-anthropic-model
"""
        )

        with patch("src.models.chat.get_config_value", return_value="test-key"):
            client = build_default_client(config_path)

        self.assertIsInstance(client, AnthropicMessagesClient)
        self.assertEqual(client.api_key, "test-key")
        self.assertEqual(client.base_url, "https://anthropic.example")
        self.assertEqual(client.default_model, "test-anthropic-model")
        self.assertEqual(client.timeout, 15.0)
        self.assertEqual(client.anthropic_version, "2024-01-01")

    def test_build_default_client_uses_openai_compatible_yaml_config(self):
        config_path = self._write_config(
            """
providers:
  compatible_test:
    type: openai-compatible
    api_key_env: TEST_COMPATIBLE_API_KEY
    base_url: https://gateway.example/v1
    timeout_seconds: 8
routes:
  default:
    default:
      provider: compatible_test
      model: test-compatible-model
"""
        )

        with patch("src.models.chat.get_config_value", return_value="test-key"):
            client = build_default_client(config_path)

        self.assertIsInstance(client, OpenAIChatCompletionsClient)
        self.assertEqual(client.api_key, "test-key")
        self.assertEqual(client.base_url, "https://gateway.example/v1")
        self.assertEqual(client.default_model, "test-compatible-model")
        self.assertEqual(client.timeout, 8.0)

    def test_build_default_client_uses_api_key_fallback_for_local_provider(self):
        config_path = self._write_config(
            """
providers:
  local_test:
    type: openai-compatible
    api_key_env: TEST_LOCAL_API_KEY
    api_key_fallback: not-needed
    base_url: http://localhost:12345/v1
routes:
  default:
    default:
      provider: local_test
      model: local-model
"""
        )

        with patch("src.models.chat.get_config_value", return_value=None):
            client = build_default_client(config_path)

        self.assertIsInstance(client, OpenAIChatCompletionsClient)
        self.assertEqual(client.api_key, "not-needed")
        self.assertEqual(client.base_url, "http://localhost:12345/v1")

    def test_build_default_client_requires_yaml_configured_api_key(self):
        config_path = self._write_config(
            """
providers:
  openai_test:
    type: openai
    api_key_env: TEST_OPENAI_API_KEY
routes:
  default:
    default:
      provider: openai_test
      model: test-openai-model
"""
        )

        with patch("src.models.chat.get_config_value", return_value=None):
            with self.assertRaises(ValueError):
                build_default_client(config_path)

    def test_chat_once_applies_route_defaults_when_client_is_injected(self):
        config_path = self._write_config(
            """
providers:
  openai_test:
    type: openai
    api_key_env: TEST_OPENAI_API_KEY
routes:
  dialogue:
    followup:
      provider: openai_test
      model: route-model
      temperature: 0.0
      response_format:
        type: json_object
"""
        )
        client = RecordingClient()

        chat_once(
            [ChatMessage(role="user", content="Hello")],
            client=client,
            route="dialogue.followup",
            config_path=config_path,
        )

        self.assertEqual(client.calls[0]["kwargs"]["temperature"], 0.0)
        self.assertEqual(
            client.calls[0]["kwargs"]["response_format"],
            {"type": "json_object"},
        )

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

    def test_openai_responses_client_streams_text_deltas(self):
        captured = {}

        def fake_post_json_stream(url, payload, *, headers, timeout):
            captured.update({"url": url, "payload": payload, "headers": headers})
            yield {"type": "response.output_text.delta", "delta": "你"}
            yield {"type": "response.output_text.delta", "delta": "好"}
            yield {"type": "response.completed"}

        client = OpenAIResponsesClient(api_key="key", base_url="https://api.example/v1")

        with patch("src.models.chat._post_json_stream", fake_post_json_stream):
            chunks = list(client.chat_stream([ChatMessage(role="user", content="Hello")]))

        self.assertEqual(chunks, ["你", "好"])
        self.assertEqual(captured["url"], "https://api.example/v1/responses")
        self.assertIs(captured["payload"]["stream"], True)

    def test_provider_clients_accept_dict_messages(self):
        captured = {}

        def fake_post_json(url, payload, *, headers, timeout):
            captured.update({"url": url, "payload": payload})
            return {"choices": [{"message": {"content": "ok"}}], "model": payload["model"]}

        client = OpenAIChatCompletionsClient(
            api_key="key",
            base_url="https://api.example/v1",
        )

        with patch("src.models.chat._post_json", fake_post_json):
            response = client.chat([{"role": "user", "content": "Return JSON"}])

        self.assertEqual(response.content, "ok")
        self.assertEqual(captured["url"], "https://api.example/v1/chat/completions")
        self.assertEqual(
            captured["payload"]["messages"],
            [{"role": "user", "content": "Return JSON"}],
        )

    def test_openai_compatible_client_streams_delta_content(self):
        captured = {}

        def fake_post_json_stream(url, payload, *, headers, timeout):
            captured.update({"url": url, "payload": payload})
            yield {"choices": [{"delta": {"content": "你"}}]}
            yield {"choices": [{"delta": {"content": "好"}}]}

        client = OpenAIChatCompletionsClient(
            api_key="key",
            base_url="https://api.example/v1",
        )

        with patch("src.models.chat._post_json_stream", fake_post_json_stream):
            chunks = list(client.chat_stream([{"role": "user", "content": "Hello"}]))

        self.assertEqual(chunks, ["你", "好"])
        self.assertEqual(captured["url"], "https://api.example/v1/chat/completions")
        self.assertIs(captured["payload"]["stream"], True)

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

    def test_anthropic_client_streams_text_deltas(self):
        captured = {}

        def fake_post_json_stream(url, payload, *, headers, timeout):
            captured.update({"url": url, "payload": payload})
            yield {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "你"},
            }
            yield {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "好"},
            }

        client = AnthropicMessagesClient(api_key="key", base_url="https://api.example")

        with patch("src.models.chat._post_json_stream", fake_post_json_stream):
            chunks = list(client.chat_stream([ChatMessage(role="user", content="Hello")]))

        self.assertEqual(chunks, ["你", "好"])
        self.assertEqual(captured["url"], "https://api.example/v1/messages")
        self.assertIs(captured["payload"]["stream"], True)

    def _write_config(self, content: str):
        self.addCleanup(self._cleanup_config_files)
        from pathlib import Path
        import tempfile

        if not hasattr(self, "_config_files"):
            self._config_files = []
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".yaml",
            delete=False,
        )
        with handle:
            handle.write(content.strip() + "\n")
        path = Path(handle.name)
        self._config_files.append(path)
        return path

    def _cleanup_config_files(self):
        for path in getattr(self, "_config_files", []):
            path.unlink(missing_ok=True)
        self._config_files = []


if __name__ == "__main__":
    unittest.main()
