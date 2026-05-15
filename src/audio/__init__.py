"""Audio input/output adapters for voice chat."""

from .schemas import VoiceChatRequest, VoiceChatResponse
from .service import AudioDependencies, handle_voice_chat

__all__ = [
    "AudioDependencies",
    "VoiceChatRequest",
    "VoiceChatResponse",
    "handle_voice_chat",
]
