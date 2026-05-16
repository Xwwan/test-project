"""Low-latency live ASR sessions for chunked voice input."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Callable, Protocol
from uuid import uuid4

from src.audio.stt import VolcengineAsrClient, build_default_stt_client
from src.audio.volcengine_asr import (
    AsrErrorFrame,
    AsrResultFrame,
    build_audio_request_frame,
    build_full_client_request_frame,
    parse_server_frame,
)


LIVE_CHUNK_DURATION_MS = 160
LIVE_CHUNK_BYTES = 5120


class WebSocketModule(Protocol):
    def create_connection(
        self,
        url: str,
        *,
        timeout: float,
        header: list[str],
    ) -> Any:
        """Create a websocket-client compatible connection."""


@dataclass(frozen=True)
class LiveTranscriptState:
    transcript: str = ""
    is_final: bool = False
    error: str | None = None


@dataclass(frozen=True)
class LiveAsrConfig:
    app_id: str
    access_key: str
    resource_id: str
    url: str
    sample_rate: int = 16000
    timeout_seconds: float = 60.0
    uid: str = "voice-chat-service"


class LiveAsrSessionNotFoundError(KeyError):
    """Raised when a live voice session id is unknown."""


class LiveAsrSession:
    """One Volcengine ASR websocket session backed by a receive thread."""

    def __init__(
        self,
        config: LiveAsrConfig,
        *,
        websocket_module: WebSocketModule,
    ) -> None:
        self._config = config
        self._socket = websocket_module.create_connection(
            config.url,
            timeout=config.timeout_seconds,
            header=[
                f"X-Api-App-Key: {config.app_id}",
                f"X-Api-Access-Key: {config.access_key}",
                f"X-Api-Resource-Id: {config.resource_id}",
                f"X-Api-Connect-Id: {uuid4()}",
            ],
        )
        self._condition = threading.Condition()
        self._sequence = 1
        self._closed = False
        self._state = LiveTranscriptState()
        self._socket.send_binary(
            build_full_client_request_frame(self._request_payload(), sequence=self._sequence)
        )
        self._sequence += 1
        self._reader = threading.Thread(
            target=self._receive_loop,
            name="live-asr-recv",
            daemon=True,
        )
        self._reader.start()

    def submit_audio(self, audio: bytes) -> None:
        if not isinstance(audio, (bytes, bytearray)) or not audio:
            raise ValueError("audio chunk must be non-empty bytes")
        with self._condition:
            self._raise_if_unusable_locked()
            sequence = self._sequence
            self._sequence += 1
        self._socket.send_binary(build_audio_request_frame(bytes(audio), sequence=sequence))

    def get_transcript(self) -> LiveTranscriptState:
        with self._condition:
            return self._state

    def finish(self, timeout_seconds: float = 2.0) -> LiveTranscriptState:
        with self._condition:
            self._raise_if_unusable_locked(allow_final=True)
            sequence = self._sequence
            self._sequence += 1
        self._socket.send_binary(
            build_audio_request_frame(b"", is_final=True, sequence=sequence)
        )
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while (
                not self._state.is_final
                and self._state.error is None
                and time.monotonic() < deadline
            ):
                self._condition.wait(timeout=max(0.0, deadline - time.monotonic()))
            state = self._state
        self.close()
        return state

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
        self._socket.close()

    def _receive_loop(self) -> None:
        while True:
            with self._condition:
                if self._closed:
                    return
            try:
                raw = self._socket.recv()
                frame = parse_server_frame(raw)
            except Exception as exc:  # pragma: no cover - exact socket failures vary
                with self._condition:
                    if not self._closed:
                        self._state = LiveTranscriptState(
                            transcript=self._state.transcript,
                            is_final=self._state.is_final,
                            error=str(exc),
                        )
                        self._condition.notify_all()
                return
            with self._condition:
                if isinstance(frame, AsrErrorFrame):
                    self._state = LiveTranscriptState(
                        transcript=self._state.transcript,
                        is_final=self._state.is_final,
                        error=f"Volcengine ASR error {frame.code}: {frame.payload}",
                    )
                    self._condition.notify_all()
                    return
                if isinstance(frame, AsrResultFrame):
                    transcript = frame.text or self._state.transcript
                    self._state = LiveTranscriptState(
                        transcript=transcript,
                        is_final=frame.is_final,
                        error=None,
                    )
                    self._condition.notify_all()
                    if frame.is_final:
                        return

    def _request_payload(self) -> dict[str, Any]:
        return {
            "user": {"uid": self._config.uid},
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": self._config.sample_rate,
                "bits": 16,
                "channel": 1,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "show_utterances": True,
                "result_type": "full",
            },
        }

    def _raise_if_unusable_locked(self, *, allow_final: bool = False) -> None:
        if self._closed:
            raise RuntimeError("live ASR session is closed")
        if self._state.error is not None:
            raise RuntimeError(self._state.error)
        if self._state.is_final and not allow_final:
            raise RuntimeError("live ASR session is already final")


SessionFactory = Callable[[LiveAsrConfig], LiveAsrSession]


class LiveAsrSessionManager:
    def __init__(
        self,
        *,
        config: LiveAsrConfig,
        session_factory: SessionFactory | None = None,
        session_ttl_seconds: float = 300.0,
    ) -> None:
        self._config = config
        self._session_factory = session_factory or self._default_session_factory
        self._session_ttl_seconds = session_ttl_seconds
        self._sessions: dict[str, tuple[LiveAsrSession, float]] = {}
        self._lock = threading.Lock()

    def start_session(self, sample_rate: int, channels: int, audio_format: str) -> str:
        if sample_rate != 16000:
            raise ValueError("sample_rate must be 16000")
        if channels != 1:
            raise ValueError("channels must be 1")
        if audio_format != "pcm":
            raise ValueError("audio_format must be 'pcm'")
        self._cleanup_expired()
        session_id = f"live_{uuid4().hex}"
        session = self._session_factory(self._config)
        with self._lock:
            self._sessions[session_id] = (session, time.monotonic())
        return session_id

    def submit_chunk(self, session_id: str, audio: bytes) -> int:
        session = self._get_session(session_id)
        session.submit_audio(audio)
        with self._lock:
            self._sessions[session_id] = (session, time.monotonic())
        return len(audio)

    def get_transcript(self, session_id: str) -> LiveTranscriptState:
        session = self._get_session(session_id)
        return session.get_transcript()

    def finish_session(
        self,
        session_id: str,
        *,
        timeout_seconds: float = 2.0,
    ) -> LiveTranscriptState:
        session = self._pop_session(session_id)
        try:
            return session.finish(timeout_seconds=timeout_seconds)
        finally:
            session.close()

    def abort_session(self, session_id: str) -> None:
        session = self._pop_session(session_id)
        session.close()

    def _get_session(self, session_id: str) -> LiveAsrSession:
        if not isinstance(session_id, str) or not session_id:
            raise LiveAsrSessionNotFoundError("live ASR session id is required")
        self._cleanup_expired()
        with self._lock:
            item = self._sessions.get(session_id)
        if item is None:
            raise LiveAsrSessionNotFoundError(f"unknown live ASR session: {session_id}")
        return item[0]

    def _pop_session(self, session_id: str) -> LiveAsrSession:
        if not isinstance(session_id, str) or not session_id:
            raise LiveAsrSessionNotFoundError("live ASR session id is required")
        with self._lock:
            item = self._sessions.pop(session_id, None)
        if item is None:
            raise LiveAsrSessionNotFoundError(f"unknown live ASR session: {session_id}")
        return item[0]

    def _cleanup_expired(self) -> None:
        now = time.monotonic()
        expired: list[LiveAsrSession] = []
        with self._lock:
            for session_id, (session, touched_at) in list(self._sessions.items()):
                if now - touched_at > self._session_ttl_seconds:
                    expired.append(session)
                    del self._sessions[session_id]
        for session in expired:
            session.close()

    def _default_session_factory(self, config: LiveAsrConfig) -> LiveAsrSession:
        from src.audio.stt import _load_websocket_client

        return LiveAsrSession(config, websocket_module=_load_websocket_client())


_default_live_asr_manager: LiveAsrSessionManager | None = None
_default_manager_lock = threading.Lock()


def build_default_live_asr_manager() -> LiveAsrSessionManager:
    stt_client = build_default_stt_client()
    if not isinstance(stt_client, VolcengineAsrClient):
        raise ValueError("live ASR requires the Volcengine STT provider")
    config = LiveAsrConfig(
        app_id=stt_client.app_id,
        access_key=stt_client.access_key,
        resource_id=stt_client.resource_id,
        url=stt_client.url,
        sample_rate=stt_client.sample_rate,
        timeout_seconds=stt_client.timeout_seconds,
        uid=stt_client.uid,
    )
    return LiveAsrSessionManager(config=config)


def get_default_live_asr_manager() -> LiveAsrSessionManager:
    global _default_live_asr_manager
    with _default_manager_lock:
        if _default_live_asr_manager is None:
            _default_live_asr_manager = build_default_live_asr_manager()
        return _default_live_asr_manager
