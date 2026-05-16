"""Tests for voice request/response schemas."""

from __future__ import annotations

import base64
import unittest

from src.api.schemas import SchemaError
from src.audio.schemas import (
    LiveVoiceAbortRequest,
    LiveVoiceChunkRequest,
    LiveVoiceFinishRequest,
    LiveVoiceStartRequest,
    LiveVoiceStartResponse,
    LiveVoiceTranscriptResponse,
    VoiceChatRequest,
    VoiceChatResponse,
)


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

    def test_live_voice_start_request_defaults_to_pcm_16k_mono(self) -> None:
        request = LiveVoiceStartRequest.from_dict({})

        assert request.sample_rate == 16000
        assert request.channels == 1
        assert request.audio_format == "pcm"

    def test_live_voice_start_request_rejects_unsupported_format(self) -> None:
        with self.assertRaises(SchemaError):
            LiveVoiceStartRequest.from_dict({"sample_rate": 48000})

    def test_live_voice_chunk_request_decodes_audio(self) -> None:
        request = LiveVoiceChunkRequest.from_dict(
            {
                "session_id": "live-1",
                "audio_base64": base64.b64encode(b"chunk").decode("ascii"),
                "is_final": False,
            }
        )

        assert request.session_id == "live-1"
        assert request.audio_bytes == b"chunk"
        assert request.is_final is False

    def test_live_voice_chunk_request_rejects_empty_base64_audio(self) -> None:
        with self.assertRaises(SchemaError):
            LiveVoiceChunkRequest.from_dict(
                {
                    "session_id": "live-1",
                    "audio_base64": base64.b64encode(b"").decode("ascii"),
                }
            )

    def test_live_voice_finish_and_abort_requests_validate_session(self) -> None:
        finish = LiveVoiceFinishRequest.from_dict(
            {"session_id": "live-1", "conversation_id": "conv-1", "tts_enabled": False}
        )
        abort = LiveVoiceAbortRequest.from_dict({"session_id": "live-1"})

        assert finish.session_id == "live-1"
        assert finish.conversation_id == "conv-1"
        assert finish.tts_enabled is False
        assert abort.session_id == "live-1"

    def test_live_voice_responses_serialize(self) -> None:
        start = LiveVoiceStartResponse(
            session_id="live-1",
            sample_rate=16000,
            channels=1,
            audio_format="pcm",
        )
        transcript = LiveVoiceTranscriptResponse(
            session_id="live-1",
            transcript="你好",
            is_final=False,
            error=None,
        )

        assert start.to_dict()["chunk_duration_ms"] == 160
        assert start.to_dict()["chunk_bytes"] == 5120
        assert transcript.to_dict() == {
            "session_id": "live-1",
            "transcript": "你好",
            "is_final": False,
            "error": None,
        }


if __name__ == "__main__":
    unittest.main()
