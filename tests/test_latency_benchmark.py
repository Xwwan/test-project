"""Tests for latency benchmark statistics."""

from __future__ import annotations

import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.latency_benchmark import (
    load_prompts,
    run_text_latency_benchmark,
    save_benchmark_result,
)
from src.models import ChatResponse


class LatencyBenchmarkTest(unittest.TestCase):
    def test_run_text_latency_benchmark_collects_samples_and_summary(self) -> None:
        times = iter([10.0, 10.1, 20.0, 20.3])
        seen_prompts: list[str] = []

        def fake_chat(messages):
            seen_prompts.append(messages[-1].content)
            return ChatResponse(content="回复", raw={})

        with patch("scripts.latency_benchmark.time.perf_counter", side_effect=times):
            result = run_text_latency_benchmark(
                ["第一段", "第二段"],
                repeat=1,
                route="dialogue.initial",
                chat_call=fake_chat,
            )

        self.assertEqual(seen_prompts, ["第一段", "第二段"])
        self.assertEqual(result.summary.count, 2)
        self.assertEqual(result.samples[0].prompt, "第一段")
        self.assertEqual(result.samples[0].reply, "回复")
        self.assertAlmostEqual(result.samples[0].elapsed_ms, 100.0)
        self.assertAlmostEqual(result.samples[1].elapsed_ms, 300.0)
        self.assertAlmostEqual(result.summary.average_ms, 200.0)
        self.assertAlmostEqual(result.summary.median_ms, 200.0)

    def test_run_text_latency_benchmark_rejects_empty_prompts(self) -> None:
        with self.assertRaises(ValueError):
            run_text_latency_benchmark(["", "   "], chat_call=lambda messages: None)

    def test_load_prompts_reads_one_prompt_per_line(self) -> None:
        path = "/private/tmp/latency-prompts.txt"
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("一\n\n二\n")

        self.assertEqual(load_prompts(path), ["一", "二"])

    def test_save_benchmark_result_writes_timestamped_json_with_full_reply(self) -> None:
        with patch("scripts.latency_benchmark.time.perf_counter", side_effect=[1.0, 1.2]):
            result = run_text_latency_benchmark(
                ["第一段"],
                route="dialogue.initial",
                chat_call=lambda messages: ChatResponse(content="完整回复", raw={}),
            )

        with TemporaryDirectory() as directory:
            with patch("scripts.latency_benchmark.datetime") as fake_datetime:
                fake_datetime.now.return_value.strftime.return_value = "20260518-121314"
                path = save_benchmark_result(result, directory)

            self.assertEqual(
                path.name,
                "latency-20260518-121314-dialogue-initial.json",
            )
            saved = Path(path).read_text(encoding="utf-8")
            self.assertIn('"prompt": "第一段"', saved)
            self.assertIn('"reply": "完整回复"', saved)


if __name__ == "__main__":
    unittest.main()
