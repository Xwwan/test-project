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

import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable
from urllib.parse import urlsplit

from src.coordinator import (
    RequestNotFoundError,
    RequestStateError,
    get_pending_followup_requests,
)
from src.audio import AudioDependencies, VoiceChatRequest, handle_voice_chat
from src.services import (
    DialogueDependencies,
    curate_conversation_memory,
    handle_chat_message,
    handle_followup,
    refresh_user_profile,
)
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


_FOLLOWUP_RUN_PATTERN = re.compile(r"^/followups/(?P<request_id>[^/]+)/run$")


def dispatch(
    method: str,
    path: str,
    body: Any = None,
    *,
    dependencies: DialogueDependencies | None = None,
    audio_dependencies: AudioDependencies | None = None,
) -> JSONResponse:
    """Route one request to the right service function."""

    if not isinstance(method, str):
        return _error(400, "method must be a string")
    if not isinstance(path, str) or not path.startswith("/"):
        return _error(400, "path must start with '/'")

    method = method.upper()
    pure_path = urlsplit(path).path

    try:
        if method == "POST" and pure_path == "/chat":
            return _post_chat(body, dependencies)
        if method == "POST" and pure_path == "/voice/chat":
            return _post_voice_chat(body, dependencies, audio_dependencies)
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


def _get_pending_followups() -> JSONResponse:
    pending = get_pending_followup_requests()
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


# ---------------------------------------------------------------------------
# BaseHTTPRequestHandler glue
# ---------------------------------------------------------------------------


def create_request_handler(
    dependencies: DialogueDependencies | None = None,
    audio_dependencies: AudioDependencies | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Return a request handler class bound to ``dependencies``."""

    class _BoundHandler(ChatRequestHandler):
        injected_dependencies = dependencies
        injected_audio_dependencies = audio_dependencies

    return _BoundHandler


class ChatRequestHandler(BaseHTTPRequestHandler):
    """HTTP adapter that forwards requests to :func:`dispatch`."""

    injected_dependencies: DialogueDependencies | None = None
    injected_audio_dependencies: AudioDependencies | None = None
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
        body: Any = None
        if method == "POST":
            body = self._read_json_body()
            if body is _BODY_ERROR:
                return

        status, response_body = dispatch(
            method,
            self.path,
            body,
            dependencies=type(self).injected_dependencies,
            audio_dependencies=type(self).injected_audio_dependencies,
        )
        self._write_json(status, response_body)

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


_BODY_ERROR = object()


def build_app(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    dependencies: DialogueDependencies | None = None,
    audio_dependencies: AudioDependencies | None = None,
    server_class: Callable[..., HTTPServer] = HTTPServer,
) -> HTTPServer:
    """Build (but do not start) an :class:`HTTPServer` ready to serve the API."""

    handler_cls = create_request_handler(
        dependencies=dependencies,
        audio_dependencies=audio_dependencies,
    )
    return server_class((host, port), handler_cls)
