"""Voice chat orchestration.

This module is the only place where speech adapters meet the existing
dialogue service: STT produces text, dialogue handles text, TTS optionally
turns the reply back into audio.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from src.audio.schemas import VoiceChatResponse
from src.audio.stt import SpeechToTextClient, build_default_stt_client
from src.audio.tts import TextToSpeechClient, build_default_tts_client
from src.services import DialogueDependencies, handle_chat_message


ChatHandler = Callable[..., dict]


@dataclass
class AudioDependencies:
    stt_client: SpeechToTextClient | None = None
    tts_client: TextToSpeechClient | None = None
    chat_handler: ChatHandler | None = None

    def with_overrides(self, **overrides: Any) -> "AudioDependencies":
        return replace(self, **overrides)

    def resolved(self) -> "AudioDependencies":
        return AudioDependencies(
            stt_client=self.stt_client or build_default_stt_client(),
            tts_client=self.tts_client or build_default_tts_client(),
            chat_handler=self.chat_handler or handle_chat_message,
        )


def handle_voice_chat(
    conversation_id: str,
    audio_bytes: bytes,
    *,
    audio_format: str = "pcm",
    tts_enabled: bool = True,
    dependencies: AudioDependencies | None = None,
    dialogue_dependencies: DialogueDependencies | None = None,
) -> VoiceChatResponse:
    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("conversation_id must be a non-empty string")
    if not isinstance(audio_bytes, (bytes, bytearray)) or not audio_bytes:
        raise ValueError("audio_bytes must be non-empty bytes")

    deps = (dependencies or AudioDependencies()).resolved()
    transcript = deps.stt_client.transcribe(bytes(audio_bytes), audio_format=audio_format)
    return handle_voice_reply_from_text(
        conversation_id,
        transcript,
        tts_enabled=tts_enabled,
        dependencies=deps,
        dialogue_dependencies=dialogue_dependencies,
    )


def handle_voice_reply_from_text(
    conversation_id: str,
    transcript: str,
    *,
    tts_enabled: bool = True,
    dependencies: AudioDependencies | None = None,
    dialogue_dependencies: DialogueDependencies | None = None,
) -> VoiceChatResponse:
    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("conversation_id must be a non-empty string")
    if not isinstance(transcript, str) or not transcript.strip():
        raise ValueError("transcript must be a non-empty string")
    transcript = transcript.strip()

    deps = dependencies or AudioDependencies()
    chat_handler = deps.chat_handler or handle_chat_message
    chat_payload = chat_handler(
        conversation_id,
        transcript,
        dependencies=dialogue_dependencies,
    )
    reply = chat_payload.get("reply")
    if not isinstance(reply, str):
        raise ValueError("chat handler must return a string reply")

    output_audio = None
    if tts_enabled:
        tts_client = deps.tts_client or build_default_tts_client()
        output_audio = tts_client.synthesize(reply)
    return VoiceChatResponse(
        conversation_id=chat_payload["conversation_id"],
        request_id=chat_payload["request_id"],
        turn_id=chat_payload["turn_id"],
        transcript=transcript,
        reply=reply,
        retrieval_status=chat_payload["retrieval_status"],
        retrieved_memory_ids=list(chat_payload.get("retrieved_memory_ids", [])),
        audio_bytes=output_audio,
    )
