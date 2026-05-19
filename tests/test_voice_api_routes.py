"""Tests for voice chat API routing."""

from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.api.routes import dispatch, iter_voice_latency_finish_stream
from src.coordinator import request_coordinator
from src.audio.live_asr import LiveAsrSessionNotFoundError, LiveTranscriptState
from src.audio.service import AudioDependencies
from src.audio.schemas import LiveVoiceFinishRequest
from src.persona import file_manager


class VoiceApiRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        request_coordinator.reset_store()

    def tearDown(self) -> None:
        request_coordinator.reset_store()
        file_manager.reset_data_dir()

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

    def test_live_voice_start_returns_session_and_chunk_settings(self) -> None:
        manager = FakeLiveAsrManager()

        status, body = dispatch(
            "POST",
            "/voice/live/start",
            {},
            live_asr_manager=manager,
        )

        assert status == 200
        assert body["session_id"] == "live-1"
        assert body["sample_rate"] == 16000
        assert body["chunk_duration_ms"] == 160
        assert body["chunk_bytes"] == 5120
        assert manager.started == [(16000, 1, "pcm")]

    def test_live_voice_chunk_accepts_base64_pcm(self) -> None:
        manager = FakeLiveAsrManager()

        status, body = dispatch(
            "POST",
            "/voice/live/chunk",
            {
                "session_id": "live-1",
                "audio_base64": base64.b64encode(b"chunk").decode("ascii"),
            },
            live_asr_manager=manager,
        )

        assert status == 200
        assert body == {"ok": True, "accepted_bytes": 5}
        assert manager.chunks == [("live-1", b"chunk")]

    def test_live_voice_transcript_returns_latest_state(self) -> None:
        manager = FakeLiveAsrManager()
        manager.states["live-1"] = LiveTranscriptState(
            transcript="你好 Reachy",
            is_final=False,
            error=None,
        )

        status, body = dispatch(
            "GET",
            "/voice/live/transcript?session_id=live-1",
            live_asr_manager=manager,
        )

        assert status == 200
        assert body == {
            "session_id": "live-1",
            "transcript": "你好 Reachy",
            "is_final": False,
            "error": None,
        }

    def test_live_voice_finish_reuses_chat_and_tts_without_stt(self) -> None:
        manager = FakeLiveAsrManager()
        manager.states["live-1"] = LiveTranscriptState(
            transcript="实时文本",
            is_final=True,
            error=None,
        )
        tts_calls: list[str] = []
        deps = AudioDependencies(
            stt_client=type(
                "S",
                (),
                {
                    "transcribe": lambda self, audio, *, audio_format="pcm": (_ for _ in ()).throw(
                        AssertionError("STT should not run")
                    )
                },
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
                "reply": f"回复:{message}",
                "retrieval_status": "completed",
                "retrieved_memory_ids": [],
            },
        )

        status, body = dispatch(
            "POST",
            "/voice/live/finish",
            {
                "session_id": "live-1",
                "conversation_id": "conv-1",
                "tts_enabled": True,
            },
            audio_dependencies=deps,
            live_asr_manager=manager,
        )

        assert status == 200
        assert body["conversation_id"] == "conv-1"
        assert body["transcript"] == "实时文本"
        assert body["reply"] == "回复:实时文本"
        assert body["audio_base64"] == base64.b64encode(b"audio").decode("ascii")
        assert tts_calls == ["回复:实时文本"]
        assert manager.finished == ["live-1"]

    def test_voice_latency_finish_generates_reply_without_persisting_turns(self) -> None:
        manager = FakeLiveAsrManager()
        manager.states["live-1"] = LiveTranscriptState(
            transcript="只测试延迟",
            is_final=True,
            error=None,
        )
        injected_chat_calls: list[str] = []
        deps = AudioDependencies(
            chat_handler=lambda conversation_id, message, *, dependencies=None: injected_chat_calls.append(
                message
            )
            or {
                "conversation_id": conversation_id,
                "request_id": "req-injected",
                "turn_id": "turn-injected",
                "reply": "不应调用",
                "retrieval_status": "completed",
                "retrieved_memory_ids": [],
            },
        )

        def fake_initial(input_data, **kwargs):
            return {
                "request_id": input_data["request_id"],
                "reply": f"延迟回复:{input_data['current_query']}",
            }

        with patch("src.agents.dialogue_agent.generate_initial_reply", fake_initial):
            status, body = dispatch(
                "POST",
                "/tools/voice-latency/finish",
                {
                    "session_id": "live-1",
                    "conversation_id": "conv-latency",
                    "tts_enabled": False,
                },
                audio_dependencies=deps,
                live_asr_manager=manager,
            )

        assert status == 200
        assert body["conversation_id"] == "conv-latency"
        assert body["transcript"] == "只测试延迟"
        assert body["reply"] == "延迟回复:只测试延迟"
        assert body["audio_base64"] is None
        assert body["retrieved_memory_ids"] == []
        assert injected_chat_calls == []
        assert manager.finished == ["live-1"]

    def test_voice_latency_finish_includes_model_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            data_dir.mkdir()
            (data_dir / "Model.md").write_text("模型人设内容", encoding="utf-8")
            (data_dir / "User.md").write_text("用户画像内容", encoding="utf-8")
            file_manager.set_data_dir(data_dir)

            manager = FakeLiveAsrManager()
            manager.states["live-1"] = LiveTranscriptState(
                transcript="看看人设",
                is_final=True,
                error=None,
            )
            seen_input: dict = {}

            def fake_initial(input_data, **kwargs):
                seen_input.update(input_data)
                return {
                    "request_id": input_data["request_id"],
                    "reply": "收到",
                }

            with patch("src.agents.dialogue_agent.generate_initial_reply", fake_initial):
                status, body = dispatch(
                    "POST",
                    "/tools/voice-latency/finish",
                    {
                        "session_id": "live-1",
                        "conversation_id": "conv-latency",
                        "tts_enabled": False,
                    },
                    live_asr_manager=manager,
                )

        assert status == 200
        assert body["reply"] == "收到"
        assert seen_input["model_profile"] == "模型人设内容"
        assert seen_input["user_profile"] == "用户画像内容"

    def test_voice_latency_finish_stream_yields_transcript_deltas_and_done(self) -> None:
        manager = FakeLiveAsrManager()
        manager.states["live-1"] = LiveTranscriptState(
            transcript="只测试流式延迟",
            is_final=True,
            error=None,
        )

        def fake_initial_stream(input_data, **kwargs):
            yield "流式"
            yield "回复"

        with patch(
            "src.agents.dialogue_agent.generate_initial_reply_stream",
            fake_initial_stream,
        ):
            events = list(
                iter_voice_latency_finish_stream(
                    LiveVoiceFinishRequest(
                        session_id="live-1",
                        conversation_id="conv-latency",
                        tts_enabled=False,
                    ),
                    live_asr_manager=manager,
                )
            )

        assert [event["event"] for event in events] == [
            "transcript",
            "meta",
            "delta",
            "delta",
            "done",
        ]
        assert events[0]["data"]["transcript"] == "只测试流式延迟"
        assert events[2]["data"] == {"delta": "流式"}
        assert events[3]["data"] == {"delta": "回复"}
        assert events[-1]["data"]["transcript"] == "只测试流式延迟"
        assert events[-1]["data"]["reply"] == "流式回复"
        assert events[-1]["data"]["audio_base64"] is None
        assert manager.finished == ["live-1"]

    def test_voice_latency_finish_stream_yields_tts_audio_events_when_enabled(self) -> None:
        manager = FakeLiveAsrManager()
        manager.states["live-1"] = LiveTranscriptState(
            transcript="需要语音",
            is_final=True,
            error=None,
        )
        tts = FakeStreamingTts([b"audio-a", b"audio-b"])

        def fake_initial_stream(input_data, **kwargs):
            yield "语音[emo:angry]"
            yield "回复[act:开心]"

        with patch(
            "src.agents.dialogue_agent.generate_initial_reply_stream",
            fake_initial_stream,
        ):
            events = list(
                iter_voice_latency_finish_stream(
                    LiveVoiceFinishRequest(
                        session_id="live-1",
                        conversation_id="conv-latency",
                        tts_enabled=True,
                    ),
                    audio_dependencies=AudioDependencies(tts_client=tts),
                    live_asr_manager=manager,
                )
            )

        assert [event["event"] for event in events] == [
            "transcript",
            "meta",
            "delta",
            "delta",
            "audio",
            "audio",
            "done",
        ]
        assert tts.streamed_text == "语音回复"
        assert events[4]["data"]["audio_base64"] == base64.b64encode(b"audio-a").decode(
            "ascii"
        )
        assert events[4]["data"]["audio_format"] == "pcm"
        assert events[4]["data"]["sample_rate"] == 24000
        assert events[4]["data"]["chunk_index"] == 0
        assert events[5]["data"]["audio_base64"] == base64.b64encode(b"audio-b").decode(
            "ascii"
        )
        assert events[-1]["data"]["reply"] == "语音[emo:angry]回复[act:开心]"
        assert events[-1]["data"]["audio_base64"] is None

    def test_live_voice_abort_does_not_call_chat(self) -> None:
        manager = FakeLiveAsrManager()

        status, body = dispatch(
            "POST",
            "/voice/live/abort",
            {"session_id": "live-1"},
            audio_dependencies=AudioDependencies(
                chat_handler=lambda conversation_id, message, *, dependencies=None: {
                    "reply": "不应调用"
                }
            ),
            live_asr_manager=manager,
        )

        assert status == 200
        assert body == {"ok": True}
        assert manager.aborted == ["live-1"]

    def test_live_voice_unknown_session_returns_404(self) -> None:
        status, body = dispatch(
            "GET",
            "/voice/live/transcript?session_id=missing",
            live_asr_manager=FakeLiveAsrManager(),
        )

        assert status == 404
        assert "unknown live ASR session" in body["error"]["message"]


class FakeLiveAsrManager:
    def __init__(self) -> None:
        self.started: list[tuple[int, int, str]] = []
        self.chunks: list[tuple[str, bytes]] = []
        self.finished: list[str] = []
        self.aborted: list[str] = []
        self.states: dict[str, LiveTranscriptState] = {
            "live-1": LiveTranscriptState(transcript="", is_final=False, error=None)
        }

    def start_session(self, sample_rate: int, channels: int, audio_format: str) -> str:
        self.started.append((sample_rate, channels, audio_format))
        return "live-1"

    def submit_chunk(self, session_id: str, audio: bytes) -> int:
        self._require(session_id)
        self.chunks.append((session_id, audio))
        return len(audio)

    def get_transcript(self, session_id: str) -> LiveTranscriptState:
        self._require(session_id)
        return self.states[session_id]

    def finish_session(self, session_id: str) -> LiveTranscriptState:
        self._require(session_id)
        self.finished.append(session_id)
        return self.states[session_id]

    def abort_session(self, session_id: str) -> None:
        self._require(session_id)
        self.aborted.append(session_id)

    def _require(self, session_id: str) -> None:
        if session_id not in self.states:
            raise LiveAsrSessionNotFoundError(f"unknown live ASR session: {session_id}")


class FakeStreamingTts:
    sample_rate = 24000

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.streamed_text: str | None = None

    def synthesize(self, text: str) -> bytes:
        return b"".join(self.synthesize_stream(text))

    def synthesize_stream(self, text: str):
        self.streamed_text = text
        yield from self.chunks


if __name__ == "__main__":
    unittest.main()
