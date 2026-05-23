"""Unified interaction orchestration for chat and onboarding workflows."""

from __future__ import annotations

from typing import Iterator

from src.interaction import store as interaction_store
from src.services.dialogue_service import DialogueDependencies, handle_chat_message_stream
from src.services.onboarding_service import (
    OnboardingDependencies,
    get_onboarding_status,
    start_onboarding,
)


WORKFLOW_CHAT = "chat"
WORKFLOW_ONBOARDING = "onboarding"
INPUT_MODE_TEXT = "text"


def create_interaction_session(
    *,
    workflow: str,
    conversation_id: str = "",
    input_mode: str = INPUT_MODE_TEXT,
    tts_enabled: bool = False,
    onboarding_dependencies: OnboardingDependencies | None = None,
) -> dict:
    """Create one interaction session and initialize workflow-owned state."""

    _validate_workflow(workflow)
    _validate_optional_string(conversation_id, "conversation_id")
    _validate_optional_string(input_mode, "input_mode")
    onboarding_payload: dict = {}
    onboarding_session_id = ""

    if workflow == WORKFLOW_ONBOARDING:
        onboarding_payload = start_onboarding(
            conversation_id or None,
            dependencies=onboarding_dependencies,
        )
        onboarding_session_id = onboarding_payload["session_id"]

    session = interaction_store.create_session(
        workflow=workflow,
        conversation_id=conversation_id,
        onboarding_session_id=onboarding_session_id,
        input_mode=input_mode,
    )
    return _session_response(
        session,
        onboarding_payload=onboarding_payload,
        tts_enabled=tts_enabled,
    )


def get_interaction_session_status(
    interaction_session_id: str,
    *,
    onboarding_dependencies: OnboardingDependencies | None = None,
) -> dict:
    """Return the unified status for one interaction session."""

    session = interaction_store.get_session(interaction_session_id)
    onboarding_payload: dict = {}
    if session["workflow"] == WORKFLOW_ONBOARDING and session.get("onboarding_session_id"):
        onboarding_payload = get_onboarding_status(
            session["onboarding_session_id"],
            dependencies=onboarding_dependencies,
        )
    return _session_response(session, onboarding_payload=onboarding_payload)


def iter_text_interaction_events(
    *,
    interaction_session_id: str,
    workflow: str,
    message: str,
    dependencies: DialogueDependencies | None = None,
) -> Iterator[dict]:
    """Run one text interaction and yield normalized SSE event dictionaries."""

    if not isinstance(message, str) or not message.strip():
        raise ValueError("message must be a non-empty string")
    _validate_workflow(workflow)
    session = interaction_store.get_session(interaction_session_id)
    if session["workflow"] != workflow:
        raise ValueError(
            f"interaction session workflow is {session['workflow']!r}, got {workflow!r}"
        )
    if session["status"] != interaction_store.SESSION_ACTIVE:
        raise ValueError("interaction session must be active")

    if workflow == WORKFLOW_ONBOARDING:
        raise NotImplementedError(
            "onboarding text interaction streaming belongs to phase 2"
        )

    yield from _iter_chat_text_interaction_events(
        session=session,
        message=message,
        dependencies=dependencies,
    )


def _iter_chat_text_interaction_events(
    *,
    session: dict,
    message: str,
    dependencies: DialogueDependencies | None,
) -> Iterator[dict]:
    run = interaction_store.create_run(
        interaction_session_id=session["interaction_session_id"],
        workflow=WORKFLOW_CHAT,
        input_mode=INPUT_MODE_TEXT,
        transcript=message,
    )
    run_id = run["run_id"]
    request_id = ""

    try:
        for item in handle_chat_message_stream(
            session["conversation_id"],
            message,
            dependencies=dependencies,
        ):
            event = item.get("event")
            data = dict(item.get("data") or {})
            if event == "meta":
                request_id = str(data.get("request_id") or "")
                if request_id:
                    interaction_store.update_run(run_id, request_id=request_id)
                yield {
                    "event": "meta",
                    "data": {
                        **data,
                        **_run_identity(session, run_id),
                        "playback_key": f"chat-tts-{run_id}",
                    },
                }
                continue

            if event == "delta":
                yield {
                    "event": "delta",
                    "data": {
                        **_run_identity(session, run_id),
                        "delta": data.get("delta", ""),
                    },
                }
                continue

            if event == "done":
                reply = str(data.get("reply") or "")
                request_id = str(data.get("request_id") or request_id)
                interaction_store.update_run(
                    run_id,
                    status=interaction_store.RUN_COMPLETED,
                    reply=reply,
                    request_id=request_id,
                )
                yield {
                    "event": "done",
                    "data": {
                        **data,
                        **_run_identity(session, run_id),
                        "request_id": request_id,
                    },
                }
                continue

            yield {
                "event": event or "message",
                "data": {**data, **_run_identity(session, run_id)},
            }
    except Exception as exc:
        interaction_store.update_run(
            run_id,
            status=interaction_store.RUN_FAILED,
            error=str(exc) or exc.__class__.__name__,
            request_id=request_id,
        )
        raise


def _run_identity(session: dict, run_id: str) -> dict:
    return {
        "workflow": session["workflow"],
        "interaction_session_id": session["interaction_session_id"],
        "run_id": run_id,
        "conversation_id": session["conversation_id"],
    }


def _session_response(
    session: dict,
    *,
    onboarding_payload: dict | None = None,
    tts_enabled: bool | None = None,
) -> dict:
    payload = {
        "interaction_session_id": session["interaction_session_id"],
        "workflow": session["workflow"],
        "conversation_id": session["conversation_id"],
        "status": session["status"],
        "input_mode": session["input_mode"],
    }
    if tts_enabled is not None:
        payload["tts_enabled"] = tts_enabled

    onboarding_payload = onboarding_payload or {}
    onboarding_session_id = (
        session.get("onboarding_session_id")
        or onboarding_payload.get("session_id")
        or ""
    )
    if onboarding_session_id:
        payload["onboarding_session_id"] = onboarding_session_id
    if onboarding_payload:
        payload.update(
            {
                "stage": onboarding_payload.get("stage"),
                "stage_key": onboarding_payload.get("stage_key"),
                "stage_name": onboarding_payload.get("stage_name"),
                "onboarding_complete": onboarding_payload.get(
                    "onboarding_complete",
                    False,
                ),
                "collected": dict(onboarding_payload.get("collected") or {}),
                "missing_required_slots": list(
                    onboarding_payload.get("missing_required_slots") or []
                ),
            }
        )
        if onboarding_payload.get("final_payload"):
            payload["final_payload"] = dict(onboarding_payload["final_payload"])
    return payload


def _validate_workflow(value: str) -> None:
    if value not in {WORKFLOW_CHAT, WORKFLOW_ONBOARDING}:
        raise ValueError("workflow must be one of {'chat', 'onboarding'}")


def _validate_optional_string(value: str, field_name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
