import unittest
from unittest.mock import patch

from src.onboarding import dev_server


class FakeTtsClient:
    sample_rate = 24000

    def synthesize(self, text):
        raise AssertionError("stream endpoint must not call synthesize")

    def synthesize_stream(self, text):
        yield b"a"
        yield b"b"


class OnboardingDevServerTest(unittest.TestCase):
    def test_tts_stream_uses_synthesize_stream(self):
        with patch(
            "src.onboarding.dev_server.build_default_tts_client",
            return_value=FakeTtsClient(),
        ):
            metadata, chunks = dev_server.iter_voice_tts_stream({"text": "你好"})

        assert metadata == {"sample_rate": 24000, "audio_format": "pcm"}
        assert list(chunks) == [b"a", b"b"]


if __name__ == "__main__":
    unittest.main()
