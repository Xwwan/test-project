"""Tests for Volcengine ASR binary protocol helpers."""

from __future__ import annotations

import gzip
import json
import unittest

from src.audio.volcengine_asr import (
    AsrErrorFrame,
    build_audio_request_frame,
    build_full_client_request_frame,
    parse_server_frame,
    split_pcm_audio,
)


class VolcengineAsrProtocolTest(unittest.TestCase):
    def test_build_full_client_request_frame_uses_json_gzip_payload(self) -> None:
        frame = build_full_client_request_frame(
            {
                "audio": {"format": "pcm"},
                "request": {"model_name": "bigmodel"},
            }
        )

        assert frame[0] == 0x11
        assert frame[1] == 0x11
        assert frame[2] == 0x11
        assert int.from_bytes(frame[4:8], "big", signed=True) == 1
        payload_size = int.from_bytes(frame[8:12], "big")
        payload = json.loads(gzip.decompress(frame[12 : 12 + payload_size]).decode("utf-8"))
        assert payload["request"]["model_name"] == "bigmodel"

    def test_build_audio_request_frame_marks_final_packet(self) -> None:
        frame = build_audio_request_frame(b"pcm", is_final=True)

        assert frame[1] == 0x23
        assert int.from_bytes(frame[4:8], "big", signed=True) == -1
        payload_size = int.from_bytes(frame[8:12], "big")
        assert gzip.decompress(frame[12 : 12 + payload_size]) == b"pcm"

    def test_parse_final_server_result_frame(self) -> None:
        payload = gzip.compress(json.dumps({"result": {"text": "你好"}}).encode("utf-8"))
        frame = (
            bytes([0x11, 0x93, 0x11, 0])
            + (1).to_bytes(4, "big", signed=True)
            + len(payload).to_bytes(4, "big")
            + payload
        )

        parsed = parse_server_frame(frame)

        assert parsed.kind == "result"
        assert parsed.sequence == 1
        assert parsed.is_final is True
        assert parsed.text == "你好"
        assert parsed.payload["result"]["text"] == "你好"

    def test_parse_error_server_frame(self) -> None:
        payload = gzip.compress(json.dumps({"message": "bad auth"}).encode("utf-8"))
        frame = (
            bytes([0x11, 0xF0, 0x11, 0])
            + (401).to_bytes(4, "big", signed=True)
            + len(payload).to_bytes(4, "big")
            + payload
        )

        parsed = parse_server_frame(frame)

        assert isinstance(parsed, AsrErrorFrame)
        assert parsed.kind == "error"
        assert parsed.code == 401
        assert parsed.payload["message"] == "bad auth"

    def test_split_pcm_audio_uses_160ms_chunks_by_default(self) -> None:
        chunks = split_pcm_audio(b"0" * 7000)

        assert [len(chunk) for chunk in chunks] == [5120, 1880]


if __name__ == "__main__":
    unittest.main()
