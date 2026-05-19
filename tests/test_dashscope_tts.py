"""Tests for the DashScope TTS WebSocket adapter."""

from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import patch

from src.audio.dashscope_tts import DashScopeTtsClient


class DashScopeTtsClientTest(unittest.TestCase):
    def test_synthesize_stream_yields_audio_deltas(self) -> None:
        socket = FakeSocket(
            [
                {"type": "session.created"},
                {"type": "session.updated"},
                {
                    "type": "response.audio.delta",
                    "delta": base64.b64encode(b"chunk-a").decode("ascii"),
                },
                {
                    "type": "response.audio.delta",
                    "delta": base64.b64encode(b"chunk-b").decode("ascii"),
                },
                {"type": "response.done"},
            ]
        )
        websocket = FakeWebsocket(socket)
        client = DashScopeTtsClient(
            api_key="key",
            realtime_url="wss://example.invalid/tts",
            model="test-model",
            voice="Cherry",
            sample_rate=24000,
            timeout_seconds=12,
        )

        with patch("src.audio.dashscope_tts._load_websocket_client", lambda: websocket):
            chunks = list(client.synthesize_stream("你好"))

        assert chunks == [b"chunk-a", b"chunk-b"]
        assert websocket.created_url == "wss://example.invalid/tts?model=test-model"
        assert websocket.created_timeout == 12
        assert websocket.created_header == ["Authorization: Bearer key"]
        assert socket.closed is True
        sent_types = [json.loads(item)["type"] for item in socket.sent]
        assert sent_types == [
            "session.update",
            "input_text_buffer.append",
            "input_text_buffer.commit",
        ]

    def test_synthesize_joins_stream_chunks(self) -> None:
        socket = FakeSocket(
            [
                {"type": "session.created"},
                {"type": "session.updated"},
                {
                    "type": "response.audio.delta",
                    "delta": base64.b64encode(b"hello").decode("ascii"),
                },
                {
                    "type": "response.audio.delta",
                    "delta": base64.b64encode(b" world").decode("ascii"),
                },
                {"type": "response.done"},
            ]
        )
        websocket = FakeWebsocket(socket)
        client = DashScopeTtsClient(
            api_key="key",
            realtime_url="wss://example.invalid/tts",
        )

        with patch("src.audio.dashscope_tts._load_websocket_client", lambda: websocket):
            audio = client.synthesize("hello")

        assert audio == b"hello world"


class FakeWebsocket:
    def __init__(self, socket: "FakeSocket") -> None:
        self.socket = socket
        self.created_url: str | None = None
        self.created_timeout: float | None = None
        self.created_header: list[str] | None = None

    def create_connection(self, url: str, *, timeout: float, header: list[str]) -> "FakeSocket":
        self.created_url = url
        self.created_timeout = timeout
        self.created_header = list(header)
        return self.socket


class FakeSocket:
    def __init__(self, messages: list[dict]) -> None:
        self.messages = [json.dumps(message) for message in messages]
        self.sent: list[str] = []
        self.closed = False

    def recv(self) -> str:
        if not self.messages:
            raise AssertionError("unexpected recv")
        return self.messages.pop(0)

    def send(self, payload: str) -> None:
        self.sent.append(payload)

    def close(self) -> None:
        self.closed = True


if __name__ == "__main__":
    unittest.main()
