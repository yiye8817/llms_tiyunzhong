from datetime import datetime, timezone
import unittest

from fusion_agent.intent import (
    CHAT,
    KNOWLEDGE_QUERY,
    REALTIME_QUERY,
    TASK_ACTION,
    IntentDecision,
    assess_web_search,
    classify_intent,
)


NOW = datetime(2026, 9, 10, 12, 30, tzinfo=timezone.utc)


class IntentRouterTests(unittest.TestCase):
    def test_social_chat_goes_directly_to_model(self):
        route = classify_intent("你好！")
        self.assertEqual(route.kind, CHAT)
        self.assertTrue(route.direct_to_model)
        self.assertFalse(route.action_loop_requested)
        self.assertFalse(route.side_effect_gate_open)
        self.assertIn("standalone_social_message", route.reasons)

    def test_stable_questions_are_knowledge_queries_in_both_languages(self):
        for prompt in ("请解释 Python 的 GIL 有什么作用？", "How does TLS certificate validation work?"):
            with self.subTest(prompt=prompt):
                route = classify_intent(prompt)
                self.assertEqual(route.kind, KNOWLEDGE_QUERY)
                self.assertTrue(route.direct_to_model)
                self.assertFalse(route.realtime_required)
                self.assertFalse(route.side_effect_gate_open)

    def test_realtime_query_requires_volatile_and_temporal_evidence(self):
        route = classify_intent("今天北京天气和空气质量如何？")
        self.assertEqual(route.kind, REALTIME_QUERY)
        self.assertTrue(route.direct_to_model)
        self.assertTrue(route.realtime_required)
        self.assertFalse(route.explicit_search_requested)
        self.assertTrue(any(item.startswith("temporal:") for item in route.evidence))
        self.assertTrue(any(item.startswith("volatile_topic:") for item in route.evidence))

        route = classify_intent("What is the latest stable Python version?")
        self.assertEqual(route.kind, REALTIME_QUERY)
        self.assertTrue(route.realtime_required)

    def test_terse_volatile_topics_enter_realtime_feedback_gate(self):
        for prompt in ("AI新闻", "北京天气", "BTC价格"):
            with self.subTest(prompt=prompt):
                route = classify_intent(prompt)
                self.assertEqual(route.kind, REALTIME_QUERY)
                self.assertTrue(route.direct_to_model)
                self.assertTrue(route.realtime_required)

    def test_stable_context_suppresses_terse_volatile_inference(self):
        for prompt in ("新闻传播原理", "股票是什么", "天气为什么会变化", "History of stock markets",
                       "整理给定新闻摘录的显示格式"):
            with self.subTest(prompt=prompt):
                route = classify_intent(prompt)
                self.assertNotEqual(route.kind, REALTIME_QUERY)
                self.assertFalse(route.realtime_required)

    def test_now_in_social_chat_does_not_trigger_search(self):
        for prompt in ("你现在感觉怎么样？", "你今天怎么样？", "我今天心情不好",
                       "写一首关于今天的诗", "Today write me a poem"):
            with self.subTest(prompt=prompt):
                route = classify_intent(prompt)
                self.assertNotEqual(route.kind, REALTIME_QUERY)
                self.assertFalse(route.realtime_required)

    def test_explicit_search_is_read_only_information_route(self):
        for prompt in ("请联网搜索 Qwen 最新版本", "请多路并行搜索今天的 AI 新闻",
                       "搜索今天的 AI 新闻", "Search for today's Linux news",
                       "Search the web for today's Linux news", "Please parallel search today's AI news"):
            with self.subTest(prompt=prompt):
                route = classify_intent(prompt)
                self.assertEqual(route.kind, REALTIME_QUERY)
                self.assertTrue(route.explicit_search_requested)
                self.assertFalse(route.action_loop_requested)
                self.assertFalse(route.side_effect_gate_open)

    def test_explicit_environment_action_opens_only_requested_gate(self):
        route = classify_intent("请把结果写入文件 /tmp/report.md")
        self.assertEqual(route.kind, TASK_ACTION)
        self.assertFalse(route.direct_to_model)
        self.assertTrue(route.action_loop_requested)
        self.assertTrue(route.side_effect_gate_open)
        self.assertIn("runtime_permissions_still_required", route.reasons)

        read_route = classify_intent("打开浏览器窗口")
        self.assertEqual(read_route.kind, TASK_ACTION)
        self.assertTrue(read_route.action_loop_requested)
        self.assertTrue(read_route.side_effect_gate_open)

        close_route = classify_intent("Close the browser window")
        self.assertEqual(close_route.kind, TASK_ACTION)
        self.assertTrue(close_route.side_effect_gate_open)

    def test_local_read_view_and_list_are_non_mutating_actions(self):
        for prompt in ("读取文件 ./report.md", "查看本地目录 /tmp", "列出文件夹 ./output",
                       "Read the local file ./report.md", "List directory /tmp"):
            with self.subTest(prompt=prompt):
                route = classify_intent(prompt)
                self.assertEqual(route.kind, TASK_ACTION)
                self.assertTrue(route.action_loop_requested)
                self.assertFalse(route.side_effect_gate_open)

    def test_search_combined_with_file_write_keeps_both_routes(self):
        for prompt in ("联网搜索今天新闻并写入文件 report.md",
                       "搜索今天 AI 新闻并保存为 ai_news.md"):
            with self.subTest(prompt=prompt):
                route = classify_intent(prompt)
                self.assertEqual(route.kind, TASK_ACTION)
                self.assertTrue(route.action_loop_requested)
                self.assertTrue(route.realtime_required)
                self.assertTrue(route.explicit_search_requested)
                self.assertTrue(route.side_effect_gate_open)
                self.assertTrue(any(item.startswith("explicit_search:") for item in route.evidence))
                self.assertTrue(any(item.startswith("coordinated_action:") for item in route.evidence))

    def test_bare_safe_filename_is_an_environment_target(self):
        route = classify_intent("帮我保存到 ai_news.md")
        self.assertEqual(route.kind, TASK_ACTION)
        self.assertTrue(route.action_loop_requested)
        self.assertTrue(route.side_effect_gate_open)

    def test_file_targets_do_not_leak_embedded_words_into_other_capabilities(self):
        route = classify_intent("读取 input.txt 并保存到 output.md")
        self.assertEqual(route.kind, TASK_ACTION)
        self.assertEqual(route.requested_capabilities, ("files",))
        self.assertEqual(route.mutating_capabilities, ("files",))
        self.assertEqual(route.requested_targets, ("file:input.txt", "file:output.md"))
        self.assertNotIn("browser", route.requested_capabilities)
        self.assertNotIn("desktop", route.requested_capabilities)

        for filename in ("browser.txt", "window.json", "app.py"):
            with self.subTest(filename=filename):
                route = classify_intent("读取 " + filename)
                self.assertEqual(route.kind, TASK_ACTION)
                self.assertEqual(route.requested_capabilities, ("files",))
                self.assertEqual(route.mutating_capabilities, ())
                self.assertEqual(route.requested_targets, ("file:" + filename,))
                self.assertNotIn("browser", route.requested_capabilities)
                self.assertNotIn("desktop", route.requested_capabilities)

    def test_filename_verb_is_masked_before_file_mutation_detection(self):
        route = classify_intent("读取 save.md")
        self.assertEqual(route.kind, TASK_ACTION)
        self.assertEqual(route.requested_capabilities, ("files",))
        self.assertEqual(route.mutating_capabilities, ())
        self.assertEqual(route.requested_targets, ("file:save.md",))
        self.assertFalse(route.side_effect_gate_open)

    def test_url_click_and_file_save_open_only_their_explicit_scope(self):
        browser = classify_intent("请在 https://example.com 点击提交按钮")
        self.assertEqual(browser.kind, TASK_ACTION)
        self.assertEqual(browser.requested_capabilities, ("browser",))
        self.assertEqual(browser.mutating_capabilities, ("browser",))
        self.assertEqual(browser.requested_targets, ("url:https://example.com",))
        self.assertTrue(browser.side_effect_gate_open)

        files = classify_intent("请把 example.txt 保存到 out.md")
        self.assertEqual(files.kind, TASK_ACTION)
        self.assertEqual(files.requested_capabilities, ("files",))
        self.assertEqual(files.mutating_capabilities, ("files",))
        self.assertEqual(files.requested_targets, ("file:example.txt", "file:out.md"))
        self.assertNotIn("browser", files.requested_capabilities)
        self.assertNotIn("desktop", files.requested_capabilities)
        self.assertTrue(files.side_effect_gate_open)

    def test_locative_environment_imperatives_do_not_require_a_polite_prefix(self):
        for prompt, capability in (("在桌面点击确定按钮", "desktop"),
                                   ("在屏幕输入框输入 hello", "desktop"),
                                   ("在网页点击提交按钮", "browser")):
            with self.subTest(prompt=prompt):
                route = classify_intent(prompt)
                self.assertEqual(route.kind, TASK_ACTION)
                self.assertEqual(route.requested_capabilities, (capability,))
                self.assertEqual(route.mutating_capabilities, (capability,))

    def test_browser_software_words_do_not_authorize_desktop_actions(self):
        for prompt in (
            "请在浏览器页面点击软件的下载按钮",
            "请在浏览器中打开应用官网",
            "请打开浏览器访问这个程序的网站",
            "Please click the app download button in the browser",
        ):
            with self.subTest(prompt=prompt):
                route = classify_intent(prompt)
                self.assertEqual(route.kind, TASK_ACTION)
                self.assertEqual(route.requested_capabilities, ("browser",))
                self.assertEqual(route.mutating_capabilities, ("browser",))

        combined = classify_intent("请先在浏览器点击提交，然后在桌面点击确定")
        self.assertEqual(combined.kind, TASK_ACTION)
        self.assertEqual(combined.requested_capabilities, ("browser", "desktop"))
        self.assertEqual(combined.mutating_capabilities, ("browser", "desktop"))

    def test_search_noun_phrase_remains_stable_knowledge(self):
        route = classify_intent("搜索算法是什么？")
        self.assertEqual(route.kind, KNOWLEDGE_QUERY)
        self.assertFalse(route.explicit_search_requested)

    def test_how_to_and_negation_close_side_effect_gate(self):
        for prompt in ("如何删除 Linux 文件？", "不要删除这个文件，解释它的格式。",
                       "Can I run a shell command safely?"):
            with self.subTest(prompt=prompt):
                route = classify_intent(prompt)
                self.assertNotEqual(route.kind, TASK_ACTION)
                self.assertFalse(route.side_effect_gate_open)

    def test_knowledge_directive_alone_does_not_authorize_environment_mutation(self):
        for prompt in (
            "请介绍移动应用是什么",
            "请解释修改 report.md 会有什么风险",
            "请总结保存 report.md 的步骤",
        ):
            with self.subTest(prompt=prompt):
                route = classify_intent(prompt)
                self.assertFalse(route.action_loop_requested)
                self.assertFalse(route.side_effect_gate_open)
                self.assertEqual(route.mutating_capabilities, ())

    def test_explicit_action_syntax_still_authorizes_requested_mutation(self):
        for prompt in ("帮我保存 report.md", "请先解释，然后修改 report.md"):
            with self.subTest(prompt=prompt):
                route = classify_intent(prompt)
                self.assertEqual(route.kind, TASK_ACTION)
                self.assertTrue(route.action_loop_requested)
                self.assertEqual(route.requested_capabilities, ("files",))
                self.assertEqual(route.mutating_capabilities, ("files",))

    def test_general_generation_is_not_a_computer_action(self):
        for prompt in ("写一首关于秋天的诗", "讲个笑话", "生成一段 Python 代码"):
            with self.subTest(prompt=prompt):
                route = classify_intent(prompt)
                self.assertEqual(route.kind, CHAT)
                self.assertTrue(route.direct_to_model)
                self.assertIn("answer_content_generation", route.reasons)

    def test_continuation_inherits_but_never_expands_prior_gate(self):
        previous = classify_intent("解释量子纠缠")
        route = classify_intent("继续", previous=previous)
        self.assertEqual(route.kind, KNOWLEDGE_QUERY)
        self.assertFalse(route.side_effect_gate_open)
        self.assertIn("explicit_continuation_of_prior_route", route.reasons)

        without_context = classify_intent("continue")
        self.assertEqual(without_context.kind, CHAT)
        self.assertFalse(without_context.side_effect_gate_open)

    def test_evidence_can_be_logged_as_plain_json_data(self):
        data = classify_intent("今天的股票价格是多少？").as_dict()
        self.assertIsInstance(data["evidence"], list)
        self.assertIsInstance(data["reasons"], list)
        self.assertEqual(data["kind"], REALTIME_QUERY)

    def test_persisted_intent_round_trip_is_strict_and_safe(self):
        original = classify_intent("联网搜索今天新闻并写入文件 report.md")
        restored = IntentDecision.from_dict(original.as_dict())
        self.assertEqual(restored, original)

        damaged = original.as_dict()
        damaged["action_loop_requested"] = False
        with self.assertRaises(ValueError):
            IntentDecision.from_dict(damaged)

        damaged = original.as_dict()
        damaged["side_effect_gate_open"] = "yes"
        with self.assertRaises(ValueError):
            IntentDecision.from_dict(damaged)


class SearchEscalationTests(unittest.TestCase):
    def test_stable_answer_never_triggers_automatic_search(self):
        gate = assess_web_search("解释 TCP 三次握手", "TCP uses SYN and ACK.", now=NOW)
        self.assertFalse(gate.should_search)
        self.assertIn("request_is_not_time_sensitive", gate.reasons)

    def test_explicit_search_always_triggers_even_with_plausible_answer(self):
        task = "联网搜索今天的新闻"
        answer = "截至2026年9月10日，见 https://example.com/news"
        gate = assess_web_search(task, answer, now=NOW)
        self.assertTrue(gate.should_search)
        self.assertEqual(gate.confidence, 1.0)

    def test_model_disclosure_of_stale_access_triggers_search(self):
        gate = assess_web_search("今天北京天气？", "我无法访问实时天气，知识可能不是最新。", now=NOW)
        self.assertTrue(gate.should_search)
        self.assertIn("model_disclosed_missing_or_stale_live_access", gate.reasons)

    def test_realtime_answer_without_provenance_triggers_search(self):
        gate = assess_web_search("What is the latest Python version?", "It is Python 3.15.", now=NOW)
        self.assertTrue(gate.should_search)
        self.assertIn("missing_current_retrieval_timestamp", gate.reasons)

    def test_url_alone_is_not_freshness_proof(self):
        gate = assess_web_search("最新 Linux 新闻", "Source: https://example.com/story", now=NOW)
        self.assertTrue(gate.should_search)
        self.assertIn("missing_current_retrieval_timestamp", gate.reasons)

    def test_current_date_and_source_pass_feedback_gate(self):
        answer = "检索时间为 2026-09-10；来源：https://example.com/weather"
        gate = assess_web_search("今天北京天气？", answer, now=NOW)
        self.assertFalse(gate.should_search)
        self.assertIn("model_feedback_contains_current_or_retrieval_provenance", gate.reasons)

    def test_current_date_without_url_is_sufficient_feedback(self):
        gate = assess_web_search("北京天气", "截至2026年9月10日，北京晴，气温 27 度。", now=NOW)
        self.assertFalse(gate.should_search)
        self.assertTrue(any(item.startswith("current_date:") for item in gate.evidence))

    def test_explicit_retrieval_marker_without_url_is_sufficient_feedback(self):
        gate = assess_web_search("BTC价格", "已实时查询：BTC 当前为 120000 美元。", now=NOW)
        self.assertFalse(gate.should_search)
        self.assertTrue(any(item.startswith("retrieval_marker:") for item in gate.evidence))

    def test_empty_realtime_answer_triggers_search(self):
        gate = assess_web_search("最新汇率", "", now=NOW)
        self.assertTrue(gate.should_search)
        self.assertIn("empty_model_feedback", gate.reasons)


if __name__ == "__main__":
    unittest.main()
