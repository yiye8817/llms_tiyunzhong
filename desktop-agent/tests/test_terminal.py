import io
import unittest

from fusion_agent.terminal import TerminalProgress, authorization_details


class TerminalProgressTests(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        self.progress = TerminalProgress(self.stream, secrets=("fixture-key",))

    def test_human_step_flow_omits_payloads_and_registry_duplicates(self):
        events = [
            ("agent.started", {"model": "qwen"}),
            ("run.started", {"max_steps": 20, "payload": {"task": "private complete task"}}),
            ("model.request", {"payload": {"messages": ["private messages"]}}),
            ("model.http_request", {"payload": {"request": "private HTTP request"}}),
            ("model.response", {"payload": {"content": "private model JSON"}}),
            ("model.raw_reply", {"payload": {"reply": "private model JSON"}}),
            ("tool.attempt", {"action_id": "a1", "step": 1, "tool": "browser.open", "payload": {"summary": "打开任务网页"}}),
            ("tool.started", {"tool": "browser.open"}),
            ("tool.completed", {"tool": "browser.open", "payload": {"stdout": "private long stdout"}}),
            ("tool.result", {"action_id": "a1", "ok": True, "payload": {"result": {"ok": True, "stdout": "private long stdout", "verification": {"status": "pending"}}}}),
        ]
        for name, fields in events:
            self.progress(name, fields)
        # Re-delivery must not print a duplicate action or result.
        self.progress(*events[6])
        self.progress(*events[-1])
        text = self.stream.getvalue()
        self.assertEqual(len(text.splitlines()), 4)
        self.assertIn("任务开始 · qwen", text)
        self.assertIn("等待 qwen 响应", text)
        self.assertEqual(text.count("第 1 步"), 1)
        self.assertIn("操作已触发，等待核验", text)
        self.assertNotIn("成功", text)
        self.assertNotIn("private", text)

    def test_failed_result_distinguishes_not_started_and_unknown(self):
        for index, result in enumerate([
            {"ok": False, "execution": {"status": "not_started"}, "error": {"message": "缺少浏览器依赖"}},
            {"ok": False, "outcome_unknown": True, "error": {"code": "timed_out"}},
            {"ok": False, "error": {"message": "退出代码 7"}},
        ]):
            self.progress("tool.result", {"action_id": str(index), "ok": False, "payload": {"result": result}})
        text = self.stream.getvalue()
        self.assertIn("未执行：缺少浏览器依赖", text)
        self.assertIn("执行结果不确定，需核验：timed_out", text)
        self.assertIn("失败：退出代码 7", text)
        self.assertNotIn("成功", text)

    def test_success_after_verified_result_and_one_final_status(self):
        self.progress("tool.result", {"ok": True, "payload": {"result": {"verification": {"status": "verified"}}}})
        self.progress("run.completed", {"payload": {"answer": "raw final body"}})
        for _ in range(2):
            self.progress("agent.finished", {"status": "completed", "steps": 3, "payload": {"result": "raw result"}})
        text = self.stream.getvalue()
        self.assertIn("成功，已核验", text)
        self.assertEqual(text.count("任务完成"), 1)
        self.assertNotIn("raw", text)

    def test_pointer_check_never_claims_pending_application_action_was_verified(self):
        self.progress("verification.insufficient", {})
        self.progress("tool.result", {"ok": True, "payload": {"result": {
            "ok": True, "verification": {"status": "verified", "method": "pointer_position"},
            "pending_verification": {"tool": "desktop.click", "action_id": "previous-click"}}}})
        text = self.stream.getvalue()
        self.assertIn("原操作仍待核验", text)
        self.assertNotIn("成功，已核验", text)

    def test_visible_summaries_are_sanitized_redacted_and_bounded(self):
        self.progress("tool.attempt", {"step": 1, "tool": "shell.run", "payload": {
            "summary": "\x1b[2J\x1b]0;spoof title\x07fixture-key\n" + "非常长的摘要" * 1000}})
        text = self.stream.getvalue()
        self.assertNotIn("\x1b", text)
        self.assertNotIn("spoof title", text)
        self.assertNotIn("fixture-key", text)
        self.assertEqual(len(text.splitlines()), 1)
        self.assertLess(len(text), 240)

    def test_repairs_and_dependency_stages_have_short_progress_without_private_body(self):
        for name in ("model.invalid_protocol", "model.repair_requested", "model.protocol_repaired",
                     "environment.command.started", "environment.command.completed"):
            self.progress(name, {"repair_count": 1, "step": "pip_install", "payload": {"raw_reply": "private broken JSON", "stdout": "private pip output"}})
        text = self.stream.getvalue()
        self.assertIn("第 1 次", text)
        self.assertIn("已修正 JSON", text)
        self.assertIn("安装 Playwright", text)
        self.assertNotIn("private", text)

    def test_closed_terminal_does_not_raise_into_action_runtime(self):
        self.stream.close()
        self.progress("model.request", {})

    def test_web_stages_distinguish_dispatch_waiting_generation_and_collection(self):
        def stage(value, **fields):
            self.progress("model.web_progress", {"request_id": "request-one", "provider": "qwen",
                                                  "stage": value, **fields})

        stage("rate_limited", wait_ms=1250)
        self.assertIn("访问过快，正在限速等待（1.2 秒）", self.stream.getvalue())
        stage("send_dispatched")
        self.assertIn("等待网页接收", self.stream.getvalue())
        self.assertNotIn("网页已接收请求", self.stream.getvalue())
        stage("accepted")
        stage("waiting_response")
        text = self.stream.getvalue()
        self.assertIn("网页已接收请求", text)
        self.assertIn("正在等待或接收网页响应", text)
        self.assertNotIn("正在生成回答", text)
        self.assertNotIn("服务器已返回", text)
        stage("server_responded", http_status=200)
        stage("generating")
        stage("provider_completed")
        stage("completed")
        lines = self.stream.getvalue().splitlines()
        self.assertTrue(any("生成服务器已返回（HTTP 200）" in line for line in lines))
        self.assertTrue(any("正在生成回答" in line for line in lines))
        self.assertTrue(any("网页回答已采集" in line for line in lines))
        self.assertTrue(any("响应已返回，正在解析" in line for line in lines))
        self.assertNotIn("任务完成", self.stream.getvalue())

    def test_interleaved_provider_repeats_are_suppressed_but_new_requests_still_show(self):
        for provider in ("qwen", "deepseek", "qwen", "deepseek"):
            self.progress("model.web_progress", {"request_id": "first-request", "provider": provider,
                                                  "stage": "waiting_response"})
        text = self.stream.getvalue()
        self.assertEqual(len(text.splitlines()), 2)
        self.assertEqual(text.count("qwen ·"), 1)
        self.assertEqual(text.count("deepseek ·"), 1)
        self.progress("model.request", {})
        self.progress("model.web_progress", {"request_id": "next-request", "provider": "qwen",
                                              "stage": "waiting_response"})
        self.assertEqual(self.stream.getvalue().count("qwen ·"), 2)

    def test_progress_and_alias_display_omit_raw_json_paths_and_unproven_success(self):
        private = {"content": "private raw reply", "arguments": {"path": "/private/workspace/report.txt"},
                   "logs": {"result": "/private/runtime/result.md"}}
        self.progress("agent.started", {"model": "qwen", "payload": private})
        self.progress("model.raw_reply", {"payload": private})
        self.progress("model.web_progress", {"provider": "qwen", "request_id": "request-one",
                                              "stage": "server_responded", "http_status": 503, "payload": private})
        self.progress("tool.alias_resolved", {"original_tool": "file.read", "tool": "files.read", "payload": private})
        self.progress("tool.alternatives_found", {"payload": private})
        text = self.stream.getvalue()
        self.assertFalse(self.progress.verbose)
        self.assertIn("HTTP 503", text)
        self.assertIn("工具名称已匹配：file.read → files.read", text)
        self.assertIn("正在校验替代方案", text)
        for omitted in ("private", "{", "}", "成功", "任务完成"):
            self.assertNotIn(omitted, text)

    def test_web_search_progress_shows_stages_without_query_or_backend_error(self):
        private = {"query": "PRIVATE QUERY", "error": "PRIVATE BACKEND ERROR"}
        self.progress("web.search_started", {"method": "sequential", "payload": private})
        self.progress("web.search_backend", {"provider": "ddgo", "status": "empty",
                                              "code": "network_private", "payload": private})
        self.progress("web.search_backend", {"provider": "browser_use", "status": "unavailable",
                                              "code": "PRIVATE-CODE", "payload": private})
        self.progress("web.search_backend", {"provider": "opencli", "status": "ok",
                                              "result_count": 3, "payload": private})
        self.progress("web.search_completed", {"result_count": 3, "payload": private})
        lines = self.stream.getvalue().splitlines()
        self.assertEqual(lines, [
            "正在搜索实时网页信息（顺序降级）…",
            "  网页搜索 · DDG · 未找到有效结果，尝试下一路",
            "  网页搜索 · Browser Use · 当前不可用，尝试下一路",
            "  网页搜索 · OpenCLI · 已返回结果",
            "网页搜索完成 · 已整理 3 条结果",
        ])
        self.assertNotIn("PRIVATE", self.stream.getvalue())
        self.assertNotIn("network_private", self.stream.getvalue())

        self.stream.seek(0)
        self.stream.truncate(0)
        self.progress("web.search_started", {"method": "parallel"})
        self.progress("web.search_backend", {"provider": "playwright", "status": "timeout"})
        self.progress("web.search_failed", {"code": "search_failed"})
        text = self.stream.getvalue()
        self.assertIn("多路并行", text)
        self.assertIn("Playwright · 响应超时", text)
        self.assertNotIn("尝试下一路", text)
        self.assertIn("未获得可用结果", text)


class AuthorizationDetailsTests(unittest.TestCase):
    def test_full_argv_command_url_and_path_are_readable_without_json_dump(self):
        long_path = "/tmp/" + "target" * 400
        text = authorization_details({"argv": ["python3", "-c", "print('hello world')"],
                                      "command": "first command\nsecond command",
                                      "path": long_path, "url": "https://example.com/path?a=b"})
        self.assertIn("argv：python3 -c", text)
        self.assertIn("command：first command\n  second command", text)
        self.assertIn(long_path, text)
        self.assertIn("url：https://example.com/path?a=b", text)
        self.assertNotIn('{"', text)

    def test_secrets_are_redacted_and_hidden_controls_are_visible_in_targets(self):
        text = authorization_details({"command": "echo \x1b[2Jfixture-key", "path": "/tmp/a\u202eb", "api_key": "fixture-key"}, ("fixture-key",))
        self.assertIn("\\u001b[2J", text)
        self.assertIn("\\u202e", text)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("fixture-key", text)

    def test_large_file_body_preview_never_shortens_destination(self):
        text = authorization_details({"path": "/tmp/requested-target.txt", "content": "value " * 1000})
        self.assertIn("path：/tmp/requested-target.txt", text)
        self.assertIn("共 6000 字符", text)
        self.assertIn("预览前 1000", text)
        self.assertLess(len(text), 1200)


if __name__ == "__main__":
    unittest.main()
