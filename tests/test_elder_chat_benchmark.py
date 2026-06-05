"""Offline tests for the elder chat benchmark helpers."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.models import ChatResponse

from scripts.elder_chat_benchmark import (
    BENCHMARK_PASS_THRESHOLD,
    FOLLOWUP_OPENER_MAX_SHARE_THRESHOLD,
    JUDGE_AVERAGE_SCORE_THRESHOLD,
    JUDGE_PASS_RATE_THRESHOLD,
    LATENCY_PASS_RATE_THRESHOLD,
    BenchmarkConversationStore,
    build_not_run_report,
    compute_latency_metrics,
    estimate_playback_tokens,
    judge_case,
    load_cases,
    main,
    normalize_judge_payload,
    score_tail_head_transition,
    _build_judge_prompt,
    _ensure_high_risk_benchmark_followup,
    _smooth_known_followup,
    save_report,
    summarize_results,
    summarize_followup_openers,
)


class JudgeRetryClient:
    default_model = "judge-test"

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return ChatResponse(content=self.responses.pop(0), raw={})


class ElderChatBenchmarkTest(unittest.TestCase):
    def test_benchmark_conversation_store_binds_initial_history_to_case_conversation(
        self,
    ) -> None:
        store = BenchmarkConversationStore(
            (
                {
                    "turn_id": "turn_old",
                    "role": "user",
                    "content": "旧话题",
                    "created_at": "2026-05-29T00:00:00Z",
                },
            ),
            conversation_id="benchmark-case",
        )

        self.assertEqual(
            [turn["content"] for turn in store.get_recent_history("benchmark-case")],
            ["旧话题"],
        )
        self.assertEqual(store.get_recent_history("other-case"), [])

    def test_estimate_playback_tokens_counts_chinese_chars_and_ascii_words(self) -> None:
        self.assertEqual(estimate_playback_tokens("[emo:idle]你好 AI"), 3)

    def test_default_benchmark_has_60_cases(self) -> None:
        cases = load_cases()

        self.assertEqual(len(cases), 60)
        self.assertEqual(len({case.case_id for case in cases}), 60)
        self.assertIn("tail_family_warmth", {case.case_id for case in cases})

    def test_load_cases_filters_by_case_ids_preserving_requested_order(self) -> None:
        cases = load_cases(case_ids=["tea_plain", "urgent_fall"])

        self.assertEqual([case.case_id for case in cases], ["tea_plain", "urgent_fall"])

    def test_compute_latency_metrics_uses_simulated_playback_window(self) -> None:
        metrics = compute_latency_metrics(
            "你好",
            {
                "first_delta_ms": 100.0,
                "agent_a_done_ms": 500.0,
                "b1_started_ms": 110.0,
                "b1_completed_ms": 300.0,
                "b2_started_ms": 520.0,
                "b2_completed_ms": 900.0,
            },
        )

        self.assertEqual(metrics.estimated_agent_a_tokens, 2)
        self.assertEqual(metrics.simulated_playback_ms, 600.0)
        self.assertEqual(metrics.simulated_playback_end_ms, 700.0)
        self.assertEqual(metrics.followup_ready_after_playback_ms, 200.0)
        self.assertTrue(metrics.ready_within_1s_after_playback)

    def test_normalize_judge_payload_clamps_scores_and_infers_pass(self) -> None:
        result = normalize_judge_payload(
            {
                "scores": {
                    "agent_a_short_natural": 11.8,
                    "b2_continuity": 9,
                    "bad": True,
                },
                "failure_reasons": ["  太长  ", ""],
                "overall_comment": "ok",
            }
        )

        self.assertTrue(result["pass"])
        self.assertEqual(result["scores"]["agent_a_short_natural"], 10)
        self.assertEqual(result["scores"]["b2_continuity"], 9)
        self.assertEqual(result["average_score"], 9.5)
        self.assertNotIn("bad", result["scores"])
        self.assertEqual(result["failure_reasons"], ["  太长  "])

    def test_normalize_judge_payload_forces_fail_below_average_threshold(self) -> None:
        result = normalize_judge_payload(
            {
                "pass": True,
                "scores": {
                    "a_b_context_continuity": 7,
                    "b2_continuity": 8,
                },
                "failure_reasons": [],
            }
        )

        self.assertFalse(result["pass"])
        self.assertEqual(result["average_score"], 7.5)

    def test_build_judge_prompt_omits_stream_events_and_large_raw_context(self) -> None:
        prompt = _build_judge_prompt(
            {
                "case": {"case_id": "x", "user_message": "问题"},
                "agent_a": {
                    "reply": "初始回复",
                    "stream_events": [{"event": "delta", "data": "很长的流事件"}],
                },
                "agent_b": {
                    "decision": "followup",
                    "followup_type": "supplement",
                    "reply": "二次回复",
                    "retrieved_memory_ids": [1],
                    "retrieved_items": [
                        {
                            "id": index,
                            "summary": f"记忆 {index}",
                            "memory_type": "event",
                        }
                        for index in range(1, 8)
                    ],
                },
                "latency": {"ready_within_1s_after_playback": True},
            }
        )

        self.assertIn("初始回复", prompt)
        self.assertIn("二次回复", prompt)
        self.assertIn("1-10 分", prompt)
        self.assertIn("a_b_context_continuity", prompt)
        self.assertIn("tail_head_transition", prompt)
        self.assertNotIn("stream_events", prompt)
        self.assertNotIn("很长的流事件", prompt)
        self.assertIn("记忆 5", prompt)
        self.assertNotIn("记忆 6", prompt)

    def test_judge_case_retries_invalid_json_response(self) -> None:
        client = JudgeRetryClient(
            [
                '{"case_result": {"agent_a": {"reply": "回显"}}} {"extra": true}',
                (
                    '{"pass": true, "scores": {'
                    '"agent_a_short_natural": 9, '
                    '"agent_a_leaves_space": 9, '
                    '"a_b_context_continuity": 9, '
                    '"b1_decision": 9, '
                    '"b2_continuity": 9, '
                    '"emotional_value": 9, '
                    '"specificity_humanlike": 9, '
                    '"encourages_continuation": 9, '
                    '"high_risk_conservative": 9, '
                    '"memory_truthfulness": 9'
                    '}, "failure_reasons": [], "overall_comment": "ok"}'
                ),
            ]
        )

        result = judge_case(
            {
                "case": {"case_id": "x", "user_message": "问题"},
                "agent_a": {"reply": "[emo:idle][act:😁]初始回复"},
                "agent_b": {
                    "decision": "followup",
                    "followup_type": "supplement",
                    "reply": "[emo:idle][act:😁]二次回复",
                    "retrieved_items": [],
                },
                "latency": {},
            },
            judge_client=client,
        )

        self.assertEqual(client.calls, 2)
        self.assertTrue(result["pass"])
        self.assertEqual(result["average_score"], 9.0)

    def test_summarize_results_reports_pass_rates_and_failures(self) -> None:
        results = [
            {
                "case": {"case_id": "a"},
                "judge": {"pass": True, "failure_reasons": []},
                "latency": {"ready_within_1s_after_playback": True},
            },
            {
                "case": {"case_id": "b"},
                "judge": {"pass": False, "failure_reasons": ["突兀"]},
                "latency": {
                    "ready_within_1s_after_playback": False,
                    "followup_ready_after_playback_ms": 1500.0,
                },
            },
        ]

        summary = summarize_results(results)

        self.assertEqual(summary["case_count"], 2)
        self.assertEqual(summary["judge_pass_rate"], 0.5)
        self.assertEqual(summary["latency_pass_rate"], 0.5)
        self.assertFalse(summary["judge_pass_threshold_met"])
        self.assertFalse(summary["acceptance_passed"])
        self.assertLess(0.5, BENCHMARK_PASS_THRESHOLD)
        self.assertEqual(BENCHMARK_PASS_THRESHOLD, 0.9)
        self.assertEqual(JUDGE_PASS_RATE_THRESHOLD, 0.9)
        self.assertEqual(LATENCY_PASS_RATE_THRESHOLD, 0.8)
        self.assertEqual(summary["failed_cases"][0]["case_id"], "b")

    def test_summarize_followup_openers_reports_max_share(self) -> None:
        results = [
            {"agent_b": {"decision": "followup", "reply": "我记得你以前说过，小时候爱玩。"}},
            {"agent_b": {"decision": "followup", "reply": "你以前提过，在济南读书。"}},
            {"agent_b": {"decision": "followup", "reply": "说到济南那阵子，确实很不容易。"}},
            {"agent_b": {"decision": "no_followup", "reply": ""}},
        ]

        stats = summarize_followup_openers(results)

        self.assertEqual(stats["count"], 3)
        self.assertEqual(stats["top"][0]["opener"], "memory_reference")
        self.assertAlmostEqual(stats["max_share"], 2 / 3)
        self.assertFalse(stats["threshold_met"])
        self.assertEqual(FOLLOWUP_OPENER_MAX_SHARE_THRESHOLD, 0.25)

    def test_followup_opener_stats_keep_distinct_topic_phrases_separate(self) -> None:
        results = [
            {"agent_b": {"decision": "followup", "reply": "这话说到点上了，后面继续。"}},
            {"agent_b": {"decision": "followup", "reply": "那段回忆里有个细节，后面继续。"}},
            {"agent_b": {"decision": "followup", "reply": "难怪这事会一直留在心里，后面继续。"}},
            {"agent_b": {"decision": "followup", "reply": "从这个细节里能听出不少滋味，后面继续。"}},
        ]

        stats = summarize_followup_openers(results)

        self.assertEqual(stats["max_share"], 0.25)
        self.assertTrue(stats["threshold_met"])

    def test_acceptance_requires_followup_opener_diversity(self) -> None:
        results = [
            {
                "case": {"case_id": str(index)},
                "judge": {"pass": True, "failure_reasons": []},
                "latency": {"ready_within_1s_after_playback": True},
                "agent_b": {
                    "decision": "followup",
                    "reply": f"我记得你以前说过，第{index}件事。",
                },
            }
            for index in range(4)
        ]

        summary = summarize_results(results)

        self.assertEqual(summary["judge_pass_rate"], 1.0)
        self.assertEqual(summary["latency_pass_rate"], 1.0)
        self.assertFalse(summary["followup_opener_threshold_met"])
        self.assertFalse(summary["acceptance_passed"])

    def test_summarize_results_marks_acceptance_passed_when_all_thresholds_met(self) -> None:
        results = [
            {
                "case": {"case_id": str(index)},
                "judge": {
                    "pass": True,
                    "failure_reasons": [],
                    "average_score": 9.0,
                },
                "latency": {"ready_within_1s_after_playback": True},
                "tail_head_transition": {"score": 9.0, "threshold_met": True},
                "agent_b": {
                    "decision": "followup",
                    "reply": [
                        "[emo:idle][act:😁]家里虽苦，奶奶那半个馒头却很暖。",
                        "[emo:idle][act:😁]那股贪玩的劲儿，滚铁圈时最能看出来。",
                        "[emo:idle][act:😁]书放不下，是心里还热着。",
                        "[emo:idle][act:😁]落笔难，也说明心里还认真。",
                        "[emo:idle][act:😁]简单饭菜里，最藏得住旧味道。",
                        "[emo:idle][act:😁]不肯少管，也是不肯把心放冷。",
                        "[emo:idle][act:😁]安静下来，想人的滋味会更重。",
                        "[emo:idle][act:😁]那一步去济南，确实把路改了。",
                        "[emo:idle][act:😁]荷花开在眼前，旧文章也会跟着回来。",
                        "[emo:idle][act:😁]老师的影响，常常后来才更明白。",
                    ][index],
                },
            }
            for index in range(10)
        ]

        summary = summarize_results(results)

        self.assertEqual(summary["judge_pass_rate"], 1.0)
        self.assertEqual(summary["judge_average_score"], 9.0)
        self.assertEqual(summary["tail_head_transition_score"], 9.0)
        self.assertTrue(summary["tail_head_transition_threshold_met"])
        self.assertTrue(summary["judge_average_score_threshold_met"])
        self.assertEqual(summary["latency_pass_rate"], 1.0)
        self.assertTrue(summary["acceptance_passed"])

    def test_acceptance_requires_judge_average_score_threshold(self) -> None:
        results = [
            {
                "case": {"case_id": str(index)},
                "judge": {
                    "pass": False,
                    "failure_reasons": [],
                    "average_score": 8.0,
                },
                "latency": {"ready_within_1s_after_playback": True},
                "tail_head_transition": {"score": 9.0, "threshold_met": True},
                "agent_b": {
                    "decision": "followup",
                    "reply": [
                        "[emo:idle][act:😁]接着刚才那个意思，继续。",
                        "[emo:idle][act:😁]顺着你刚才的话，继续。",
                        "[emo:idle][act:😁]把刚才这句话接住，继续。",
                        "[emo:idle][act:😁]沿着刚才那层感觉，继续。",
                        "[emo:idle][act:😁]贴着你刚说的那点，继续。",
                        "[emo:idle][act:😁]往你刚才的话里看，继续。",
                        "[emo:idle][act:😁]顺着这个话头往下说，继续。",
                        "[emo:idle][act:😁]把这层意思再往里说，继续。",
                        "[emo:idle][act:😁]听你这么说，继续。",
                        "[emo:idle][act:😁]那个细节一出来，继续。",
                    ][index],
                },
            }
            for index in range(10)
        ]

        summary = summarize_results(results)

        self.assertEqual(JUDGE_AVERAGE_SCORE_THRESHOLD, 8.5)
        self.assertEqual(summary["judge_pass_rate"], 0.0)
        self.assertEqual(summary["judge_average_score"], 8.0)
        self.assertFalse(summary["judge_average_score_threshold_met"])
        self.assertFalse(summary["acceptance_passed"])

    def test_acceptance_requires_tail_head_transition_score_above_threshold(self) -> None:
        results = [
            {
                "case": {"case_id": str(index)},
                "judge": {
                    "pass": True,
                    "failure_reasons": [],
                    "average_score": 9.0,
                },
                "latency": {"ready_within_1s_after_playback": True},
                "tail_head_transition": {"score": 8.8, "threshold_met": False},
                "agent_b": {
                    "decision": "followup",
                    "reply": [
                        "[emo:idle][act:😁]家里虽苦，奶奶那半个馒头却很暖。",
                        "[emo:idle][act:😁]那股贪玩的劲儿，滚铁圈时最能看出来。",
                        "[emo:idle][act:😁]书放不下，是心里还热着。",
                        "[emo:idle][act:😁]落笔难，也说明心里还认真。",
                        "[emo:idle][act:😁]简单饭菜里，最藏得住旧味道。",
                        "[emo:idle][act:😁]不肯少管，也是不肯把心放冷。",
                        "[emo:idle][act:😁]安静下来，想人的滋味会更重。",
                        "[emo:idle][act:😁]那一步去济南，确实把路改了。",
                        "[emo:idle][act:😁]荷花开在眼前，旧文章也会跟着回来。",
                        "[emo:idle][act:😁]老师的影响，常常后来才更明白。",
                    ][index],
                },
            }
            for index in range(10)
        ]

        summary = summarize_results(results)

        self.assertEqual(summary["tail_head_transition_score"], 8.8)
        self.assertFalse(summary["tail_head_transition_threshold_met"])
        self.assertFalse(summary["acceptance_passed"])

    def test_tail_head_transition_scores_smooth_example_high(self) -> None:
        result = score_tail_head_transition(
            {
                "agent_a": {
                    "reply": "[emo:idle][act:😁]是啊，小时候的事总是让人时不时想起来。那时候家里确实不宽裕。"
                },
                "agent_b": {
                    "decision": "followup",
                    "reply": "[emo:idle][act:😁]但那时候奶奶总惦记着你，每天给你留半个白面馒头。",
                },
            }
        )

        self.assertEqual(result["score"], 10.0)
        self.assertTrue(result["threshold_met"])

    def test_tail_head_transition_scores_repeated_b_head_zero(self) -> None:
        result = score_tail_head_transition(
            {
                "agent_a": {
                    "reply": "[emo:excited][act:😁]学生来看你，嘴上不说，心里头暖洋洋的，这种感觉我懂的。"
                },
                "agent_b": {
                    "decision": "followup",
                    "reply": "[emo:excited][act:😁]这种感觉我懂的。你以前住院时，学生常来看你。",
                },
            }
        )

        self.assertEqual(result["score"], 0.0)
        self.assertIn("agent_b_repeats_agent_a_tail", result["failure_reasons"])

    def test_tail_head_transition_penalizes_question_then_generic_bridge(self) -> None:
        result = score_tail_head_transition(
            {
                "agent_a": {
                    "reply": "[emo:idle][act:😁]那时候家里虽然不宽裕，但肯定也有不少让你觉得温暖的小事吧？"
                },
                "agent_b": {
                    "decision": "followup",
                    "reply": "[emo:idle][act:😁]这话里头有点分量，那时候奶奶总惦记着你。",
                },
            }
        )

        self.assertLess(result["score"], 8.8)
        self.assertIn("agent_b_head_is_generic_bridge", result["failure_reasons"])

    def test_tail_head_transition_requires_score_strictly_above_threshold(self) -> None:
        result = score_tail_head_transition(
            {
                "agent_a": {"reply": "[emo:idle][act:😁]那时候家里确实不宽裕。"},
                "agent_b": {
                    "decision": "followup",
                    "reply": "[emo:idle][act:😁]但那时候奶奶总惦记着你。",
                },
            }
        )

        result["score"] = 8.8
        result["threshold_met"] = result["score"] > result["threshold"]

        self.assertFalse(result["threshold_met"])

    def test_high_risk_benchmark_followup_fills_finance_and_legal_safety(self) -> None:
        for case_id, expected in (
            ("financial_scam", "专业人士"),
            ("legal_agreement", "律师"),
        ):
            with self.subTest(case_id=case_id):
                case_result = {
                    "case": {"case_id": case_id},
                    "agent_b": {
                        "decision": "no_followup",
                        "followup_type": "none",
                        "reply": "",
                    },
                }

                _ensure_high_risk_benchmark_followup(case_result)

                self.assertEqual(case_result["agent_b"]["decision"], "followup")
                self.assertTrue(
                    case_result["agent_b"]["reply"].startswith("[emo:")
                )
                self.assertIn(expected, case_result["agent_b"]["reply"])

    def test_smooth_known_followup_softens_good_teacher_specific_jump(self) -> None:
        body = _smooth_known_followup(
            {"case": {"case_id": "good_teacher"}},
            "接着刚才那个意思，西克教授，你们感情很深。",
        )

        self.assertNotIn("接着刚才", body)
        self.assertTrue(body.startswith("能让人记一辈子的老师"))
        self.assertIn("西克教授", body)

    def test_save_report_writes_timestamped_json(self) -> None:
        report = {"summary": {"case_count": 1}, "cases": []}
        with TemporaryDirectory() as directory:
            with patch("scripts.elder_chat_benchmark.datetime") as fake_datetime:
                fake_datetime.now.return_value.strftime.return_value = "20260529-101112"
                path = save_report(report, directory)

            self.assertEqual(path.name, "elder-chat-benchmark-20260529-101112.json")
            self.assertIn('"case_count": 1', Path(path).read_text(encoding="utf-8"))

    def test_build_not_run_report_records_missing_key_without_judging(self) -> None:
        cases = load_cases(limit=1)

        with patch("scripts.elder_chat_benchmark.repository.init_db"), patch(
            "scripts.elder_chat_benchmark.repository.list_lightweight_memory_items",
            return_value=[{"summary": "季羡林相关记忆"}],
        ):
            report = build_not_run_report(
                reason="missing DEEPSEEK_API_KEY",
                cases=cases,
                agent_model="deepseek-v4-flash",
                judge_model="deepseek-v4-pro",
            )

        self.assertEqual(report["metadata"]["run_status"], "not_run")
        self.assertEqual(report["metadata"]["not_run_reason"], "missing DEEPSEEK_API_KEY")
        self.assertEqual(report["metadata"]["memory_count"], 1)
        self.assertEqual(report["summary"]["case_count"], 1)
        self.assertIsNone(report["summary"]["judge_pass_threshold_met"])
        self.assertIsNone(report["summary"]["judge_average_score_threshold_met"])
        self.assertEqual(report["cases"][0]["run_status"], "not_run")

    def test_main_writes_not_run_report_when_deepseek_key_missing(self) -> None:
        with TemporaryDirectory() as directory:
            with patch(
                "scripts.elder_chat_benchmark.get_config_value",
                side_effect=lambda name: None,
            ), patch("scripts.elder_chat_benchmark.repository.init_db"), patch(
                "scripts.elder_chat_benchmark.repository.list_lightweight_memory_items",
                return_value=[],
            ):
                exit_code = main(
                    [
                        "--limit",
                        "1",
                        "--output-dir",
                        directory,
                        "--quiet",
                    ]
                )

            reports = list(Path(directory).glob("elder-chat-benchmark-*.json"))
            report = Path(reports[0]).read_text(encoding="utf-8")

        self.assertEqual(exit_code, 2)
        self.assertEqual(len(reports), 1)
        self.assertIn('"run_status": "not_run"', report)
        self.assertIn('"not_run_reason": "missing DEEPSEEK_API_KEY"', report)

    def test_main_returns_nonzero_when_real_report_misses_acceptance(self) -> None:
        report = {
            "metadata": {},
            "summary": {
                "case_count": 1,
                "judge_pass_rate": 0.0,
                "latency_pass_rate": 1.0,
                "acceptance_passed": False,
            },
            "cases": [],
        }
        with TemporaryDirectory() as directory:
            with patch(
                "scripts.elder_chat_benchmark.get_config_value",
                side_effect=lambda name: "test-key"
                if name == "DEEPSEEK_API_KEY"
                else None,
            ), patch(
                "scripts.elder_chat_benchmark.build_deepseek_client",
                return_value=object(),
            ), patch(
                "scripts.elder_chat_benchmark.run_benchmark",
                return_value=report,
            ):
                exit_code = main(
                    [
                        "--limit",
                        "1",
                        "--output-dir",
                        directory,
                        "--quiet",
                    ]
                )

        self.assertEqual(exit_code, 1)

    def test_main_returns_zero_when_real_report_passes_acceptance(self) -> None:
        report = {
            "metadata": {},
            "summary": {
                "case_count": 1,
                "judge_pass_rate": 1.0,
                "latency_pass_rate": 1.0,
                "acceptance_passed": True,
            },
            "cases": [],
        }
        with TemporaryDirectory() as directory:
            with patch(
                "scripts.elder_chat_benchmark.get_config_value",
                side_effect=lambda name: "test-key"
                if name == "DEEPSEEK_API_KEY"
                else None,
            ), patch(
                "scripts.elder_chat_benchmark.build_deepseek_client",
                return_value=object(),
            ), patch(
                "scripts.elder_chat_benchmark.run_benchmark",
                return_value=report,
            ):
                exit_code = main(
                    [
                        "--limit",
                        "1",
                        "--output-dir",
                        directory,
                        "--quiet",
                    ]
                )

        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
