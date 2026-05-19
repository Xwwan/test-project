"""Tests for voice-chat service orchestration."""

from __future__ import annotations

import unittest
from typing import Any

from src.audio.service import (
    AudioDependencies,
    handle_voice_chat,
    handle_voice_reply_from_text,
)


class FakeStt:
    def __init__(self, transcript: str = "用户语音") -> None:
        self.transcript = transcript
        self.last_audio: bytes | None = None
        self.last_audio_format: str | None = None

    def transcribe(self, audio: bytes, *, audio_format: str = "pcm") -> str:
        self.last_audio = audio
        self.last_audio_format = audio_format
        return self.transcript


class FakeTts:
    def __init__(self) -> None:
        self.last_text: str | None = None

    def synthesize(self, text: str) -> bytes:
        self.last_text = text
        return b"voice"


class VoiceServiceTest(unittest.TestCase):
    def test_handle_voice_chat_runs_stt_chat_and_tts(self) -> None:
        stt = FakeStt()
        tts = FakeTts()
        calls: list[tuple[str, str, Any]] = []

        def chat(conversation_id: str, message: str, *, dependencies=None) -> dict:
            calls.append((conversation_id, message, dependencies))
            return {
                "conversation_id": conversation_id,
                "request_id": "req-1",
                "turn_id": "turn-1",
                "reply": "模型回复",
                "retrieval_status": "completed",
                "retrieved_memory_ids": [2],
            }

        dialogue_deps = object()
        result = handle_voice_chat(
            "conv-1",
            b"pcm",
            dialogue_dependencies=dialogue_deps,
            dependencies=AudioDependencies(
                stt_client=stt,
                tts_client=tts,
                chat_handler=chat,
            ),
        )

        assert result.transcript == "用户语音"
        assert result.reply == "模型回复"
        assert result.audio_bytes == b"voice"
        assert result.retrieved_memory_ids == [2]
        assert stt.last_audio == b"pcm"
        assert stt.last_audio_format == "pcm"
        assert calls == [("conv-1", "用户语音", dialogue_deps)]
        assert tts.last_text == "模型回复"

    def test_handle_voice_chat_skips_tts_when_disabled(self) -> None:
        tts = FakeTts()
        result = handle_voice_chat(
            "conv-1",
            b"pcm",
            tts_enabled=False,
            dependencies=AudioDependencies(
                stt_client=FakeStt(),
                tts_client=tts,
                chat_handler=lambda conversation_id, message, *, dependencies=None: {
                    "conversation_id": conversation_id,
                    "request_id": "req-1",
                    "turn_id": "turn-1",
                    "reply": "模型回复",
                    "retrieval_status": "completed",
                    "retrieved_memory_ids": [],
                },
            ),
        )

        assert result.audio_bytes is None
        assert tts.last_text is None

    def test_handle_voice_chat_rejects_blank_transcript(self) -> None:
        with self.assertRaises(ValueError):
            handle_voice_chat(
                "conv-1",
                b"pcm",
                dependencies=AudioDependencies(
                    stt_client=FakeStt(transcript="  "),
                    tts_client=FakeTts(),
                    chat_handler=lambda conversation_id, message, *, dependencies=None: {},
                ),
            )

    def test_handle_voice_reply_from_text_skips_stt_and_runs_tts(self) -> None:
        stt = FakeStt(transcript="不应调用")
        tts = FakeTts()

        result = handle_voice_reply_from_text(
            "conv-1",
            "  实时字幕  ",
            dependencies=AudioDependencies(
                stt_client=stt,
                tts_client=tts,
                chat_handler=lambda conversation_id, message, *, dependencies=None: {
                    "conversation_id": conversation_id,
                    "request_id": "req-1",
                    "turn_id": "turn-1",
                    "reply": f"回复:{message}",
                    "retrieval_status": "completed",
                    "retrieved_memory_ids": [],
                },
            ),
        )

        assert result.transcript == "实时字幕"
        assert result.reply == "回复:实时字幕"
        assert result.audio_bytes == b"voice"
        assert stt.last_audio is None
        assert tts.last_text == "回复:实时字幕"

    def test_handle_voice_reply_from_text_strips_all_reply_tags_before_tts(
        self,
    ) -> None:
        tts = FakeTts()

        result = handle_voice_reply_from_text(
            "conv-1",
            "实时字幕",
            dependencies=AudioDependencies(
                tts_client=tts,
                chat_handler=lambda conversation_id, message, *, dependencies=None: {
                    "conversation_id": conversation_id,
                    "request_id": "req-1",
                    "turn_id": "turn-1",
                    "reply": "回复[emo:angry]，继续[act:开心]。[emo:excited][act:😁]",
                    "retrieval_status": "completed",
                    "retrieved_memory_ids": [],
                },
            ),
        )

        assert result.reply == "回复[emo:angry]，继续[act:开心]。[emo:excited][act:😁]"
        assert result.audio_bytes == b"voice"
        assert tts.last_text == "回复，继续。"

    def test_handle_voice_reply_from_text_skips_tts_when_disabled(self) -> None:
        tts = FakeTts()

        result = handle_voice_reply_from_text(
            "conv-1",
            "实时字幕",
            tts_enabled=False,
            dependencies=AudioDependencies(
                tts_client=tts,
                chat_handler=lambda conversation_id, message, *, dependencies=None: {
                    "conversation_id": conversation_id,
                    "request_id": "req-1",
                    "turn_id": "turn-1",
                    "reply": "回复",
                    "retrieval_status": "completed",
                    "retrieved_memory_ids": [],
                },
            ),
        )

        assert result.audio_bytes is None
        assert tts.last_text is None

    def test_handle_voice_reply_from_text_rejects_blank_transcript(self) -> None:
        with self.assertRaises(ValueError):
            handle_voice_reply_from_text(
                "conv-1",
                " ",
                dependencies=AudioDependencies(
                    chat_handler=lambda conversation_id, message, *, dependencies=None: {}
                ),
            )


if __name__ == "__main__":
    unittest.main()
