"""Tests for voice chat API routing."""

from __future__ import annotations

import base64
import unittest

from src.api.routes import dispatch
from src.audio.service import AudioDependencies


class VoiceApiRoutesTest(unittest.TestCase):
    def test_post_voice_chat_returns_transcript_reply_and_audio(self) -> None:
        deps = AudioDependencies(
            stt_client=type(
                "S",
                (),
                {"transcribe": lambda self, audio, *, audio_format="pcm": "你好"},
            )(),
            tts_client=type("T", (), {"synthesize": lambda self, text: b"audio"})(),
            chat_handler=lambda conversation_id, message, *, dependencies=None: {
                "conversation_id": conversation_id,
                "request_id": "req-1",
                "turn_id": "turn-1",
                "reply": f"回复:{message}",
                "retrieval_status": "completed",
                "retrieved_memory_ids": [],
            },
        )

        status, body = dispatch(
            "POST",
            "/voice/chat",
            {
                "conversation_id": "conv-1",
                "audio_base64": base64.b64encode(b"pcm").decode("ascii"),
            },
            audio_dependencies=deps,
        )

        assert status == 200
        assert body["conversation_id"] == "conv-1"
        assert body["transcript"] == "你好"
        assert body["reply"] == "回复:你好"
        assert body["audio_base64"] == base64.b64encode(b"audio").decode("ascii")

    def test_post_voice_chat_can_disable_tts(self) -> None:
        tts_calls: list[str] = []
        deps = AudioDependencies(
            stt_client=type(
                "S",
                (),
                {"transcribe": lambda self, audio, *, audio_format="pcm": "你好"},
            )(),
            tts_client=type(
                "T",
                (),
                {"synthesize": lambda self, text: tts_calls.append(text) or b"audio"},
            )(),
            chat_handler=lambda conversation_id, message, *, dependencies=None: {
                "conversation_id": conversation_id,
                "request_id": "req-1",
                "turn_id": "turn-1",
                "reply": "回复",
                "retrieval_status": "completed",
                "retrieved_memory_ids": [],
            },
        )

        status, body = dispatch(
            "POST",
            "/voice/chat",
            {
                "conversation_id": "conv-1",
                "audio_base64": base64.b64encode(b"pcm").decode("ascii"),
                "tts_enabled": False,
            },
            audio_dependencies=deps,
        )

        assert status == 200
        assert body["audio_base64"] is None
        assert tts_calls == []

    def test_post_voice_chat_validates_body(self) -> None:
        status, body = dispatch("POST", "/voice/chat", {})

        assert status == 400
        assert "conversation_id" in body["error"]["message"]


if __name__ == "__main__":
    unittest.main()
