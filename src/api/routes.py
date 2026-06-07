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
import queue
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlsplit

from src.coordinator import RequestNotFoundError, RequestStateError, get_pending_followup_requests
from src.interaction import store as interaction_store
from src.interaction.store import (
    InteractionRunNotFoundError,
    InteractionSessionNotFoundError,
)
from src.audio.live_asr import (
    LiveAsrSessionManager,
    LiveAsrSessionNotFoundError,
    get_default_live_asr_manager,
)
from src.audio.schemas import (
    LiveVoiceAbortRequest,
    LiveVoiceChunkRequest,
    LiveVoiceFinishRequest,
    LiveVoiceFinishTranscriptRequest,
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
from src.demo_profiles import (
    DemoProfileError,
    get_demo_profile_state,
    switch_demo_profile,
)
from src.services import (
    DialogueDependencies,
    curate_conversation_memory,
    handle_chat_message,
    handle_chat_message_stream,
    handle_followup,
    create_interaction_session,
    get_interaction_session_status,
    iter_text_interaction_events,
    iter_followup_events,
    refresh_user_profile,
)
from src.services.onboarding_service import OnboardingDependencies
from src.services.reply_tags import prepare_tts_text
from .schemas import (
    ChatRequest,
    ChatResponse,
    FollowupDecisionResponse,
    InteractionSessionCreateRequest,
    InteractionLiveChunkRequest,
    InteractionLiveFinishStreamRequest,
    InteractionLiveSessionRequest,
    InteractionLiveStartRequest,
    InteractionPlaybackRequest,
    InteractionTextStreamRequest,
    MemoryCurateRequest,
    MemoryCurateResponse,
    ProfileRefreshResponse,
    SchemaError,
)


JSONResponse = tuple[int, dict]


logger = logging.getLogger("chat-service.api")


_FOLLOWUP_RUN_PATTERN = re.compile(r"^/followups/(?P<request_id>[^/]+)/run$")
_INTERACTION_SESSION_PATTERN = re.compile(
    r"^/interaction/sessions/(?P<interaction_session_id>[^/]+)$"
)
_INTERACTION_SESSION_RUNS_PATTERN = re.compile(
    r"^/interaction/sessions/(?P<interaction_session_id>[^/]+)/runs$"
)
_INTERACTION_RUN_PATTERN = re.compile(r"^/interaction/runs/(?P<run_id>[^/]+)$")
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_VOICE_LATENCY_PAGE = _STATIC_DIR / "voice_latency.html"
_TTS_SENTINEL = object()
_STREAM_SENTINEL = object()
_TTS_SEGMENT_PUNCTUATION = "。！？!?；;\n"
_TTS_SEGMENT_MAX_CHARS = 80


def dispatch(
    method: str,
    path: str,
    body: Any = None,
    *,
    dependencies: DialogueDependencies | None = None,
    audio_dependencies: AudioDependencies | None = None,
    live_asr_manager: LiveAsrSessionManager | None = None,
    onboarding_dependencies: OnboardingDependencies | None = None,
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
        if method == "POST" and pure_path == "/interaction/sessions":
            return _post_interaction_session(body, onboarding_dependencies)
        if method == "GET" and _INTERACTION_SESSION_RUNS_PATTERN.match(pure_path):
            interaction_session_id = _INTERACTION_SESSION_RUNS_PATTERN.match(pure_path)[
                "interaction_session_id"
            ]
            return _get_interaction_session_runs(interaction_session_id, query)
        if method == "GET" and _INTERACTION_SESSION_PATTERN.match(pure_path):
            interaction_session_id = _INTERACTION_SESSION_PATTERN.match(pure_path)[
                "interaction_session_id"
            ]
            return _get_interaction_session(
                interaction_session_id,
                onboarding_dependencies,
            )
        if method == "GET" and _INTERACTION_RUN_PATTERN.match(pure_path):
            run_id = _INTERACTION_RUN_PATTERN.match(pure_path)["run_id"]
            return _get_interaction_run(run_id)
        if method == "POST" and pure_path == "/interaction/playback/done":
            return _post_interaction_playback_done(body)
        if method == "POST" and pure_path == "/interaction/playback/error":
            return _post_interaction_playback_error(body)
        if method == "POST" and pure_path == "/interaction/live/start":
            return _post_interaction_live_start(body, live_asr_manager)
        if method == "POST" and pure_path == "/interaction/live/chunk":
            return _post_interaction_live_chunk(body, live_asr_manager)
        if method == "GET" and pure_path == "/interaction/live/transcript":
            return _get_interaction_live_transcript(query, live_asr_manager)
        if method == "POST" and pure_path == "/interaction/live/finish-transcript":
            return _post_interaction_live_finish_transcript(body, live_asr_manager)
        if method == "POST" and pure_path == "/interaction/live/abort":
            return _post_interaction_live_abort(body, live_asr_manager)
        if method == "POST" and pure_path == "/voice/chat":
            return _post_voice_chat(body, dependencies, audio_dependencies)
        if method == "POST" and pure_path == "/voice/live/start":
            return _post_voice_live_start(body, live_asr_manager)
        if method == "POST" and pure_path == "/voice/live/chunk":
            return _post_voice_live_chunk(body, live_asr_manager)
        if method == "GET" and pure_path == "/voice/live/transcript":
            return _get_voice_live_transcript(query, live_asr_manager)
        if method == "POST" and pure_path == "/voice/live/finish-transcript":
            return _post_voice_live_finish_transcript(body, live_asr_manager)
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
        if method == "GET" and pure_path == "/demo/profile":
            return _get_demo_profile()
        if method == "POST" and pure_path == "/demo/profile":
            return _post_demo_profile(body)
        if method == "GET" and pure_path == "/healthz":
            return 200, {"status": "ok"}
    except SchemaError as exc:
        return _error(400, str(exc))
    except DemoProfileError as exc:
        return _error(400, str(exc))
    except (ValueError, TypeError) as exc:
        return _error(400, str(exc))
    except LiveAsrSessionNotFoundError as exc:
        return _error(404, str(exc))
    except InteractionSessionNotFoundError as exc:
        return _error(404, str(exc))
    except InteractionRunNotFoundError as exc:
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


def _post_interaction_session(
    body: Any,
    onboarding_dependencies: OnboardingDependencies | None,
) -> JSONResponse:
    request = InteractionSessionCreateRequest.from_dict(body or {})
    return 200, create_interaction_session(
        workflow=request.workflow,
        conversation_id=request.conversation_id,
        input_mode=request.input_mode,
        tts_enabled=request.tts_enabled,
        onboarding_dependencies=onboarding_dependencies,
    )


def _get_interaction_session(
    interaction_session_id: str,
    onboarding_dependencies: OnboardingDependencies | None,
) -> JSONResponse:
    return 200, get_interaction_session_status(
        interaction_session_id,
        onboarding_dependencies=onboarding_dependencies,
    )


def _get_interaction_session_runs(
    interaction_session_id: str,
    query: str,
) -> JSONResponse:
    parsed = parse_qs(query)
    limit = int((parsed.get("limit") or ["50"])[0])
    runs = interaction_store.list_runs_for_session(interaction_session_id, limit=limit)
    return 200, {
        "interaction_session_id": interaction_session_id,
        "runs": runs,
    }


def _get_interaction_run(run_id: str) -> JSONResponse:
    return 200, interaction_store.get_run(run_id)


def _post_interaction_playback_done(body: Any) -> JSONResponse:
    request = InteractionPlaybackRequest.from_dict(body or {})
    run = interaction_store.update_run(
        request.run_id,
        playback_key=request.playback_key,
        playback_status=interaction_store.PLAYBACK_DONE,
        playback_error="",
    )
    return 200, {
        "ok": True,
        "run_id": run["run_id"],
        "playback_key": run["playback_key"],
        "playback_status": run["playback_status"],
    }


def _post_interaction_playback_error(body: Any) -> JSONResponse:
    request = InteractionPlaybackRequest.from_dict(body or {})
    run = interaction_store.update_run(
        request.run_id,
        playback_key=request.playback_key,
        playback_status=interaction_store.PLAYBACK_ERROR,
        playback_error=request.error,
    )
    return 200, {
        "ok": True,
        "run_id": run["run_id"],
        "playback_key": run["playback_key"],
        "playback_status": run["playback_status"],
        "playback_error": run["playback_error"],
    }


def _post_interaction_live_start(
    body: Any,
    live_asr_manager: LiveAsrSessionManager | None,
) -> JSONResponse:
    request = InteractionLiveStartRequest.from_dict(body or {})
    _require_interaction_session_workflow(
        request.interaction_session_id,
        request.workflow,
    )
    manager = live_asr_manager or get_default_live_asr_manager()
    live_session_id = manager.start_session(
        request.sample_rate,
        request.channels,
        request.audio_format,
    )
    response = LiveVoiceStartResponse(
        session_id=live_session_id,
        sample_rate=request.sample_rate,
        channels=request.channels,
        audio_format=request.audio_format,
    ).to_dict()
    response.update(
        {
            "interaction_session_id": request.interaction_session_id,
            "workflow": request.workflow,
            "live_session_id": live_session_id,
        }
    )
    return 200, response


def _post_interaction_live_chunk(
    body: Any,
    live_asr_manager: LiveAsrSessionManager | None,
) -> JSONResponse:
    request = InteractionLiveChunkRequest.from_dict(body or {})
    _require_interaction_session_workflow(
        request.interaction_session_id,
        request.workflow,
    )
    manager = live_asr_manager or get_default_live_asr_manager()
    accepted_bytes = manager.submit_chunk(request.live_session_id, request.audio_bytes)
    return 200, {
        "ok": True,
        "interaction_session_id": request.interaction_session_id,
        "workflow": request.workflow,
        "live_session_id": request.live_session_id,
        "accepted_bytes": accepted_bytes,
    }


def _get_interaction_live_transcript(
    query: str,
    live_asr_manager: LiveAsrSessionManager | None,
) -> JSONResponse:
    parsed = parse_qs(query)
    live_session_id = (
        parsed.get("live_session_id")
        or parsed.get("session_id")
        or [""]
    )[0]
    interaction_session_id = (parsed.get("interaction_session_id") or [""])[0]
    workflow = (parsed.get("workflow") or [""])[0]
    if interaction_session_id or workflow:
        _require_interaction_session_workflow(interaction_session_id, workflow)
    manager = live_asr_manager or get_default_live_asr_manager()
    state = manager.get_transcript(live_session_id)
    return 200, {
        "interaction_session_id": interaction_session_id,
        "workflow": workflow,
        "live_session_id": live_session_id,
        "session_id": live_session_id,
        "transcript": state.transcript,
        "is_final": state.is_final,
        "error": state.error,
    }


def _post_interaction_live_finish_transcript(
    body: Any,
    live_asr_manager: LiveAsrSessionManager | None,
) -> JSONResponse:
    request = InteractionLiveSessionRequest.from_dict(body or {})
    _require_interaction_session_workflow(
        request.interaction_session_id,
        request.workflow,
    )
    manager = live_asr_manager or get_default_live_asr_manager()
    state = manager.finish_session(request.live_session_id)
    if state.error:
        raise ValueError(state.error)
    return 200, {
        "interaction_session_id": request.interaction_session_id,
        "workflow": request.workflow,
        "live_session_id": request.live_session_id,
        "session_id": request.live_session_id,
        "transcript": state.transcript,
        "is_final": state.is_final,
        "error": state.error,
    }


def _post_interaction_live_abort(
    body: Any,
    live_asr_manager: LiveAsrSessionManager | None,
) -> JSONResponse:
    request = InteractionLiveSessionRequest.from_dict(body or {})
    _require_interaction_session_workflow(
        request.interaction_session_id,
        request.workflow,
    )
    manager = live_asr_manager or get_default_live_asr_manager()
    manager.abort_session(request.live_session_id)
    return 200, {
        "ok": True,
        "interaction_session_id": request.interaction_session_id,
        "workflow": request.workflow,
        "live_session_id": request.live_session_id,
    }


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


def _require_interaction_session_workflow(
    interaction_session_id: str,
    workflow: str,
) -> dict:
    session = interaction_store.get_session(interaction_session_id)
    if session["workflow"] != workflow:
        raise ValueError(
            f"interaction session workflow is {session['workflow']!r}, got {workflow!r}"
        )
    if session["status"] != interaction_store.SESSION_ACTIVE:
        raise ValueError("interaction session must be active")
    return session


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


def _post_voice_live_finish_transcript(
    body: Any,
    live_asr_manager: LiveAsrSessionManager | None,
) -> JSONResponse:
    request = LiveVoiceFinishTranscriptRequest.from_dict(body or {})
    manager = live_asr_manager or get_default_live_asr_manager()
    state = manager.finish_session(request.session_id)
    if state.error:
        raise ValueError(state.error)
    response = LiveVoiceTranscriptResponse(
        session_id=request.session_id,
        transcript=state.transcript,
        is_final=state.is_final,
        error=state.error,
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

    yield from _iter_dialogue_reply_stream_events(
        lambda: handle_chat_message_stream(
            request.conversation_id,
            transcript,
            dependencies=_build_no_db_latency_dependencies(),
        ),
        transcript=transcript,
        tts_enabled=request.tts_enabled,
        audio_dependencies=audio_dependencies,
    )


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

    yield from _iter_dialogue_reply_stream_events(
        lambda: handle_chat_message_stream(
            request.conversation_id,
            transcript,
            dependencies=dependencies,
        ),
        transcript=transcript,
        tts_enabled=request.tts_enabled,
        audio_dependencies=audio_dependencies,
    )


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


def _get_demo_profile() -> JSONResponse:
    return 200, get_demo_profile_state()


def _post_demo_profile(body: Any) -> JSONResponse:
    if not isinstance(body, dict):
        raise DemoProfileError("request body must be an object")
    profile = body.get("profile")
    return 200, switch_demo_profile(profile)


def _error(status: int, message: str) -> JSONResponse:
    return status, {"error": {"message": message}}


def _query_bool(query: dict[str, list[str]], key: str, default: bool) -> bool:
    values = query.get(key)
    if not values:
        return default
    value = values[-1].strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{key} must be a boolean")


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


class _StreamWorkerError:
    def __init__(self, error: BaseException) -> None:
        self.error = error


def _iter_dialogue_reply_stream_events(
    chat_events_factory: Callable[[], Iterable[dict]],
    *,
    transcript: str | None = None,
    tts_enabled: bool,
    audio_dependencies: AudioDependencies | None = None,
):
    include_audio_done_fields = tts_enabled or transcript is not None
    if not tts_enabled:
        for item in chat_events_factory():
            if item.get("event") == "done":
                yield _dialogue_done_event(
                    item,
                    transcript=transcript,
                    include_audio_fields=include_audio_done_fields,
                )
                continue
            yield item
        return

    output_queue: queue.Queue[Any] = queue.Queue()
    tts_queue: queue.Queue[Any] = queue.Queue()
    done_holder: dict[str, dict] = {}
    audio_identity_holder: dict[str, dict] = {}

    def enqueue_tts_segment(raw_text: str) -> None:
        tts_text = prepare_tts_text(raw_text)
        if tts_text.strip():
            tts_queue.put(tts_text)

    def chat_worker() -> None:
        segmenter = _StreamingTtsSegmenter()
        try:
            for item in chat_events_factory():
                if item.get("event") == "meta":
                    data = item.get("data", {})
                    audio_identity_holder["data"] = _stream_audio_identity(data)
                    output_queue.put(item)
                    continue

                if item.get("event") == "delta":
                    output_queue.put(item)
                    delta = item.get("data", {}).get("delta")
                    if isinstance(delta, str) and delta:
                        for segment in segmenter.feed(delta):
                            enqueue_tts_segment(segment)
                    continue

                if item.get("event") == "done":
                    data = item.get("data", {})
                    if "data" not in audio_identity_holder:
                        audio_identity_holder["data"] = _stream_audio_identity(data)
                    for segment in segmenter.flush():
                        enqueue_tts_segment(segment)
                    done_holder["event"] = _dialogue_done_event(
                        item,
                        transcript=transcript,
                        include_audio_fields=include_audio_done_fields,
                    )
                    tts_queue.put(_TTS_SENTINEL)
                    return

                output_queue.put(item)
            tts_queue.put(_TTS_SENTINEL)
        except BaseException as exc:  # pragma: no cover - defensive stream path
            output_queue.put(_StreamWorkerError(exc))
            tts_queue.put(_TTS_SENTINEL)

    def tts_worker() -> None:
        try:
            audio_deps = audio_dependencies or AudioDependencies()
            tts_client = audio_deps.tts_client
            stream_fn: Callable[[str], Iterable[bytes]] | None = None
            sample_rate = 24000
            chunk_index = 0
            segment_index = 0

            while True:
                segment = tts_queue.get()
                if segment is _TTS_SENTINEL:
                    done_event = done_holder.get("event")
                    if done_event is not None:
                        output_queue.put(done_event)
                    output_queue.put(_STREAM_SENTINEL)
                    return

                if tts_client is None:
                    from src.audio.tts import build_default_tts_client

                    tts_client = build_default_tts_client()
                    sample_rate = getattr(tts_client, "sample_rate", 24000)
                    candidate_stream_fn = getattr(tts_client, "synthesize_stream", None)
                    if callable(candidate_stream_fn):
                        stream_fn = candidate_stream_fn
                elif stream_fn is None:
                    sample_rate = getattr(tts_client, "sample_rate", 24000)
                    candidate_stream_fn = getattr(tts_client, "synthesize_stream", None)
                    if callable(candidate_stream_fn):
                        stream_fn = candidate_stream_fn

                if callable(stream_fn):
                    chunks = stream_fn(segment)
                else:
                    chunks = [tts_client.synthesize(segment)]

                for chunk in chunks:
                    if not chunk:
                        continue
                    audio_data = dict(audio_identity_holder.get("data", {}))
                    audio_data.update(
                        {
                            "audio_base64": base64.b64encode(chunk).decode("ascii"),
                            "audio_format": "pcm",
                            "sample_rate": sample_rate,
                            "chunk_index": chunk_index,
                            "segment_index": segment_index,
                        }
                    )
                    output_queue.put(
                        {
                            "event": "audio",
                            "data": audio_data,
                        }
                    )
                    chunk_index += 1
                segment_index += 1
        except BaseException as exc:  # pragma: no cover - defensive stream path
            output_queue.put(_StreamWorkerError(exc))
            output_queue.put(_STREAM_SENTINEL)

    threads = [
        threading.Thread(target=tts_worker, name="voice-stream-tts", daemon=True),
        threading.Thread(target=chat_worker, name="voice-stream-chat", daemon=True),
    ]
    for thread in threads:
        thread.start()

    try:
        while True:
            item = output_queue.get()
            if item is _STREAM_SENTINEL:
                return
            if isinstance(item, _StreamWorkerError):
                raise item.error
            yield item
    finally:
        tts_queue.put(_TTS_SENTINEL)


def _dialogue_done_event(
    item: dict,
    *,
    transcript: str | None = None,
    include_audio_fields: bool = False,
) -> dict:
    data = dict(item.get("data", {}))
    if transcript is not None:
        data["transcript"] = transcript
    if include_audio_fields:
        data["audio_base64"] = None
        data["audio_format"] = "pcm"
    return {"event": "done", "data": data}


def _stream_audio_identity(data: dict) -> dict:
    identity = {
        "conversation_id": data.get("conversation_id", ""),
        "request_id": data.get("request_id", ""),
        "turn_id": data.get("turn_id", ""),
        "phase": "initial",
    }
    for key in (
        "workflow",
        "interaction_session_id",
        "run_id",
        "onboarding_session_id",
        "stage",
        "stage_key",
        "stage_name",
        "playback_key",
    ):
        value = data.get(key)
        if value:
            identity[key] = value
    return identity


class _StreamingTtsSegmenter:
    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, delta: str) -> list[str]:
        self._buffer += delta
        return self._pop_ready_segments()

    def flush(self) -> list[str]:
        if not self._buffer.strip():
            self._buffer = ""
            return []
        segment = self._buffer
        self._buffer = ""
        return [segment]

    def _pop_ready_segments(self) -> list[str]:
        segments: list[str] = []
        while True:
            split_at = self._find_punctuation_split()
            if split_at is None and self._can_split_by_length():
                split_at = len(self._buffer)
            if split_at is None:
                return segments

            segment = self._buffer[:split_at]
            self._buffer = self._buffer[split_at:]
            if segment.strip():
                segments.append(segment)

    def _find_punctuation_split(self) -> int | None:
        for index, char in enumerate(self._buffer):
            if char in _TTS_SEGMENT_PUNCTUATION:
                return index + 1
        return None

    def _can_split_by_length(self) -> bool:
        if len(self._buffer) < _TTS_SEGMENT_MAX_CHARS:
            return False
        last_open = self._buffer.rfind("[")
        last_close = self._buffer.rfind("]")
        return last_open <= last_close


def _iter_tts_audio_events(
    text: str,
    *,
    audio_dependencies: AudioDependencies | None = None,
    extra_data: dict | None = None,
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
        data = dict(extra_data or {})
        data.update(
            {
                "audio_base64": base64.b64encode(chunk).decode("ascii"),
                "audio_format": "pcm",
                "sample_rate": sample_rate,
                "chunk_index": index,
            }
        )
        yield {
            "event": "audio",
            "data": data,
        }


# ---------------------------------------------------------------------------
# BaseHTTPRequestHandler glue
# ---------------------------------------------------------------------------


def create_request_handler(
    dependencies: DialogueDependencies | None = None,
    audio_dependencies: AudioDependencies | None = None,
    live_asr_manager: LiveAsrSessionManager | None = None,
    onboarding_dependencies: OnboardingDependencies | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Return a request handler class bound to ``dependencies``."""

    class _BoundHandler(ChatRequestHandler):
        injected_dependencies = dependencies
        injected_audio_dependencies = audio_dependencies
        injected_live_asr_manager = live_asr_manager
        injected_onboarding_dependencies = onboarding_dependencies

    return _BoundHandler


class ChatRequestHandler(BaseHTTPRequestHandler):
    """HTTP adapter that forwards requests to :func:`dispatch`."""

    injected_dependencies: DialogueDependencies | None = None
    injected_audio_dependencies: AudioDependencies | None = None
    injected_live_asr_manager: LiveAsrSessionManager | None = None
    injected_onboarding_dependencies: OnboardingDependencies | None = None
    server_version = "ChatService/0.1"

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        self._handle("POST")

    def do_OPTIONS(self) -> None:  # noqa: N802 - http.server naming
        self.send_response(204)
        self._write_common_headers()
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server signature
        # Quieter logging that respects ``--quiet`` style usage. Callers can
        # subclass and override if they want detailed access logs.
        return None

    def _handle(self, method: str) -> None:
        if method == "GET" and urlsplit(self.path).path == "/tools/voice-latency":
            self._write_html_file(_VOICE_LATENCY_PAGE)
            return
        if method == "GET" and urlsplit(self.path).path == "/followups/stream":
            self._write_followups_stream()
            return

        body: Any = None
        if method == "POST":
            body = self._read_json_body()
            if body is _BODY_ERROR:
                return
            if urlsplit(self.path).path == "/chat/stream":
                self._write_chat_stream(body)
                return
            if urlsplit(self.path).path == "/interaction/runs/text-stream":
                self._write_interaction_text_stream(body)
                return
            if urlsplit(self.path).path == "/interaction/live/finish-stream":
                self._write_interaction_live_finish_stream(body)
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
            onboarding_dependencies=type(self).injected_onboarding_dependencies,
        )
        self._write_json(status, response_body)

    def _write_chat_stream(self, body: Any) -> None:
        try:
            request = ChatRequest.from_dict(body or {})
        except SchemaError as exc:
            self._write_json(400, {"error": {"message": str(exc)}})
            return

        self.send_response(200)
        self._write_common_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            for item in _iter_dialogue_reply_stream_events(
                lambda: handle_chat_message_stream(
                    request.conversation_id,
                    request.message,
                    dependencies=type(self).injected_dependencies,
                ),
                tts_enabled=request.tts_enabled,
                audio_dependencies=type(self).injected_audio_dependencies,
            ):
                event = item.get("event", "message")
                data = item.get("data", {})
                self._write_sse_event(event, data)
        except Exception as exc:  # pragma: no cover - defensive network path
            self._write_sse_event(
                "error",
                {"message": str(exc) or exc.__class__.__name__},
            )

    def _write_followups_stream(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        conversation_id = (query.get("conversation_id") or [""])[0]
        try:
            tts_enabled = _query_bool(query, "tts_enabled", False)
        except ValueError as exc:
            self._write_json(400, {"error": {"message": str(exc)}})
            return
        if not conversation_id:
            self._write_json(
                400,
                {"error": {"message": "conversation_id must be a non-empty string"}},
            )
            return

        self.send_response(200)
        self._write_common_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            for item in iter_followup_events(conversation_id):
                self._write_sse_event(item.get("event", "message"), item.get("data", {}))
                if item.get("event") != "followup" or not tts_enabled:
                    continue
                payload = item.get("data", {})
                reply = payload.get("reply")
                if not isinstance(reply, str) or not reply.strip():
                    continue
                audio_event_base = {
                    "conversation_id": payload.get("conversation_id", conversation_id),
                    "request_id": payload.get("request_id", ""),
                    "turn_id": payload.get("followup_turn_id", ""),
                    "followup_turn_id": payload.get("followup_turn_id", ""),
                    "phase": "followup",
                }
                for audio_event in _iter_tts_audio_events(
                    reply,
                    audio_dependencies=type(self).injected_audio_dependencies,
                    extra_data=audio_event_base,
                ):
                    self._write_sse_event(
                        audio_event.get("event", "message"),
                        audio_event.get("data", {}),
                    )
                done_data = dict(audio_event_base)
                done_data.update(
                    {
                        "audio_base64": None,
                        "audio_format": "pcm",
                    }
                )
                self._write_sse_event("followup_done", done_data)
        except (BrokenPipeError, ConnectionResetError):  # pragma: no cover - network path
            logger.info(
                "followup stream client disconnected conversation_id=%s",
                conversation_id,
            )
        except Exception as exc:  # pragma: no cover - defensive network path
            self._write_sse_event(
                "error",
                {"message": str(exc) or exc.__class__.__name__},
            )

    def _write_interaction_text_stream(self, body: Any) -> None:
        try:
            request = InteractionTextStreamRequest.from_dict(body or {})
        except SchemaError as exc:
            self._write_json(400, {"error": {"message": str(exc)}})
            return

        self.send_response(200)
        self._write_common_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            for item in _iter_dialogue_reply_stream_events(
                lambda: iter_text_interaction_events(
                    interaction_session_id=request.interaction_session_id,
                    workflow=request.workflow,
                    message=request.message,
                    dependencies=type(self).injected_dependencies,
                    onboarding_dependencies=type(self).injected_onboarding_dependencies,
                ),
                tts_enabled=request.tts_enabled,
                audio_dependencies=type(self).injected_audio_dependencies,
            ):
                event = item.get("event", "message")
                data = item.get("data", {})
                self._write_sse_event(event, data)
        except Exception as exc:  # pragma: no cover - defensive network path
            self._write_sse_event(
                "error",
                {"message": str(exc) or exc.__class__.__name__},
            )

    def _write_interaction_live_finish_stream(self, body: Any) -> None:
        try:
            request = InteractionLiveFinishStreamRequest.from_dict(body or {})
            _require_interaction_session_workflow(
                request.interaction_session_id,
                request.workflow,
            )
        except (SchemaError, ValueError, InteractionSessionNotFoundError) as exc:
            status = 404 if isinstance(exc, InteractionSessionNotFoundError) else 400
            self._write_json(status, {"error": {"message": str(exc)}})
            return

        self.send_response(200)
        self._write_common_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            manager = type(self).injected_live_asr_manager or get_default_live_asr_manager()
            state = manager.finish_session(request.live_session_id)
            if state.error:
                raise ValueError(state.error)
            transcript = state.transcript.strip()
            if not transcript:
                raise ValueError("speech recognition produced an empty transcript")

            reply_events = iter(
                _iter_dialogue_reply_stream_events(
                    lambda: iter_text_interaction_events(
                        interaction_session_id=request.interaction_session_id,
                        workflow=request.workflow,
                        message=transcript,
                        dependencies=type(self).injected_dependencies,
                        onboarding_dependencies=type(self).injected_onboarding_dependencies,
                    ),
                    transcript=transcript,
                    tts_enabled=request.tts_enabled,
                    audio_dependencies=type(self).injected_audio_dependencies,
                )
            )
            first_item = next(reply_events)
            first_data = dict(first_item.get("data") or {})
            transcript_data = {
                "workflow": request.workflow,
                "interaction_session_id": request.interaction_session_id,
                "run_id": first_data.get("run_id", ""),
                "conversation_id": first_data.get("conversation_id", ""),
                "onboarding_session_id": first_data.get("onboarding_session_id", ""),
                "live_session_id": request.live_session_id,
                "session_id": request.live_session_id,
                "transcript": transcript,
                "is_final": True,
            }
            self._write_sse_event("transcript", transcript_data)
            self._write_sse_event(
                first_item.get("event", "message"),
                first_item.get("data", {}),
            )
            for item in reply_events:
                self._write_sse_event(item.get("event", "message"), item.get("data", {}))
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
        self._write_common_headers()
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
        self._write_common_headers()
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
        self._write_common_headers()
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
        self._write_common_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _write_common_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


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
