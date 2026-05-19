"""Tests for reply expression/action tag handling."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from src.services.reply_tags import (
    ReplyTagConfig,
    load_reply_tag_config,
    strip_configured_reply_tags,
)


class ReplyTagsTest(unittest.TestCase):
    def test_strip_configured_reply_tags_removes_english_emoji_and_chinese_keys(
        self,
    ) -> None:
        config = ReplyTagConfig(
            emo=frozenset({"angry", "😧"}),
            act=frozenset({"开心"}),
        )

        result = strip_configured_reply_tags(
            "我有点生气。[emo:angry]啊？[emo:😧] 好吧。[act:开心]",
            config,
        )

        assert result == "我有点生气。啊？ 好吧。"

    def test_strip_configured_reply_tags_removes_all_emo_and_act_tags(self) -> None:
        config = ReplyTagConfig(
            emo=frozenset({"angry"}),
            act=frozenset({"开心"}),
        )

        result = strip_configured_reply_tags(
            "你好[emo:excited]，我会过滤未知动作[act:跳舞]，也过滤表情动作[act:😁]",
            config,
        )

        assert result == "你好，我会过滤未知动作，也过滤表情动作"

    def test_strip_configured_reply_tags_accepts_case_and_chinese_colon(self) -> None:
        config = ReplyTagConfig()

        result = strip_configured_reply_tags(
            "[EMO：smug][ ACT : 😁 ]你好呀",
            config,
        )

        assert result == "你好呀"

    def test_strip_configured_reply_tags_trims_spaces_inside_key(self) -> None:
        config = ReplyTagConfig(
            emo=frozenset({"开心"}),
            act=frozenset({"sad"}),
        )

        result = strip_configured_reply_tags(
            "好的 [emo: 开心 ]，我知道了 [act: sad ]!",
            config,
        )

        assert result == "好的，我知道了!"

    def test_load_reply_tag_config_reads_yaml_lists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.yaml"
            path.write_text(
                textwrap.dedent(
                    """
                    reply_tags:
                      emo:
                        - angry
                        - 😧
                      act:
                        - 开心
                    """
                ),
                encoding="utf-8",
            )

            config = load_reply_tag_config(path)

        assert config.emo == frozenset({"angry", "😧"})
        assert config.act == frozenset({"开心"})


if __name__ == "__main__":
    unittest.main()
