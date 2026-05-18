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

from src.models import ChatMessage, ModelClient, build_default_client, chat_stream


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
    model: str | None
    provider: str | None
    first_delta_ms: float
    total_ms: float
    reply: str
    reply_chars: int
    reply_preview: str
    deltas: list[str]


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
    model: str | None
    provider: str | None
    samples: list[LatencySample]
    summary: LatencySummary
    saved_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "model": self.model,
            "provider": self.provider,
            "saved_at": self.saved_at,
            "samples": [asdict(sample) for sample in self.samples],
            "summary": asdict(self.summary),
        }


StreamChatCall = Callable[[list[ChatMessage]], Any]


def run_text_latency_benchmark(
    prompts: list[str],
    *,
    repeat: int = 1,
    route: str | None = "dialogue.initial",
    system_prompt: str = "你是一个自然、简洁的中文对话助手。",
    client: ModelClient | None = None,
    chat_call: StreamChatCall | None = None,
    progress: Callable[[str], None] | None = None,
) -> LatencyBenchmarkResult:
    """Call the configured model for every prompt and return latency stats."""

    clean_prompts = [prompt.strip() for prompt in prompts if prompt.strip()]
    if not clean_prompts:
        raise ValueError("at least one non-empty prompt is required")
    if repeat <= 0:
        raise ValueError("repeat must be a positive integer")

    samples: list[LatencySample] = []
    active_client = client
    if chat_call is None and active_client is None:
        active_client = build_default_client(route=route)
    model_name, provider_name = _model_client_metadata(active_client)
    call = chat_call or (
        lambda messages: chat_stream(messages, client=active_client, route=route)
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
            deltas: list[str] = []
            first_delta_ms: float | None = None
            for delta in call(messages):
                if not isinstance(delta, str):
                    raise TypeError("stream chat call must yield strings")
                if not delta:
                    continue
                if first_delta_ms is None:
                    first_delta_ms = (time.perf_counter() - started) * 1000
                deltas.append(delta)
            total_ms = (time.perf_counter() - started) * 1000
            if first_delta_ms is None:
                raise ValueError("stream chat call did not yield any text delta")
            reply = "".join(deltas)
            samples.append(
                LatencySample(
                    prompt_index=prompt_index,
                    iteration=iteration,
                    prompt=prompt,
                    prompt_chars=len(prompt),
                    model=model_name,
                    provider=provider_name,
                    first_delta_ms=first_delta_ms,
                    total_ms=total_ms,
                    reply=reply,
                    reply_chars=len(reply),
                    reply_preview=reply[:80],
                    deltas=deltas,
                )
            )
            if progress:
                progress(
                    f"Done prompt {prompt_index}/{len(clean_prompts)} "
                    f"iteration {iteration}/{repeat}: "
                    f"first_delta={first_delta_ms:.1f}ms total={total_ms:.1f}ms"
                )

    return LatencyBenchmarkResult(
        route=route,
        model=model_name,
        provider=provider_name,
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
    values = [sample.first_delta_ms for sample in samples]
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
        model=result.model,
        provider=result.provider,
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
    model = result.model or "unknown"
    provider = result.provider or "unknown"
    print(f"Text latency benchmark route={route} provider={provider} model={model}")
    for sample in result.samples:
        print(
            f"- prompt={sample.prompt_index} iteration={sample.iteration} "
            f"provider={sample.provider or 'unknown'} model={sample.model or 'unknown'} "
            f"first_delta={sample.first_delta_ms:.1f}ms "
            f"total={sample.total_ms:.1f}ms prompt_chars={sample.prompt_chars} "
            f"reply_chars={sample.reply_chars} deltas={len(sample.deltas)}"
        )
    summary = result.summary
    print(
        "Summary: "
        f"first_delta_count={summary.count}, avg={summary.average_ms:.1f}ms, "
        f"median={summary.median_ms:.1f}ms, min={summary.min_ms:.1f}ms, "
        f"max={summary.max_ms:.1f}ms, stdev={summary.stdev_ms:.1f}ms"
    )


def _print_progress(message: str) -> None:
    print(message, flush=True)


def _model_client_metadata(client: ModelClient | None) -> tuple[str | None, str | None]:
    if client is None:
        return None, None
    model = getattr(client, "default_model", None)
    if not isinstance(model, str) or not model:
        model = getattr(client, "model", None)
    provider = getattr(client, "provider", None)
    return (
        model if isinstance(model, str) and model else None,
        provider if isinstance(provider, str) and provider else None,
    )


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
