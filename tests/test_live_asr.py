"""Tests for low-latency live ASR session management."""

from __future__ import annotations

import gzip
import json
import threading
import unittest

from src.audio.live_asr import (
    LiveAsrConfig,
    LiveAsrSession,
    LiveAsrSessionManager,
    LiveAsrSessionNotFoundError,
)
def _server_result(text: str, *, is_final: bool = False) -> bytes:
    payload = gzip.compress(json.dumps({"result": {"text": text}}).encode("utf-8"))
    return (
        bytes([0x11, 0x93 if is_final else 0x91, 0x11, 0])
        + (1).to_bytes(4, "big", signed=True)
        + len(payload).to_bytes(4, "big")
        + payload
    )


def _server_error() -> bytes:
    payload = gzip.compress(json.dumps({"message": "bad auth"}).encode("utf-8"))
    return (
        bytes([0x11, 0xF0, 0x11, 0])
        + (401).to_bytes(4, "big", signed=True)
        + len(payload).to_bytes(4, "big")
        + payload
    )


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed = False
        self._incoming: list[bytes] = []
        self._condition = threading.Condition()

    def send_binary(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self) -> bytes:
        with self._condition:
            while not self._incoming and not self.closed:
                self._condition.wait()
            if self._incoming:
                return self._incoming.pop(0)
            raise RuntimeError("socket closed")

    def emit(self, data: bytes) -> None:
        with self._condition:
            self._incoming.append(data)
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self.closed = True
            self._condition.notify_all()


class FakeWebSocketModule:
    def __init__(self, socket: FakeSocket) -> None:
        self.socket = socket
        self.last_url: str | None = None
        self.last_header: list[str] | None = None

    def create_connection(self, url: str, *, timeout: float, header: list[str]) -> FakeSocket:
        self.last_url = url
        self.last_header = header
        return self.socket


def _config() -> LiveAsrConfig:
    return LiveAsrConfig(
        app_id="app",
        access_key="access",
        resource_id="resource",
        url="wss://example.test/asr",
        timeout_seconds=3,
    )


class LiveAsrTest(unittest.TestCase):
    def test_session_start_sends_full_client_request(self) -> None:
        socket = FakeSocket()
        websocket = FakeWebSocketModule(socket)
        session = LiveAsrSession(_config(), websocket_module=websocket)

        assert websocket.last_url == "wss://example.test/asr"
        assert websocket.last_header is not None
        assert "X-Api-App-Key: app" in websocket.last_header
        assert len(socket.sent) == 1
        assert socket.sent[0][1] == 0x11

        session.close()

    def test_submit_chunk_sends_audio_frame(self) -> None:
        socket = FakeSocket()
        session = LiveAsrSession(_config(), websocket_module=FakeWebSocketModule(socket))

        session.submit_audio(b"pcm")

        assert len(socket.sent) == 2
        assert socket.sent[1][1] == 0x21
        session.close()

    def test_result_updates_transcript_and_final_state(self) -> None:
        socket = FakeSocket()
        session = LiveAsrSession(_config(), websocket_module=FakeWebSocketModule(socket))

        socket.emit(_server_result("你好", is_final=False))
        self._wait_until(lambda: session.get_transcript().transcript == "你好")
        socket.emit(_server_result("你好 Reachy", is_final=True))
        self._wait_until(lambda: session.get_transcript().is_final)

        state = session.get_transcript()
        assert state.transcript == "你好 Reachy"
        assert state.is_final is True
        assert state.error is None
        session.close()

    def test_finish_sends_final_packet_and_closes(self) -> None:
        socket = FakeSocket()
        session = LiveAsrSession(_config(), websocket_module=FakeWebSocketModule(socket))
        socket.emit(_server_result("最终文本", is_final=True))
        self._wait_until(lambda: session.get_transcript().is_final)

        state = session.finish(timeout_seconds=0.1)

        assert socket.sent[-1][1] == 0x23
        assert int.from_bytes(socket.sent[-1][4:8], "big", signed=True) == -2
        assert state.transcript == "最终文本"
        assert socket.closed is True

    def test_manager_removes_finished_and_aborted_sessions(self) -> None:
        created: list[FakeSocket] = []

        def factory(config: LiveAsrConfig) -> LiveAsrSession:
            socket = FakeSocket()
            created.append(socket)
            return LiveAsrSession(config, websocket_module=FakeWebSocketModule(socket))

        manager = LiveAsrSessionManager(config=_config(), session_factory=factory)
        finished_id = manager.start_session(16000, 1, "pcm")
        created[-1].emit(_server_result("完成", is_final=True))
        state = manager.finish_session(finished_id, timeout_seconds=0.1)

        assert state.transcript == "完成"
        with self.assertRaises(LiveAsrSessionNotFoundError):
            manager.get_transcript(finished_id)

        aborted_id = manager.start_session(16000, 1, "pcm")
        manager.abort_session(aborted_id)

        with self.assertRaises(LiveAsrSessionNotFoundError):
            manager.get_transcript(aborted_id)

    def test_manager_submit_chunk_and_error_state(self) -> None:
        created: list[FakeSocket] = []

        def factory(config: LiveAsrConfig) -> LiveAsrSession:
            socket = FakeSocket()
            created.append(socket)
            return LiveAsrSession(config, websocket_module=FakeWebSocketModule(socket))

        manager = LiveAsrSessionManager(config=_config(), session_factory=factory)
        session_id = manager.start_session(16000, 1, "pcm")

        accepted = manager.submit_chunk(session_id, b"chunk")
        created[-1].emit(_server_error())
        self._wait_until(lambda: manager.get_transcript(session_id).error is not None)

        assert accepted == len(b"chunk")
        assert "Volcengine ASR error 401" in manager.get_transcript(session_id).error
        manager.abort_session(session_id)

    def _wait_until(self, predicate) -> None:
        event = threading.Event()
        for _ in range(100):
            if predicate():
                return
            event.wait(0.01)
        raise AssertionError("condition was not reached")


if __name__ == "__main__":
    unittest.main()
