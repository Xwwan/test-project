"""Tests for voice request/response schemas."""

from __future__ import annotations

import base64
import unittest

from src.api.schemas import SchemaError
from src.audio.schemas import VoiceChatRequest, VoiceChatResponse


class AudioSchemasTest(unittest.TestCase):
    def test_voice_chat_request_decodes_base64_audio(self) -> None:
        request = VoiceChatRequest.from_dict(
            {
                "conversation_id": "conv-1",
                "audio_base64": base64.b64encode(b"pcm").decode("ascii"),
                "tts_enabled": True,
            }
        )

        assert request.conversation_id == "conv-1"
        assert request.audio_bytes == b"pcm"
        assert request.audio_format == "pcm"
        assert request.tts_enabled is True

    def test_voice_chat_request_rejects_invalid_base64(self) -> None:
        with self.assertRaises(SchemaError):
            VoiceChatRequest.from_dict(
                {"conversation_id": "conv-1", "audio_base64": "not base64"}
            )

    def test_voice_chat_response_serializes_optional_audio(self) -> None:
        response = VoiceChatResponse(
            conversation_id="conv-1",
            request_id="req-1",
            turn_id="turn-1",
            transcript="hello",
            reply="hi",
            retrieval_status="completed",
            retrieved_memory_ids=[1],
            audio_bytes=b"audio",
        )

        body = response.to_dict()

        assert body["audio_base64"] == base64.b64encode(b"audio").decode("ascii")
        assert body["audio_format"] == "pcm"

    def test_voice_chat_response_serializes_without_audio(self) -> None:
        response = VoiceChatResponse(
            conversation_id="conv-1",
            request_id="req-1",
            turn_id="turn-1",
            transcript="hello",
            reply="hi",
            retrieval_status="completed",
            retrieved_memory_ids=[],
            audio_bytes=None,
        )

        assert response.to_dict()["audio_base64"] is None


if __name__ == "__main__":
    unittest.main()
