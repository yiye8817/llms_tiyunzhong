"""Exercise checkpoint wiring through real CLI tasks and interactive commands."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fusion_agent.cli import execute_run, parser
from fusion_agent.client import ClientError
from fusion_agent.config import Settings
from fusion_agent.history import History
from fusion_agent.interactive import interactive_loop


class ConversationCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(workspace=str(self.root / "workspace"), runtime_dir=str(self.root / "runtime"),
                                 skills_dir=str(self.root / "skills"), model="qwen")

    def chat(self, inputs, complete, execute=execute_run):
        lines = iter(inputs)
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"FUSION_AGENT_API_KEY": "fixture-key"}), \
             patch("fusion_agent.parent_service.ensure_parent", return_value={"state": "ready"}), \
             patch("fusion_agent.client.FusionClient.complete", side_effect=complete), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = interactive_loop(self.settings, parser().parse_args(["chat"]), execute,
                                    read_line=lambda _: next(lines))
        self.assertEqual(code, 0)
        return out.getvalue(), err.getvalue()

    def test_continue_and_followup_preserve_actual_progress_until_explicit_new(self):
        requests = []
        goal = "ORIGINAL-GOAL-712 创建文件 report.md 并读取核对"
        def complete(messages):
            requests.append(json.loads(json.dumps(messages)))
            count = len(requests)
            if count == 1:
                return json.dumps({"type": "action", "tool": "files.write", "arguments": {
                    "path": "report.md", "content": "verified report", "overwrite": False}, "summary": "创建报告"})
            if count == 2:
                return json.dumps({"type": "action", "tool": "files.read", "arguments": {"path": "report.md"}, "summary": "回读核验"})
            if count == 3:
                raise ClientError("http_error", "网页暂时繁忙", 502, server_code="candidate_generation_failed")
            if count in (4, 5):
                encoded = json.dumps(messages, ensure_ascii=False)
                self.assertIn(goal, encoded)
                self.assertIn("untrusted_tool_observation", encoded)
                self.assertIn("verified report", encoded)
                return json.dumps({"type": "final", "answer": "报告已创建并核对，复用之前的实际结果。"}, ensure_ascii=False)
            self.assertEqual(count, 6)
            self.assertNotIn(goal, json.dumps(messages, ensure_ascii=False))
            return '{"type":"final","answer":"新的独立回答"}'
        out, err = self.chat([goal, "继续", "补充解释结果", "/context", "/new", "独立解释一下", "/quit"], complete)
        self.assertEqual(len(requests), 6)
        self.assertEqual((self.root / "workspace/report.md").read_text(), "verified report")
        states = [json.loads(path.read_text()) for path in (self.root / "runtime/runs").glob("*/state.json")]
        self.assertEqual(len(states), 4)
        self.assertEqual(sum(state["steps"] for state in states), 2)
        self.assertEqual(sum(state["status"] == "failed" for state in states), 1)
        self.assertIn("上下文已保留", err)
        self.assertIn("复用之前", out)
        self.assertNotIn("session.capture_failed", (self.root / "runtime/logs/agent.log").read_text())
        history = History(self.settings.runtime_dir)
        self.assertEqual(len(history.list_sessions()), 2)
        self.assertIn("补充解释结果", [row["task"] for row in history.list_runs()])

    def test_continue_without_runtime_retries_preflight_input_then_new_clears_it(self):
        tasks = []
        def before_runtime(task, settings, args, on_run):
            tasks.append(task)  # Simulate failure before Runtime creates a checkpoint.
            return 2
        out, _ = self.chat(["先检查日志", "继续", "/new", "继续", "/quit"], lambda *_: self.fail("No model call"), before_runtime)
        self.assertEqual(tasks, ["先检查日志", "先检查日志"])
        self.assertIn("当前还没有可继续", out)

    def test_retry_keeps_context_and_recording_new_text_cannot_clear_it(self):
        requests = []
        def complete(messages):
            requests.append(json.loads(json.dumps(messages)))
            return '{"type":"final","answer":"已有答案"}'
        self.chat(["KEEP-TARGET", "/retry", "创建skill", "/new", "/context", "/skill cancel", "补充上下文", "/quit"], complete)
        self.assertEqual(len(requests), 3)
        self.assertTrue(all("KEEP-TARGET" in json.dumps(value) for value in requests))
        drafts = [json.loads(p.read_text()) for p in (self.root / "runtime/skill-drafts").glob("*.json")]
        self.assertEqual(drafts[0]["steps"], ["/new", "/context"])

    def test_existing_context_does_not_hide_new_input_after_preflight_failure(self):
        requests, executions = [], []
        failed = False
        def execute(task, settings, args, on_run):
            nonlocal failed
            executions.append(task)
            if task == "B 新增限制" and not failed:
                failed = True
                raise ValueError("父服务暂未就绪")
            return execute_run(task, settings, args, on_run=on_run)
        def complete(messages):
            requests.append(json.loads(json.dumps(messages)))
            return '{"type":"final","answer":"已回答"}'
        self.chat(["A 原始目标", "B 新增限制", "继续", "/quit"], complete, execute)
        self.assertEqual(executions, ["A 原始目标", "B 新增限制", "B 新增限制"])
        self.assertEqual(len(requests), 2)
        encoded = json.dumps(requests[-1], ensure_ascii=False)
        self.assertIn("A 原始目标", encoded)
        self.assertIn("B 新增限制", encoded)


if __name__ == "__main__":
    unittest.main()
