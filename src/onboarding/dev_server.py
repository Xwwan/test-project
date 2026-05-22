"""Standalone HTTP server for testing the isolated onboarding workflow."""

from __future__ import annotations

import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from src.audio.live_asr import (
    LiveAsrSessionManager,
    LiveAsrSessionNotFoundError,
    get_default_live_asr_manager,
)
from src.audio.schemas import (
    LiveVoiceAbortRequest,
    LiveVoiceChunkRequest,
    LiveVoiceFinishTranscriptRequest,
    LiveVoiceStartRequest,
    LiveVoiceStartResponse,
    LiveVoiceTranscriptResponse,
)
from src.audio.tts import build_default_tts_client
from src.services.onboarding_service import (
    get_onboarding_status,
    handle_onboarding_message,
    start_onboarding,
)


STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_PAGE = STATIC_DIR / "onboarding_test.html"


def build_app(
    host: str = "127.0.0.1",
    port: int = 8010,
    *,
    live_asr_manager: LiveAsrSessionManager | None = None,
    server_class=ThreadingHTTPServer,
):
    handler_cls = create_request_handler(live_asr_manager=live_asr_manager)
    return server_class((host, port), handler_cls)


def create_request_handler(
    *,
    live_asr_manager: LiveAsrSessionManager | None = None,
) -> type[BaseHTTPRequestHandler]:
    class _Handler(OnboardingDevRequestHandler):
        injected_live_asr_manager = live_asr_manager

    return _Handler


class OnboardingDevRequestHandler(BaseHTTPRequestHandler):
    """HTTP adapter scoped to onboarding development tests only."""

    injected_live_asr_manager: LiveAsrSessionManager | None = None
    server_version = "OnboardingDev/0.1"

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._write_cors_headers()
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle(self, method: str) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        try:
            if method == "GET" and path in {"/", "/onboarding-test"}:
                self._write_html_file(INDEX_PAGE)
                return
            body = self._read_json_body() if method == "POST" else None
            if method == "POST" and path == "/voice/tts/stream":
                self._write_tts_stream(body)
                return
            status, payload = dispatch(
                method,
                self.path,
                body,
                live_asr_manager=type(self).injected_live_asr_manager,
            )
        except LiveAsrSessionNotFoundError as exc:
            status, payload = _error(404, str(exc))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            status, payload = _error(400, str(exc))
        except Exception as exc:  # pragma: no cover - defensive server boundary
            status, payload = _error(500, str(exc))
        self._write_json(status, payload)

    def _read_json_body(self) -> Any:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _write_html_file(self, path: Path) -> None:
        if not path.is_file():
            self._write_json(404, {"error": {"message": "test page not found"}})
            return
        data = path.read_bytes()
        self.send_response(200)
        self._write_cors_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._write_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_tts_stream(self, body: Any) -> None:
        metadata, audio_chunks = iter_voice_tts_stream(body)
        self.send_response(200)
        self._write_cors_headers()
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("X-Audio-Format", metadata["audio_format"])
        self.send_header("X-Sample-Rate", str(metadata["sample_rate"]))
        self.end_headers()
        for chunk in audio_chunks:
            if chunk:
                self.wfile.write(chunk)
                self.wfile.flush()

    def _write_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


def dispatch(
    method: str,
    path: str,
    body: Any = None,
    *,
    live_asr_manager: LiveAsrSessionManager | None = None,
) -> tuple[int, dict]:
    parsed = urlsplit(path)
    pure_path = parsed.path
    if method == "POST" and pure_path == "/onboarding/start":
        return 200, _post_onboarding_start(body)
    if method == "POST" and pure_path == "/onboarding/message":
        return 200, _post_onboarding_message(body)
    if method == "GET" and pure_path == "/onboarding/status":
        return 200, _get_onboarding_status(parsed.query)
    if method == "POST" and pure_path == "/voice/live/start":
        return 200, _post_voice_live_start(body, live_asr_manager)
    if method == "POST" and pure_path == "/voice/live/chunk":
        return 200, _post_voice_live_chunk(body, live_asr_manager)
    if method == "POST" and pure_path == "/voice/live/finish-transcript":
        return 200, _post_voice_live_finish_transcript(body, live_asr_manager)
    if method == "POST" and pure_path == "/voice/live/abort":
        return 200, _post_voice_live_abort(body, live_asr_manager)
    if method == "POST" and pure_path == "/voice/tts":
        return 200, _post_voice_tts(body)
    return _error(404, f"no route for {method} {pure_path}")


def _post_onboarding_start(body: Any) -> dict:
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    conversation_id = body.get("conversation_id") or ""
    if not isinstance(conversation_id, str):
        raise ValueError("conversation_id must be a string")
    return start_onboarding(conversation_id or None)


def _post_onboarding_message(body: Any) -> dict:
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    session_id = _required_string(body.get("session_id"), "session_id")
    message = _required_string(body.get("message"), "message")
    return handle_onboarding_message(session_id, message)


def _get_onboarding_status(query: str) -> dict:
    session_id = (parse_qs(query).get("session_id") or [""])[0]
    if not session_id:
        raise ValueError("session_id must be a non-empty string")
    return get_onboarding_status(session_id)


def _post_voice_live_start(
    body: Any,
    live_asr_manager: LiveAsrSessionManager | None,
) -> dict:
    request = LiveVoiceStartRequest.from_dict(body or {})
    manager = live_asr_manager or get_default_live_asr_manager()
    session_id = manager.start_session(
        request.sample_rate,
        request.channels,
        request.audio_format,
    )
    return LiveVoiceStartResponse(
        session_id=session_id,
        sample_rate=request.sample_rate,
        channels=request.channels,
        audio_format=request.audio_format,
    ).to_dict()


def _post_voice_live_chunk(
    body: Any,
    live_asr_manager: LiveAsrSessionManager | None,
) -> dict:
    request = LiveVoiceChunkRequest.from_dict(body or {})
    manager = live_asr_manager or get_default_live_asr_manager()
    accepted_bytes = manager.submit_chunk(request.session_id, request.audio_bytes)
    return {"ok": True, "accepted_bytes": accepted_bytes}


def _post_voice_live_finish_transcript(
    body: Any,
    live_asr_manager: LiveAsrSessionManager | None,
) -> dict:
    request = LiveVoiceFinishTranscriptRequest.from_dict(body or {})
    manager = live_asr_manager or get_default_live_asr_manager()
    state = manager.finish_session(request.session_id)
    if state.error:
        raise ValueError(state.error)
    return LiveVoiceTranscriptResponse(
        session_id=request.session_id,
        transcript=state.transcript,
        is_final=state.is_final,
        error=state.error,
    ).to_dict()


def _post_voice_live_abort(
    body: Any,
    live_asr_manager: LiveAsrSessionManager | None,
) -> dict:
    request = LiveVoiceAbortRequest.from_dict(body or {})
    manager = live_asr_manager or get_default_live_asr_manager()
    manager.abort_session(request.session_id)
    return {"ok": True}


def _post_voice_tts(body: Any) -> dict:
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    text = _required_string(body.get("text"), "text").strip()
    tts_client = build_default_tts_client()
    audio = tts_client.synthesize(text)
    return {
        "audio_base64": base64.b64encode(audio).decode("ascii"),
        "sample_rate": getattr(tts_client, "sample_rate", 24000),
        "audio_format": "pcm",
    }


def iter_voice_tts_stream(body: Any) -> tuple[dict, Any]:
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    text = _required_string(body.get("text"), "text").strip()
    tts_client = build_default_tts_client()
    metadata = {
        "sample_rate": getattr(tts_client, "sample_rate", 24000),
        "audio_format": "pcm",
    }
    return metadata, tts_client.synthesize_stream(text)


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _error(status: int, message: str) -> tuple[int, dict]:
    return status, {"error": {"message": message}}


def main() -> None:
    server = build_app()
    host, port = server.server_address
    print(f"Onboarding dev server listening on http://{host}:{port}")
    print(f"Test page: http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
