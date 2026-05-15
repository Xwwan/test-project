"""DashScope realtime TTS WebSocket client."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from urllib.parse import urlencode


@dataclass
class DashScopeTtsClient:
    api_key: str
    realtime_url: str
    model: str = "qwen3-tts-flash-realtime"
    voice: str = "Cherry"
    sample_rate: int = 24000
    timeout_seconds: float = 60.0

    def synthesize(self, text: str) -> bytes:
        """Synthesize text to raw PCM bytes."""

        if not isinstance(text, str) or not text.strip():
            return b""
        websocket = _load_websocket_client()
        url = f"{self.realtime_url}?{urlencode({'model': self.model})}"
        socket = websocket.create_connection(
            url,
            timeout=self.timeout_seconds,
            header=[f"Authorization: Bearer {self.api_key}"],
        )
        audio_parts: list[bytes] = []
        try:
            self._run_session(socket, text, audio_parts)
        finally:
            socket.close()
        return b"".join(audio_parts)

    def _run_session(self, socket: Any, text: str, audio_parts: list[bytes]) -> None:
        import base64

        while True:
            raw = socket.recv()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            message = json.loads(raw)
            msg_type = message.get("type")

            if msg_type == "session.created":
                socket.send(
                    json.dumps(
                        {
                            "type": "session.update",
                            "session": {
                                "voice": self.voice,
                                "response_format": "pcm",
                                "sample_rate": self.sample_rate,
                            },
                        },
                        ensure_ascii=False,
                    )
                )
                continue

            if msg_type == "session.updated":
                socket.send(
                    json.dumps(
                        {"type": "input_text_buffer.append", "text": text},
                        ensure_ascii=False,
                    )
                )
                socket.send(json.dumps({"type": "input_text_buffer.commit"}))
                continue

            if msg_type == "response.audio.delta":
                delta = message.get("delta")
                if isinstance(delta, str):
                    audio_parts.append(base64.b64decode(delta))
                continue

            if msg_type == "response.done":
                return

            if msg_type == "error":
                raise RuntimeError(f"DashScope TTS error: {message}")


def _load_websocket_client() -> Any:
    try:
        import websocket
    except ImportError as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError(
            "DashScope TTS requires websocket-client; install requirements.txt"
        ) from exc
    return websocket
