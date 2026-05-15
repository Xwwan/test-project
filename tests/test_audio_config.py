"""Tests for speech provider config builders."""

from __future__ import annotations

import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from src.audio.stt import build_default_stt_client
from src.audio.tts import build_default_tts_client


class AudioConfigTest(unittest.TestCase):
    def test_build_default_stt_client_reads_volcengine_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.yaml"
            path.write_text(
                textwrap.dedent(
                    """
                    audio:
                      stt:
                        provider: volcengine
                        url: wss://example.invalid/asr
                        app_id_env: TEST_VOLC_APP_ID
                        access_key_env: TEST_VOLC_ACCESS_KEY
                        resource_id_env: TEST_VOLC_RESOURCE_ID
                        sample_rate: 16000
                    """
                ),
                encoding="utf-8",
            )
            os.environ["TEST_VOLC_APP_ID"] = "app"
            os.environ["TEST_VOLC_ACCESS_KEY"] = "key"
            os.environ["TEST_VOLC_RESOURCE_ID"] = "resource"

            client = build_default_stt_client(config_path=path)

        assert client.url == "wss://example.invalid/asr"
        assert client.app_id == "app"
        assert client.access_key == "key"
        assert client.resource_id == "resource"

    def test_build_default_tts_client_reads_dashscope_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.yaml"
            path.write_text(
                textwrap.dedent(
                    """
                    audio:
                      tts:
                        provider: dashscope
                        realtime_url: wss://example.invalid/tts
                        api_key_env: TEST_DASHSCOPE_KEY
                        model: qwen3-tts-flash-realtime
                        voice: Cherry
                        sample_rate: 24000
                    """
                ),
                encoding="utf-8",
            )
            os.environ["TEST_DASHSCOPE_KEY"] = "dash"

            client = build_default_tts_client(config_path=path)

        assert client.realtime_url == "wss://example.invalid/tts"
        assert client.api_key == "dash"
        assert client.model == "qwen3-tts-flash-realtime"
        assert client.voice == "Cherry"

    def test_build_default_stt_client_rejects_unknown_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.yaml"
            path.write_text(
                "audio:\n  stt:\n    provider: unknown\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                build_default_stt_client(config_path=path)


if __name__ == "__main__":
    unittest.main()
