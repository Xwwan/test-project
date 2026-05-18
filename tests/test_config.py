"""Tests for YAML app config loading."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from src.utils.config import load_app_config


class ConfigLoaderTest(unittest.TestCase):
    def test_load_app_config_reads_base_yaml_without_local_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.yaml"
            path.write_text(
                textwrap.dedent(
                    """
                    routes:
                      dialogue:
                        initial:
                          provider: openai_default
                          model: gpt-5.5
                    """
                ),
                encoding="utf-8",
            )

            config = load_app_config(path)

        assert config["routes"]["dialogue"]["initial"]["provider"] == "openai_default"
        assert config["routes"]["dialogue"]["initial"]["model"] == "gpt-5.5"

    def test_load_app_config_overlays_local_yaml_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.yaml"
            local_path = Path(tmp) / "app.local.yaml"
            path.write_text(
                textwrap.dedent(
                    """
                    providers:
                      openai_default:
                        type: openai
                    routes:
                      dialogue:
                        initial:
                          provider: openai_default
                          model: gpt-5.5
                          temperature: 0.7
                    """
                ),
                encoding="utf-8",
            )
            local_path.write_text(
                textwrap.dedent(
                    """
                    providers:
                      yunwu:
                        type: openai-compatible
                        base_url: https://yunwu.ai/v1
                    routes:
                      dialogue:
                        initial:
                          provider: yunwu
                          model: gpt-5.5-mini
                    """
                ),
                encoding="utf-8",
            )

            config = load_app_config(path)

        assert config["providers"]["openai_default"]["type"] == "openai"
        assert config["providers"]["yunwu"]["base_url"] == "https://yunwu.ai/v1"
        initial = config["routes"]["dialogue"]["initial"]
        assert initial["provider"] == "yunwu"
        assert initial["model"] == "gpt-5.5-mini"
        assert initial["temperature"] == 0.7

    def test_load_app_config_rejects_non_mapping_local_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.yaml"
            local_path = Path(tmp) / "app.local.yaml"
            path.write_text("routes: {}\n", encoding="utf-8")
            local_path.write_text("- not\n- mapping\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                load_app_config(path)


if __name__ == "__main__":
    unittest.main()
