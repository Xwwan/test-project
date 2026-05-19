"""HTTP routing for the chat / memory API.

The router is split in two pieces:

* :func:`dispatch` is a pure ``(method, path, body) -> (status, body)``
  function. All business logic lives here so unit tests can run without
  spawning a real socket.
* :class:`ChatRequestHandler` is a thin :class:`http.server.BaseHTTPRequestHandler`
  subclass that converts incoming requests into ``dispatch`` calls.

The handler factory :func:`create_request_handler` injects a
:class:`DialogueDependencies` instance so production code can plug in real
Person 1 / Person 2 implementations while tests stay hermetic.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from src.coordinator import RequestNotFoundError, RequestStateError, get_pending_followup_requests
from src.audio.live_asr import (
    LiveAsrSessionManager,
    LiveAsrSessionNotFoundError,
    get_default_live_asr_manager,
)
from src.audio.schemas import (
    LiveVoiceAbortRequest,
    LiveVoiceChunkRequest,
    LiveVoiceFinishRequest,
    LiveVoiceStartRequest,
    LiveVoiceStartResponse,
    LiveVoiceTranscriptResponse,
    VoiceChatRequest,
)
from src.audio.service import (
    AudioDependencies,
    handle_voice_chat,
    handle_voice_reply_from_text,
)
from src.services import (
    DialogueDependencies,
    curate_conversation_memory,
    handle_chat_message,
    handle_chat_message_stream,
    handle_followup,
    refresh_user_profile,
)
from src.services.reply_tags import prepare_tts_text
from .schemas import (
    ChatRequest,
    ChatResponse,
    FollowupDecisionResponse,
    MemoryCurateRequest,
    MemoryCurateResponse,
    ProfileRefreshResponse,
    SchemaError,
)


JSONResponse = tuple[int, dict]


logger = logging.getLogger("chat-service.api")


_FOLLOWUP_RUN_PATTERN = re.compile(r"^/followups/(?P<request_id>[^/]+)/run$")
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_VOICE_LATENCY_PAGE = _STATIC_DIR / "voice_latency.html"


def dispatch(
    method: str,
    path: str,
    body: Any = None,
    *,
    dependencies: DialogueDependencies | None = None,
    audio_dependencies: AudioDependencies | None = None,
    live_asr_manager: LiveAsrSessionManager | None = None,
) -> JSONResponse:
    """Route one request to the right service function."""

    if not isinstance(method, str):
        return _error(400, "method must be a string")
    if not isinstance(path, str) or not path.startswith("/"):
        return _error(400, "path must start with '/'")

    method = method.upper()
    pure_path = urlsplit(path).path
    query = urlsplit(path).query

    try:
        if method == "POST" and pure_path == "/chat":
            return _post_chat(body, dependencies)
        if method == "POST" and pure_path == "/voice/chat":
            return _post_voice_chat(body, dependencies, audio_dependencies)
        if method == "POST" and pure_path == "/voice/live/start":
            return _post_voice_live_start(body, live_asr_manager)
        if method == "POST" and pure_path == "/voice/live/chunk":
            return _post_voice_live_chunk(body, live_asr_manager)
        if method == "GET" and pure_path == "/voice/live/transcript":
            return _get_voice_live_transcript(query, live_asr_manager)
        if method == "POST" and pure_path == "/voice/live/finish":
            return _post_voice_live_finish(
                body,
                dependencies,
                audio_dependencies,
                live_asr_manager,
            )
        if method == "POST" and pure_path == "/tools/voice-latency/finish":
            return _post_voice_latency_finish(body, audio_dependencies, live_asr_manager)
        if method == "POST" and pure_path == "/voice/live/abort":
            return _post_voice_live_abort(body, live_asr_manager)
        if method == "GET" and pure_path == "/followups/pending":
            return _get_pending_followups()
        if method == "POST" and _FOLLOWUP_RUN_PATTERN.match(pure_path):
            request_id = _FOLLOWUP_RUN_PATTERN.match(pure_path)["request_id"]
            return _post_followup_run(request_id, dependencies)
        if method == "POST" and pure_path == "/memory/curate":
            return _post_memory_curate(body, dependencies)
        if method == "POST" and pure_path == "/memory/profile/refresh":
            return _post_memory_profile_refresh(dependencies)
        if method == "GET" and pure_path == "/healthz":
            return 200, {"status": "ok"}
    except SchemaError as exc:
        return _error(400, str(exc))
    except (ValueError, TypeError) as exc:
        return _error(400, str(exc))
    except LiveAsrSessionNotFoundError as exc:
        return _error(404, str(exc))
    except RequestNotFoundError as exc:
        return _error(404, str(exc))
    except RequestStateError as exc:
        return _error(409, str(exc))
    except Exception as exc:  # pragma: no cover - defensive guard
        return _error(500, f"unexpected error: {exc}")

    return _error(404, f"no route for {method} {pure_path}")


# ---------------------------------------------------------------------------
# Route implementations
# ---------------------------------------------------------------------------


def _post_chat(body: Any, dependencies: DialogueDependencies | None) -> JSONResponse:
    request = ChatRequest.from_dict(body or {})
    payload = handle_chat_message(
        request.conversation_id,
        request.message,
        dependencies=dependencies,
    )
    response = ChatResponse(
        request_id=payload["request_id"],
        turn_id=payload["turn_id"],
        conversation_id=payload["conversation_id"],
        reply=payload["reply"],
        retrieval_status=payload["retrieval_status"],
        retrieved_memory_ids=payload.get("retrieved_memory_ids", []),
    )
    return 200, response.to_dict()


def _post_voice_chat(
    body: Any,
    dependencies: DialogueDependencies | None,
    audio_dependencies: AudioDependencies | None,
) -> JSONResponse:
    request = VoiceChatRequest.from_dict(body or {})
    response = handle_voice_chat(
        request.conversation_id,
        request.audio_bytes,
        audio_format=request.audio_format,
        tts_enabled=request.tts_enabled,
        dependencies=audio_dependencies,
        dialogue_dependencies=dependencies,
    )
    return 200, response.to_dict()


def _post_voice_live_start(
    body: Any,
    live_asr_manager: LiveAsrSessionManager | None,
) -> JSONResponse:
    request = LiveVoiceStartRequest.from_dict(body or {})
    manager = live_asr_manager or get_default_live_asr_manager()
    session_id = manager.start_session(
        request.sample_rate,
        request.channels,
        request.audio_format,
    )
    response = LiveVoiceStartResponse(
        session_id=session_id,
        sample_rate=request.sample_rate,
        channels=request.channels,
        audio_format=request.audio_format,
    )
    return 200, response.to_dict()


def _post_voice_live_chunk(
    body: Any,
    live_asr_manager: LiveAsrSessionManager | None,
) -> JSONResponse:
    request = LiveVoiceChunkRequest.from_dict(body or {})
    manager = live_asr_manager or get_default_live_asr_manager()
    accepted_bytes = manager.submit_chunk(request.session_id, request.audio_bytes)
    return 200, {"ok": True, "accepted_bytes": accepted_bytes}


def _get_voice_live_transcript(
    query: str,
    live_asr_manager: LiveAsrSessionManager | None,
) -> JSONResponse:
    from urllib.parse import parse_qs

    session_id = (parse_qs(query).get("session_id") or [""])[0]
    manager = live_asr_manager or get_default_live_asr_manager()
    state = manager.get_transcript(session_id)
    response = LiveVoiceTranscriptResponse(
        session_id=session_id,
        transcript=state.transcript,
        is_final=state.is_final,
        error=state.error,
    )
    return 200, response.to_dict()


def _post_voice_live_finish(
    body: Any,
    dependencies: DialogueDependencies | None,
    audio_dependencies: AudioDependencies | None,
    live_asr_manager: LiveAsrSessionManager | None,
) -> JSONResponse:
    request = LiveVoiceFinishRequest.from_dict(body or {})
    manager = live_asr_manager or get_default_live_asr_manager()
    state = manager.finish_session(request.session_id)
    if state.error:
        raise ValueError(state.error)
    response = handle_voice_reply_from_text(
        request.conversation_id,
        state.transcript,
        tts_enabled=request.tts_enabled,
        dependencies=audio_dependencies,
        dialogue_dependencies=dependencies,
    )
    return 200, response.to_dict()


def _post_voice_latency_finish(
    body: Any,
    audio_dependencies: AudioDependencies | None,
    live_asr_manager: LiveAsrSessionManager | None,
) -> JSONResponse:
    """Finish a live ASR session and reply without writing conversation data."""

    request = LiveVoiceFinishRequest.from_dict(body or {})
    manager = live_asr_manager or get_default_live_asr_manager()
    state = manager.finish_session(request.session_id)
    if state.error:
        raise ValueError(state.error)

    audio_deps = audio_dependencies or AudioDependencies()
    response = handle_voice_reply_from_text(
        request.conversation_id,
        state.transcript,
        tts_enabled=request.tts_enabled,
        dependencies=audio_deps.with_overrides(chat_handler=_handle_latency_chat_message),
        dialogue_dependencies=_build_no_db_latency_dependencies(),
    )
    return 200, response.to_dict()


def iter_voice_latency_finish_stream(
    request: LiveVoiceFinishRequest,
    *,
    audio_dependencies: AudioDependencies | None = None,
    live_asr_manager: LiveAsrSessionManager | None = None,
):
    """Finish live ASR and stream the model reply for the voice latency page."""

    manager = live_asr_manager or get_default_live_asr_manager()
    state = manager.finish_session(request.session_id)
    if state.error:
        raise ValueError(state.error)
    transcript = state.transcript.strip()
    if not transcript:
        raise ValueError("speech recognition produced an empty transcript")

    yield {
        "event": "transcript",
        "data": {
            "session_id": request.session_id,
            "conversation_id": request.conversation_id,
            "transcript": transcript,
        },
    }

    done_event: dict | None = None
    for item in handle_chat_message_stream(
        request.conversation_id,
        transcript,
        dependencies=_build_no_db_latency_dependencies(),
    ):
        if item.get("event") == "done":
            data = dict(item.get("data", {}))
            data["transcript"] = transcript
            data["audio_base64"] = None
            data["audio_format"] = "pcm"
            done_event = {"event": "done", "data": data}
            continue
        yield item

    if done_event is None:
        return

    if request.tts_enabled:
        reply = done_event["data"].get("reply") or ""
        yield from _iter_tts_audio_events(
            reply,
            audio_dependencies=audio_dependencies,
        )

    yield done_event


def iter_voice_live_finish_stream(
    request: LiveVoiceFinishRequest,
    *,
    dependencies: DialogueDependencies | None = None,
    audio_dependencies: AudioDependencies | None = None,
    live_asr_manager: LiveAsrSessionManager | None = None,
):
    """Finish live ASR and stream the normal persisted dialogue reply."""

    manager = live_asr_manager or get_default_live_asr_manager()
    state = manager.finish_session(request.session_id)
    if state.error:
        raise ValueError(state.error)
    transcript = state.transcript.strip()
    if not transcript:
        raise ValueError("speech recognition produced an empty transcript")

    yield {
        "event": "transcript",
        "data": {
            "session_id": request.session_id,
            "conversation_id": request.conversation_id,
            "transcript": transcript,
        },
    }

    done_sent = False
    for item in handle_chat_message_stream(
        request.conversation_id,
        transcript,
        dependencies=dependencies,
        stream_followup=True,
    ):
        if item.get("event") == "done":
            data = dict(item.get("data", {}))
            data["transcript"] = transcript
            data["audio_base64"] = None
            data["audio_format"] = "pcm"
            if request.tts_enabled:
                reply = data.get("reply") or ""
                yield from _iter_tts_audio_events(
                    reply,
                    audio_dependencies=audio_dependencies,
                )
            yield {"event": "done", "data": data}
            done_sent = True
            continue
        yield item

    if not done_sent:
        return


def _post_voice_live_abort(
    body: Any,
    live_asr_manager: LiveAsrSessionManager | None,
) -> JSONResponse:
    request = LiveVoiceAbortRequest.from_dict(body or {})
    manager = live_asr_manager or get_default_live_asr_manager()
    manager.abort_session(request.session_id)
    return 200, {"ok": True}


def _get_pending_followups() -> JSONResponse:
    pending = get_pending_followup_requests()
    logger.info(
        "followup pending listed pending_count=%d request_ids=%s",
        len(pending),
        [record["request_id"] for record in pending],
    )
    return 200, {
        "pending": [
            {
                "request_id": record["request_id"],
                "conversation_id": record["conversation_id"],
                "status": record["status"],
                "updated_at": record["updated_at"],
            }
            for record in pending
        ]
    }


def _post_followup_run(
    request_id: str,
    dependencies: DialogueDependencies | None,
) -> JSONResponse:
    logger.info("followup run requested request_id=%s", request_id)
    payload = handle_followup(request_id, dependencies=dependencies)
    response = FollowupDecisionResponse(
        request_id=payload["request_id"],
        conversation_id=payload["conversation_id"],
        decision=payload["decision"],
        followup_type=payload["followup_type"],
        reply=payload["reply"],
    )
    return 200, response.to_dict()


def _post_memory_curate(
    body: Any,
    dependencies: DialogueDependencies | None,
) -> JSONResponse:
    request = MemoryCurateRequest.from_dict(body or {})
    payload = curate_conversation_memory(
        request.conversation_id,
        history_limit=request.history_limit,
        dependencies=dependencies,
    )
    response = MemoryCurateResponse(
        conversation_id=payload["conversation_id"],
        operations=payload.get("operations", []),
        applied=payload.get("applied", []),
    )
    return 200, response.to_dict()


def _post_memory_profile_refresh(
    dependencies: DialogueDependencies | None,
) -> JSONResponse:
    payload = refresh_user_profile(dependencies=dependencies)
    response = ProfileRefreshResponse(
        should_update=bool(payload.get("should_update")),
        patch=payload.get("patch"),
        reason=payload.get("reason"),
        new_profile=payload.get("new_profile"),
    )
    return 200, response.to_dict()


def _error(status: int, message: str) -> JSONResponse:
    return status, {"error": {"message": message}}


def _handle_latency_chat_message(
    conversation_id: str,
    message: str,
    *,
    dependencies: DialogueDependencies | None = None,
) -> dict:
    """Run the normal initial reply flow with no-op persistence dependencies."""

    return handle_chat_message(
        conversation_id,
        message,
        dependencies=dependencies or _build_no_db_latency_dependencies(),
    )


def _build_no_db_latency_dependencies() -> DialogueDependencies:
    """Build dependencies for latency probes that must not write to SQLite."""

    from src.agents.dialogue_agent import generate_initial_reply, generate_initial_reply_stream
    from src.persona.file_manager import read_model_profile, read_user_profile

    return DialogueDependencies(
        read_model_profile=read_model_profile,
        read_user_profile=read_user_profile,
        apply_user_profile_patch=lambda patch: "",
        append_turn=lambda conversation_id, turn: turn["turn_id"],
        get_recent_history=lambda conversation_id, limit=20: [],
        get_compact_history=lambda conversation_id: "",
        update_compact_history=lambda conversation_id, value: None,
        list_lightweight_memory_items=lambda status="active": [],
        get_memory_items_by_ids=lambda ids: [],
        apply_memory_operations=lambda operations: [],
        generate_initial_reply=generate_initial_reply,
        generate_initial_reply_stream=generate_initial_reply_stream,
        generate_followup_reply=lambda input_data, **kwargs: {
            "request_id": input_data["request_id"],
            "decision": "no_followup",
            "followup_type": "none",
            "reply": "",
        },
        retrieve_relevant_memory_ids=lambda **kwargs: {
            "request_id": kwargs["request_id"],
            "selected_memory_ids": [],
        },
        extract_memory_operations=lambda **kwargs: {"operations": []},
        generate_user_profile_patch=lambda items, current, **kwargs: {
            "should_update": False,
            "patch": None,
            "reason": "latency probe",
        },
    )


def _iter_tts_audio_events(
    text: str,
    *,
    audio_dependencies: AudioDependencies | None = None,
):
    tts_text = prepare_tts_text(text)
    if not tts_text.strip():
        return

    from src.audio.tts import build_default_tts_client

    audio_deps = audio_dependencies or AudioDependencies()
    tts_client = audio_deps.tts_client or build_default_tts_client()
    stream_fn = getattr(tts_client, "synthesize_stream", None)
    if callable(stream_fn):
        chunks = stream_fn(tts_text)
    else:
        chunks = [tts_client.synthesize(tts_text)]

    sample_rate = getattr(tts_client, "sample_rate", 24000)
    for index, chunk in enumerate(chunks):
        if not chunk:
            continue
        yield {
            "event": "audio",
            "data": {
                "audio_base64": base64.b64encode(chunk).decode("ascii"),
                "audio_format": "pcm",
                "sample_rate": sample_rate,
                "chunk_index": index,
            },
        }


# ---------------------------------------------------------------------------
# BaseHTTPRequestHandler glue
# ---------------------------------------------------------------------------


def create_request_handler(
    dependencies: DialogueDependencies | None = None,
    audio_dependencies: AudioDependencies | None = None,
    live_asr_manager: LiveAsrSessionManager | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Return a request handler class bound to ``dependencies``."""

    class _BoundHandler(ChatRequestHandler):
        injected_dependencies = dependencies
        injected_audio_dependencies = audio_dependencies
        injected_live_asr_manager = live_asr_manager

    return _BoundHandler


class ChatRequestHandler(BaseHTTPRequestHandler):
    """HTTP adapter that forwards requests to :func:`dispatch`."""

    injected_dependencies: DialogueDependencies | None = None
    injected_audio_dependencies: AudioDependencies | None = None
    injected_live_asr_manager: LiveAsrSessionManager | None = None
    server_version = "ChatService/0.1"

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        self._handle("POST")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server signature
        # Quieter logging that respects ``--quiet`` style usage. Callers can
        # subclass and override if they want detailed access logs.
        return None

    def _handle(self, method: str) -> None:
        if method == "GET" and urlsplit(self.path).path == "/tools/voice-latency":
            self._write_html_file(_VOICE_LATENCY_PAGE)
            return

        body: Any = None
        if method == "POST":
            body = self._read_json_body()
            if body is _BODY_ERROR:
                return
            if urlsplit(self.path).path == "/chat/stream":
                self._write_chat_stream(body)
                return
            if urlsplit(self.path).path == "/voice/live/finish-stream":
                self._write_voice_live_stream(body)
                return
            if urlsplit(self.path).path == "/tools/voice-latency/finish-stream":
                self._write_voice_latency_stream(body)
                return

        status, response_body = dispatch(
            method,
            self.path,
            body,
            dependencies=type(self).injected_dependencies,
            audio_dependencies=type(self).injected_audio_dependencies,
            live_asr_manager=type(self).injected_live_asr_manager,
        )
        self._write_json(status, response_body)

    def _write_chat_stream(self, body: Any) -> None:
        try:
            request = ChatRequest.from_dict(body or {})
        except SchemaError as exc:
            self._write_json(400, {"error": {"message": str(exc)}})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            for item in handle_chat_message_stream(
                request.conversation_id,
                request.message,
                dependencies=type(self).injected_dependencies,
                stream_followup=True,
            ):
                event = item.get("event", "message")
                data = item.get("data", {})
                self._write_sse_event(event, data)
        except Exception as exc:  # pragma: no cover - defensive network path
            self._write_sse_event(
                "error",
                {"message": str(exc) or exc.__class__.__name__},
            )

    def _write_voice_latency_stream(self, body: Any) -> None:
        try:
            request = LiveVoiceFinishRequest.from_dict(body or {})
        except SchemaError as exc:
            self._write_json(400, {"error": {"message": str(exc)}})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            for item in iter_voice_latency_finish_stream(
                request,
                audio_dependencies=type(self).injected_audio_dependencies,
                live_asr_manager=type(self).injected_live_asr_manager,
            ):
                self._write_sse_event(item.get("event", "message"), item.get("data", {}))
        except Exception as exc:  # pragma: no cover - defensive network path
            self._write_sse_event(
                "error",
                {"message": str(exc) or exc.__class__.__name__},
            )

    def _write_voice_live_stream(self, body: Any) -> None:
        try:
            request = LiveVoiceFinishRequest.from_dict(body or {})
        except SchemaError as exc:
            self._write_json(400, {"error": {"message": str(exc)}})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            for item in iter_voice_live_finish_stream(
                request,
                dependencies=type(self).injected_dependencies,
                audio_dependencies=type(self).injected_audio_dependencies,
                live_asr_manager=type(self).injected_live_asr_manager,
            ):
                self._write_sse_event(item.get("event", "message"), item.get("data", {}))
        except Exception as exc:  # pragma: no cover - defensive network path
            self._write_sse_event(
                "error",
                {"message": str(exc) or exc.__class__.__name__},
            )

    def _read_json_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return None
        raw = self.rfile.read(length)
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._write_json(400, {"error": {"message": f"invalid JSON body: {exc}"}})
            return _BODY_ERROR

    def _write_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _write_sse_event(self, event: str, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        frame = f"event: {event}\ndata: {payload}\n\n".encode("utf-8")
        self.wfile.write(frame)
        self.wfile.flush()

    def _write_html_file(self, path: Path) -> None:
        try:
            payload = path.read_bytes()
        except OSError:
            self._write_json(404, {"error": {"message": "static page not found"}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


_BODY_ERROR = object()


def build_app(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    dependencies: DialogueDependencies | None = None,
    audio_dependencies: AudioDependencies | None = None,
    live_asr_manager: LiveAsrSessionManager | None = None,
    server_class: Callable[..., HTTPServer] = ThreadingHTTPServer,
) -> HTTPServer:
    """Build (but do not start) an :class:`HTTPServer` ready to serve the API."""

    handler_cls = create_request_handler(
        dependencies=dependencies,
        audio_dependencies=audio_dependencies,
        live_asr_manager=live_asr_manager,
    )
    return server_class((host, port), handler_cls)
