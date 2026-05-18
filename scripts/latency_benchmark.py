"""Measure model reply latency for adjustable text scenarios.

This script intentionally lives outside the unit-test suite because real model
latency depends on network, provider load, and route configuration. Unit tests
cover the statistics logic; this command is for manual benchmark runs.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.models import ChatMessage, ChatResponse, ModelClient, chat_once


DEFAULT_PROMPTS = [
    "请用一句话介绍你现在能做什么。",
    "我今天有点累，但还想继续做项目。请给我一个很短的鼓励。",
    "请把下面这句话改得更自然：我现在想要测试延迟，然后计算平均值。",
    "请用三条要点解释长期记忆聊天 Demo 的核心价值。",
    "假设用户刚说完一段语音，请生成一句自然、简短、温和的回应。",
]


@dataclass(frozen=True)
class LatencySample:
    prompt_index: int
    iteration: int
    prompt: str
    prompt_chars: int
    elapsed_ms: float
    reply: str
    reply_chars: int
    reply_preview: str


@dataclass(frozen=True)
class LatencySummary:
    count: int
    average_ms: float
    median_ms: float
    min_ms: float
    max_ms: float
    stdev_ms: float


@dataclass(frozen=True)
class LatencyBenchmarkResult:
    route: str | None
    samples: list[LatencySample]
    summary: LatencySummary
    saved_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "saved_at": self.saved_at,
            "samples": [asdict(sample) for sample in self.samples],
            "summary": asdict(self.summary),
        }


ChatCall = Callable[[list[ChatMessage]], ChatResponse]


def run_text_latency_benchmark(
    prompts: list[str],
    *,
    repeat: int = 1,
    route: str | None = "dialogue.initial",
    system_prompt: str = "你是一个自然、简洁的中文对话助手。",
    client: ModelClient | None = None,
    chat_call: ChatCall | None = None,
    progress: Callable[[str], None] | None = None,
) -> LatencyBenchmarkResult:
    """Call the configured model for every prompt and return latency stats."""

    clean_prompts = [prompt.strip() for prompt in prompts if prompt.strip()]
    if not clean_prompts:
        raise ValueError("at least one non-empty prompt is required")
    if repeat <= 0:
        raise ValueError("repeat must be a positive integer")

    samples: list[LatencySample] = []
    call = chat_call or (
        lambda messages: chat_once(messages, client=client, route=route)
    )

    for iteration in range(1, repeat + 1):
        for prompt_index, prompt in enumerate(clean_prompts, start=1):
            if progress:
                progress(
                    f"Running prompt {prompt_index}/{len(clean_prompts)} "
                    f"iteration {iteration}/{repeat}..."
                )
            messages = [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=prompt),
            ]
            started = time.perf_counter()
            response = call(messages)
            elapsed_ms = (time.perf_counter() - started) * 1000
            samples.append(
                LatencySample(
                    prompt_index=prompt_index,
                    iteration=iteration,
                    prompt=prompt,
                    prompt_chars=len(prompt),
                    elapsed_ms=elapsed_ms,
                    reply=response.content,
                    reply_chars=len(response.content),
                    reply_preview=response.content[:80],
                )
            )
            if progress:
                progress(
                    f"Done prompt {prompt_index}/{len(clean_prompts)} "
                    f"iteration {iteration}/{repeat}: {elapsed_ms:.1f}ms"
                )

    return LatencyBenchmarkResult(
        route=route,
        samples=samples,
        summary=_summarize(samples),
    )


def load_prompts(path: str | Path | None) -> list[str]:
    """Load prompts from a text or JSON file, or return defaults."""

    if path is None:
        return list(DEFAULT_PROMPTS)
    prompt_path = Path(path)
    raw = prompt_path.read_text(encoding="utf-8")
    if prompt_path.suffix.lower() == ".json":
        data = json.loads(raw)
        if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
            raise ValueError("prompt JSON must be a list of strings")
        return data
    return [line.strip() for line in raw.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure text model latency.")
    parser.add_argument(
        "--prompts",
        help="Text file with one prompt per line, or a JSON list of prompt strings.",
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--route", default="dialogue.initial")
    parser.add_argument(
        "--output-dir",
        default="data/latency-results",
        help="Directory where timestamped JSON results are saved.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the final report and saved file path.",
    )
    args = parser.parse_args(argv)

    result = run_text_latency_benchmark(
        load_prompts(args.prompts),
        repeat=args.repeat,
        route=args.route,
        progress=None if args.quiet else _print_progress,
    )

    output_path = save_benchmark_result(result, args.output_dir)
    _print_report(result)
    print(f"Saved JSON: {output_path}")
    return 0


def _summarize(samples: list[LatencySample]) -> LatencySummary:
    values = [sample.elapsed_ms for sample in samples]
    return LatencySummary(
        count=len(values),
        average_ms=statistics.fmean(values),
        median_ms=statistics.median(values),
        min_ms=min(values),
        max_ms=max(values),
        stdev_ms=statistics.stdev(values) if len(values) > 1 else 0.0,
    )


def save_benchmark_result(
    result: LatencyBenchmarkResult,
    output_dir: str | Path,
) -> Path:
    """Save benchmark result as a timestamped JSON file."""

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    route_name = _safe_filename_part(result.route or "default")
    output_path = Path(output_dir) / f"latency-{timestamp}-{route_name}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    saved = LatencyBenchmarkResult(
        route=result.route,
        samples=result.samples,
        summary=result.summary,
        saved_at=timestamp,
    )
    output_path.write_text(
        json.dumps(saved.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def _print_report(result: LatencyBenchmarkResult) -> None:
    route = result.route or "default"
    print(f"Text latency benchmark route={route}")
    for sample in result.samples:
        print(
            f"- prompt={sample.prompt_index} iteration={sample.iteration} "
            f"elapsed={sample.elapsed_ms:.1f}ms "
            f"prompt_chars={sample.prompt_chars} reply_chars={sample.reply_chars}"
        )
    summary = result.summary
    print(
        "Summary: "
        f"count={summary.count}, avg={summary.average_ms:.1f}ms, "
        f"median={summary.median_ms:.1f}ms, min={summary.min_ms:.1f}ms, "
        f"max={summary.max_ms:.1f}ms, stdev={summary.stdev_ms:.1f}ms"
    )


def _print_progress(message: str) -> None:
    print(message, flush=True)


def _safe_filename_part(value: str) -> str:
    safe = []
    for char in value:
        if char.isalnum() or char in {"-", "_"}:
            safe.append(char)
        else:
            safe.append("-")
    return "".join(safe).strip("-") or "default"


if __name__ == "__main__":
    sys.exit(main())
