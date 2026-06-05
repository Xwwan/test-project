"""Render elder chat benchmark JSON reports into readable dialogue markdown."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


SHANGHAI_TZ = timezone(timedelta(hours=8))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render an elder_chat_benchmark JSON report into a markdown dialogue digest.",
    )
    parser.add_argument("report", type=Path, help="Path to elder-chat-benchmark-*.json")
    parser.add_argument(
        "--output",
        type=Path,
        help="Markdown output path. Defaults to docs/elder-chat-benchmark-dialogues-<timestamp>.md",
    )
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    output_path = args.output or _default_output_path(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown(report, args.report, output_path), encoding="utf-8")
    print(output_path)
    return 0


def render_markdown(report: dict[str, Any], report_path: Path, output_path: Path) -> str:
    metadata = report.get("metadata", {})
    summary = report.get("summary", {})
    cases = report.get("cases", [])
    created_utc = _parse_created_at(metadata.get("created_at"))
    created_shanghai = created_utc.astimezone(SHANGHAI_TZ) if created_utc else None
    case_count = summary.get("case_count") or len(cases)
    score_title = "，含首尾衔接评测" if summary.get("tail_head_transition_score") is not None else ""

    lines: list[str] = [
        f"# Elder Chat Benchmark 测试对话整理（{case_count} 例{score_title}）",
        "",
        "## 数据位置",
        "",
        "- benchmark 测试集定义：`scripts/elder_chat_benchmark.py` 的 `DEFAULT_CASES`。",
        f"- 本次真实 benchmark JSON：`{report_path.as_posix()}`。",
        "- benchmark 历史报告目录：`data/benchmark-results/`。",
        f"- 本整理文件：`{output_path.as_posix()}`。",
        "",
        "## 时间说明",
        "",
        f"- 报告生成时间 UTC：`{_format_datetime(created_utc)}`",
        f"- 报告生成时间 Asia/Shanghai：`{_format_datetime(created_shanghai)}`",
        "- 原始 JSON 不保存每个 case 的绝对开始时间；每个 case 下方标注的是从该 case 开始计时的相对时间 `t+...ms`。",
        "- `Agent A 首 token` 是用户能看到第一段回复的时间；`Agent A 完成` 是第一段完整完成时间。",
        "- `B1` 是记忆检索；`B2` 是二次回复决策或生成；`播放结束` 是按 benchmark 配置模拟播完 Agent A 的时间。",
        "- `B2 相对播放结束` 小于等于 `+1000ms` 视为延迟达标；负数表示 B2 在 Agent A 播放结束前已经准备好。",
        "",
        "## 总结",
        "",
        f"- Agent 模型：`{metadata.get('agent_model', 'n/a')}`",
        f"- Judge 模型：`{metadata.get('judge_model', 'n/a')}`",
        f"- Base URL：`{metadata.get('deepseek_base_url', 'n/a')}`",
        f"- Case 数：`{case_count}`",
        f"- Judge 通过率：`{summary.get('judge_pass_count', 'n/a')}/{summary.get('judged_count', 'n/a')} = {_format_rate(summary.get('judge_pass_rate'))}`，阈值 `{_format_rate(summary.get('judge_pass_rate_threshold'))}`",
        f"- Judge 1-10 均分：`{_format_score(summary.get('judge_average_score'))}`，阈值 `{_format_score(summary.get('judge_average_score_threshold'))}`",
    ]
    if summary.get("tail_head_transition_score") is not None:
        lines.extend(
            [
                f"- 首尾衔接均分：`{_format_score(summary.get('tail_head_transition_score'))}`，阈值 `>{_format_score(summary.get('tail_head_transition_score_threshold'))}`",
                f"- 首尾衔接计分 case 数：`{summary.get('tail_head_transition_count', 'n/a')}`",
            ]
        )
    lines.extend(
        [
            f"- 延迟通过率：`{summary.get('latency_pass_count', 'n/a')}/{summary.get('latency_count', 'n/a')} = {_format_rate(summary.get('latency_pass_rate'))}`，阈值 `{_format_rate(summary.get('latency_pass_rate_threshold'))}`",
            f"- Agent B 可见 follow-up 数：`{summary.get('followup_opener_count', 'n/a')}`",
            f"- Agent B 开头最高重复占比：`{_format_rate(summary.get('followup_opener_max_share'))}`，阈值 `{_format_rate(summary.get('followup_opener_max_share_threshold'))}`",
            f"- judge 通过率阈值是否达标：`{_format_bool(summary.get('judge_pass_threshold_met'))}`",
            f"- judge 均分阈值是否达标：`{_format_bool(summary.get('judge_average_score_threshold_met'))}`",
            f"- latency 阈值是否达标：`{_format_bool(summary.get('latency_threshold_met'))}`",
            f"- opener 阈值是否达标：`{_format_bool(summary.get('followup_opener_threshold_met'))}`",
        ]
    )
    if "tail_head_transition_threshold_met" in summary:
        lines.append(
            f"- 首尾衔接阈值是否达标：`{_format_bool(summary.get('tail_head_transition_threshold_met'))}`"
        )
    lines.extend(
        [
            f"- acceptance_passed：`{_format_bool(summary.get('acceptance_passed'))}`",
            "",
            "### Agent B 开头样式 Top",
            "",
            "| 开头样式 | 次数 | 占比 |",
            "| --- | ---: | ---: |",
        ]
    )
    opener_top = summary.get("followup_opener_top") or []
    if opener_top:
        for item in opener_top:
            lines.append(
                f"| `{item.get('opener', '')}` | {item.get('count', 'n/a')} | {_format_rate(item.get('share'))} |"
            )
    else:
        lines.append("| n/a | n/a | n/a |")

    for index, case_result in enumerate(cases, start=1):
        _append_case(lines, index, case_result)

    return "\n".join(lines).rstrip() + "\n"


def _append_case(lines: list[str], index: int, case_result: dict[str, Any]) -> None:
    case = case_result.get("case", {})
    agent_a = case_result.get("agent_a", {})
    agent_b = case_result.get("agent_b", {})
    latency = case_result.get("latency", {})
    judge = case_result.get("judge", {})
    transition = case_result.get("tail_head_transition", {})

    case_id = case.get("case_id", "unknown")
    category = case.get("category", "unknown")
    lines.extend(
        [
            "",
            f"## {index}. {case_id} / {category}",
            "",
            f"- request_id：`{case_result.get('request_id', 'n/a')}`",
            f"- conversation_id：`{case_result.get('conversation_id', 'n/a')}`",
            f"- high_risk：`{_format_bool(case.get('high_risk'))}`",
            f"- 评测期望：{case.get('expectation', '')}",
        ]
    )

    if case.get("initial_history"):
        lines.extend(["", "### 初始历史", ""])
        for turn in case.get("initial_history", []):
            role = turn.get("role", "unknown")
            content = turn.get("content", "")
            created_at = turn.get("created_at")
            suffix = f"（{created_at}）" if created_at else ""
            lines.append(f"- **{role}{suffix}**：{content}")

    lines.extend(
        [
            "",
            "### 时间标注",
            "",
            "| 事件 | 时间 |",
            "| --- | --- |",
            "| 用户输入 | t+0.0 ms |",
            f"| Agent A 首 token | {_format_event_ms(latency.get('first_delta_ms'))} |",
            f"| Agent A 完成 | {_format_event_ms(latency.get('agent_a_done_ms'))} |",
            f"| B1 检索开始 | {_format_event_ms(latency.get('b1_started_ms'))} |",
            f"| B1 检索完成 | {_format_event_ms(latency.get('b1_completed_ms'))} |",
            f"| B2 开始 | {_format_event_ms(latency.get('b2_started_ms'))} |",
            f"| B2 完成 / 二次回复就绪 | {_format_event_ms(latency.get('b2_completed_ms'))} |",
            f"| 模拟 Agent A 播放结束 | {_format_event_ms(latency.get('simulated_playback_end_ms'))} |",
            f"| B2 相对播放结束 | {_format_delta_ms(latency.get('followup_ready_after_playback_ms'))} |",
            f"| 延迟是否达标 | `{_format_bool(latency.get('ready_within_1s_after_playback'))}` |",
            "",
            "### 对话",
            "",
            f"**用户（t+0.0ms）**：{case.get('user_message', '')}",
            "",
            (
                "**Agent A（首 token "
                f"{_format_event_ms(latency.get('first_delta_ms'))}，完成 "
                f"{_format_event_ms(latency.get('agent_a_done_ms'))}）**："
                f"{agent_a.get('reply', '')}"
            ),
            "",
        ]
    )
    if agent_b.get("decision") == "followup":
        lines.append(
            "**Agent B（"
            f"{agent_b.get('followup_type', 'none')}，完成 "
            f"{_format_event_ms(latency.get('b2_completed_ms'))}）**："
            f"{agent_b.get('reply', '')}"
        )
    else:
        lines.append(
            "**Agent B（"
            f"{agent_b.get('decision', 'none')}，完成 "
            f"{_format_event_ms(latency.get('b2_completed_ms'))}）**：未生成用户可见二次回复。"
        )

    if transition:
        lines.extend(
            [
                "",
                "### 首尾衔接",
                "",
                f"- Agent A 尾句：{transition.get('tail', '') or 'n/a'}",
                f"- Agent B 首句：{transition.get('head', '') or 'n/a'}",
                f"- score：`{_format_score(transition.get('score'))}`，threshold：`{_format_score(transition.get('threshold'))}`，threshold_met：`{_format_bool(transition.get('threshold_met'))}`",
                f"- failure_reasons：{_format_reasons(transition.get('failure_reasons'))}",
            ]
        )

    retrieved_items = agent_b.get("retrieved_items") or []
    if retrieved_items:
        lines.extend(["", "### 检索记忆", ""])
        for item in retrieved_items:
            lines.append(
                f"- `#{item.get('id', 'n/a')}` {item.get('summary', '')}"
                f"（type: `{item.get('memory_type', 'n/a')}`）"
            )

    lines.extend(
        [
            "",
            "### Judge",
            "",
            f"- pass：`{_format_bool(judge.get('pass'))}`",
            f"- average_score：`{_format_score(judge.get('average_score'))}`",
            f"- failure_reasons：{_format_reasons(judge.get('failure_reasons'))}",
            f"- overall_comment：{judge.get('overall_comment') or 'n/a'}",
        ]
    )
    scores = judge.get("scores") or {}
    if scores:
        rendered_scores = ", ".join(f"{key}={value}" for key, value in scores.items())
        lines.append(f"- scores：{rendered_scores}")


def _default_output_path(report: dict[str, Any]) -> Path:
    created_utc = _parse_created_at(report.get("metadata", {}).get("created_at"))
    if created_utc is None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    else:
        timestamp = created_utc.astimezone(SHANGHAI_TZ).strftime("%Y%m%d-%H%M%S")
    return Path("docs") / f"elder-chat-benchmark-dialogues-{timestamp}.md"


def _parse_created_at(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    suffix = "UTC" if value.utcoffset() == timedelta(0) else "UTC+08:00"
    return f"{value.strftime('%Y-%m-%d %H:%M:%S')} {suffix}"


def _format_rate(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{value * 100:.1f}%"


def _format_score(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{value:.2f}"


def _format_bool(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "n/a"


def _format_event_ms(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"t+{value:,.1f} ms ({value / 1000:.2f}s)"


def _format_delta_ms(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    sign = "+" if value >= 0 else "-"
    absolute = abs(value)
    return f"{sign}{absolute:,.1f} ms ({sign}{absolute / 1000:.2f}s)"


def _format_reasons(value: Any) -> str:
    if not value:
        return "无"
    if not isinstance(value, list):
        return str(value)
    return "；".join(str(item) for item in value if str(item).strip()) or "无"


if __name__ == "__main__":
    raise SystemExit(main())
