"""Volcengine Bigmodel ASR protocol helpers.

The provider uses a compact binary frame format around gzip-compressed JSON
and audio payloads. This module keeps that protocol isolated from API and
service orchestration code.
"""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import json
from typing import Any


PROTOCOL_VERSION = 0x1
HEADER_SIZE_UNITS = 0x1
HEADER_SIZE_BYTES = HEADER_SIZE_UNITS * 4

MESSAGE_TYPE_FULL_CLIENT_REQUEST = 0x1
MESSAGE_TYPE_AUDIO_ONLY_REQUEST = 0x2
MESSAGE_TYPE_FULL_SERVER_RESPONSE = 0x9
MESSAGE_TYPE_ERROR_RESPONSE = 0xF

MESSAGE_FLAG_NONE = 0x0
MESSAGE_FLAG_HAS_SEQUENCE = 0x1
MESSAGE_FLAG_LAST_PACKET = 0x2
MESSAGE_FLAG_LAST_PACKET_WITH_SEQUENCE = 0x3

SERIALIZATION_NONE = 0x0
SERIALIZATION_JSON = 0x1

COMPRESSION_NONE = 0x0
COMPRESSION_GZIP = 0x1


@dataclass(frozen=True)
class AsrResultFrame:
    kind: str
    payload: dict[str, Any]
    text: str
    is_final: bool
    sequence: int | None = None


@dataclass(frozen=True)
class AsrErrorFrame:
    kind: str
    code: int
    payload: Any
    sequence: int | None = None


AsrServerFrame = AsrResultFrame | AsrErrorFrame


def build_full_client_request_frame(
    request_payload: dict[str, Any],
    *,
    sequence: int = 1,
) -> bytes:
    """Build the initial ASR config frame."""

    payload = gzip.compress(json.dumps(request_payload, ensure_ascii=False).encode("utf-8"))
    return _build_frame(
        message_type=MESSAGE_TYPE_FULL_CLIENT_REQUEST,
        flags=MESSAGE_FLAG_HAS_SEQUENCE,
        serialization=SERIALIZATION_JSON,
        compression=COMPRESSION_GZIP,
        payload=payload,
        sequence=sequence,
    )


def build_audio_request_frame(
    audio: bytes,
    *,
    is_final: bool = False,
    sequence: int = 1,
) -> bytes:
    """Build one audio frame for Volcengine ASR."""

    payload = gzip.compress(bytes(audio))
    return _build_frame(
        message_type=MESSAGE_TYPE_AUDIO_ONLY_REQUEST,
        flags=MESSAGE_FLAG_LAST_PACKET_WITH_SEQUENCE
        if is_final
        else MESSAGE_FLAG_HAS_SEQUENCE,
        serialization=SERIALIZATION_NONE,
        compression=COMPRESSION_GZIP,
        payload=payload,
        sequence=-abs(sequence) if is_final else sequence,
    )


def parse_server_frame(frame: bytes) -> AsrServerFrame:
    """Parse a server frame into a normalized result or error object."""

    buffer = bytes(frame)
    if len(buffer) < HEADER_SIZE_BYTES:
        raise ValueError("invalid Volcengine ASR frame: header too short")

    header_size = (buffer[0] & 0x0F) * 4
    message_type = (buffer[1] >> 4) & 0x0F
    flags = buffer[1] & 0x0F
    serialization = (buffer[2] >> 4) & 0x0F
    compression = buffer[2] & 0x0F
    offset = header_size

    sequence: int | None = None
    if flags in {MESSAGE_FLAG_HAS_SEQUENCE, MESSAGE_FLAG_LAST_PACKET_WITH_SEQUENCE}:
        _require_length(buffer, offset, 4)
        sequence = int.from_bytes(buffer[offset : offset + 4], "big", signed=True)
        offset += 4

    if message_type == MESSAGE_TYPE_FULL_SERVER_RESPONSE:
        _require_length(buffer, offset, 4)
        payload_size = int.from_bytes(buffer[offset : offset + 4], "big")
        offset += 4
        _require_length(buffer, offset, payload_size)
        payload = _decode_payload(
            buffer[offset : offset + payload_size],
            serialization=serialization,
            compression=compression,
        )
        if not isinstance(payload, dict):
            raise ValueError("Volcengine ASR result payload must be a JSON object")
        result = payload.get("result")
        text = result.get("text", "") if isinstance(result, dict) else ""
        return AsrResultFrame(
            kind="result",
            payload=payload,
            text=text if isinstance(text, str) else "",
            is_final=flags in {MESSAGE_FLAG_LAST_PACKET, MESSAGE_FLAG_LAST_PACKET_WITH_SEQUENCE},
            sequence=sequence,
        )

    if message_type == MESSAGE_TYPE_ERROR_RESPONSE:
        _require_length(buffer, offset, 8)
        code = int.from_bytes(buffer[offset : offset + 4], "big", signed=True)
        offset += 4
        payload_size = int.from_bytes(buffer[offset : offset + 4], "big")
        offset += 4
        _require_length(buffer, offset, payload_size)
        payload = _decode_payload(
            buffer[offset : offset + payload_size],
            serialization=serialization,
            compression=compression,
        )
        return AsrErrorFrame(kind="error", code=code, payload=payload, sequence=sequence)

    raise ValueError(f"unsupported Volcengine ASR message type: {message_type}")


def split_pcm_audio(
    audio: bytes,
    *,
    chunk_duration_ms: int = 160,
    sample_rate: int = 16000,
    sample_bytes: int = 2,
    channels: int = 1,
) -> list[bytes]:
    """Split PCM audio into provider-friendly chunks."""

    bytes_per_chunk = max(
        1,
        int(sample_rate * sample_bytes * channels * chunk_duration_ms / 1000),
    )
    return [
        bytes(audio[offset : offset + bytes_per_chunk])
        for offset in range(0, len(audio), bytes_per_chunk)
    ]


def _build_frame(
    *,
    message_type: int,
    flags: int,
    serialization: int,
    compression: int,
    payload: bytes,
    sequence: int | None = None,
) -> bytes:
    header = bytes(
        [
            (PROTOCOL_VERSION << 4) | HEADER_SIZE_UNITS,
            (message_type << 4) | flags,
            (serialization << 4) | compression,
            0,
        ]
    )
    sequence_bytes = (
        int(sequence).to_bytes(4, "big", signed=True) if sequence is not None else b""
    )
    return header + sequence_bytes + len(payload).to_bytes(4, "big") + payload


def _decode_payload(
    payload: bytes,
    *,
    serialization: int,
    compression: int,
) -> Any:
    decoded = gzip.decompress(payload) if compression == COMPRESSION_GZIP else payload
    if serialization == SERIALIZATION_JSON:
        return json.loads(decoded.decode("utf-8"))
    return decoded


def _require_length(buffer: bytes, offset: int, size: int) -> None:
    if len(buffer) < offset + size:
        raise ValueError("invalid Volcengine ASR frame: payload truncated")
