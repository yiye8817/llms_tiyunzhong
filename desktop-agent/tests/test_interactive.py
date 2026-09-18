import contextlib
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from fusion_agent.cli import apply_run_overrides, execute_run, main, parser
from fusion_agent.config import ROOT, Settings
from fusion_agent.interactive import interactive_loop, pack_diagnostics


class InteractiveTests(unittest.TestCase):
    def settings(self, directory):
        return Settings(workspace=str(directory / "workspace"), runtime_dir=str(directory / "runtime"),
                        skills_dir=str(directory / "skills"), allowed_capabilities=["files", "skills", "shell"])

    def loop(self, settings, lines, execute, **patches):
        iterator = iter(lines)
        stdout, stderr = io.StringIO(), io.StringIO()
        def read(_):
            value = next(iterator, EOFError())
            if isinstance(value, BaseException):
                raise value
            return value
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            stack.enter_context(patch("fusion_agent.interactive._debug_event"))
            if "fusion_agent.parent_service.ensure_parent" not in patches:
                stack.enter_context(patch("fusion_agent.parent_service.ensure_parent", return_value={"state": "ready"}))
            for target, value in patches.items():
                stack.enter_context(patch(target, value))
            code = interactive_loop(settings, parser().parse_args(["chat"]), execute, read_line=read)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_empty_non_tty_prints_usage_without_parent_launch_or_input(self):
        with patch("sys.stdin.isatty", return_value=False), patch("builtins.input") as user_input, \
             patch("fusion_agent.parent_service.ensure_parent") as start, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([]), 2)
        user_input.assert_not_called()
        start.assert_not_called()

    def test_empty_tty_enters_chat_and_eof_returns_cleanly(self):
        with tempfile.TemporaryDirectory() as directory, patch("sys.stdin.isatty", return_value=True), \
             patch("fusion_agent.cli.load_settings", return_value=self.settings(Path(directory))), \
             patch("fusion_agent.parent_service.ensure_parent", return_value={"state": "ready"}), \
             patch("builtins.input", side_effect=EOFError), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([]), 0)

    def test_model_validation_retry_and_permissions_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(Path(directory))
            calls = []
            def execute(task, selected, args, on_run):
                calls.append((task, deepcopy(selected)))
                selected.allowed_capabilities.append("desktop")
                on_run({"run_id": "fixture-" + str(len(calls)), "run_dir": str(Path(directory) / str(len(calls)))})
                return 2 if len(calls) == 1 else 0
            code, out, err = self.loop(settings, ["第一任务", "/model missing", "/model qwen", "/retry", "/logs", "/quit"], execute,
                **{"fusion_agent.interactive.available_models": lambda _: ["chatgpt", "qwen"]})
            self.assertEqual(code, 0)
            self.assertEqual([task for task, _ in calls], ["第一任务", "第一任务"])
            self.assertEqual([item.model for _, item in calls], ["chatgpt", "qwen"])
            self.assertTrue(all("desktop" not in item.allowed_capabilities for _, item in calls))
            self.assertEqual(settings.model, "chatgpt")
            self.assertIn("未在 /v1/models", err)
            self.assertIn("fixture-2", out)

    def test_glm_and_kimi_model_aliases_can_be_selected_interactively(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(Path(directory))
            calls = []
            def execute(task, selected, args, on_run):
                calls.append((task, selected.model))
            models = ["chatgpt", "glm", "kimi", "web-glm", "web-kimi"]
            code, out, err = self.loop(
                settings,
                ["/model glm", "GLM 任务", "/model kimi", "Kimi 任务",
                 "/model web-glm", "Web GLM 任务", "/model web-kimi", "Web Kimi 任务", "/quit"],
                execute,
                **{"fusion_agent.interactive.available_models": lambda _: models},
            )
            self.assertEqual(code, 0)
            self.assertEqual(calls, [
                ("GLM 任务", "glm"), ("Kimi 任务", "kimi"),
                ("Web GLM 任务", "web-glm"), ("Web Kimi 任务", "web-kimi"),
            ])
            self.assertEqual(settings.model, "chatgpt")
            self.assertIn('"model": "glm"', out)
            self.assertIn('"model": "kimi"', out)
            self.assertIn('"model": "web-glm"', out)
            self.assertIn('"model": "web-kimi"', out)
            self.assertEqual(err, "已退出交互。\n")

    def test_workspace_change_does_not_execute_until_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(Path(directory))
            calls = []
            target = Path(directory) / "new scope"
            def execute(task, selected, args, on_run):
                calls.append((task, selected.workspace))
            _, out, err = self.loop(settings, ["/retry", "列出目标目录", f'/workspace "{target}"', "/retry", "/quit"], execute)
            self.assertEqual(calls, [("列出目标目录", settings.workspace), ("列出目标目录", str(target))])
            self.assertIn("尚无上一任务", err)
            self.assertFalse(target.exists())
            self.assertIn("下一轮", out)

    def test_invalid_slash_commands_never_dispatch_or_mutate(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            _, out, err = self.loop(self.settings(Path(directory)), ["/bin/sh -c 'echo bad'", "/workspace", "/model x extra", "/pack --output x", "/doctor --install", "/quit extra", "/quit"],
                                    lambda *a, **kw: calls.append(a))
            self.assertEqual(calls, [])
            self.assertEqual(err.count("Agent 无法执行"), 6)
            self.assertNotIn("Traceback", err)

    def test_ctrl_c_during_task_keeps_session_and_prompt_interrupt_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def execute(task, *a, **kw):
                calls.append(task)
                if len(calls) == 1:
                    raise KeyboardInterrupt
            code, _, err = self.loop(self.settings(Path(directory)), ["任务一", "任务二", KeyboardInterrupt()], execute)
            self.assertEqual(code, 0)
            self.assertEqual(calls, ["任务一", "任务一\n\n用户补充要求：\n任务二"])
            self.assertIn("返回交互输入", err)

    def test_explicit_allow_is_session_setting_but_not_original_config_mutation(self):
        args = parser().parse_args(["chat", "--allow", "browser,shell", "--model", "qwen"])
        settings = Settings()
        changed = apply_run_overrides(settings, args)
        self.assertEqual(changed.allowed_capabilities, ["browser", "files", "shell", "skills", "web"])
        self.assertEqual(settings.allowed_capabilities, ("files", "skills", "web"))

    def test_pack_captures_details_and_forwards_only_valid_run_id(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            agent_root = directory / "desktop-agent"
            agent_root.mkdir()
            (directory / "package.sh").write_text("fixture")
            invocations = []
            def packed(command, **options):
                invocations.append((command, options))
                output = Path(command[command.index("--output") + 1])
                output.parent.mkdir()
                output.write_bytes(b"fixture archive")
                return SimpleNamespace(returncode=0, stdout="private tool content", stderr="private debug")
            out = io.StringIO()
            with patch("fusion_agent.interactive.ROOT", agent_root), patch("fusion_agent.interactive.subprocess.run", side_effect=packed), \
                 patch("fusion_agent.interactive._debug_event") as event, contextlib.redirect_stdout(out):
                report = pack_diagnostics(self.settings(directory), run_id="fixture-1")
                with self.assertRaises(ValueError):
                    pack_diagnostics(self.settings(directory), run_id="../escape")
            self.assertEqual(len(invocations), 1)
            command, options = invocations[0]
            self.assertNotIn("--include-content", command)
            self.assertEqual(command[-2:], ["--agent-run-id", "fixture-1"])
            self.assertTrue(options["capture_output"])
            self.assertNotIn("shell", options)
            self.assertEqual(options["env"]["FUSION_PYTHON"], sys.executable)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(report["uploaded"], False)
            self.assertEqual(event.call_args.args[2]["payload"]["stdout"], "private tool content")

    def test_status_and_doctor_do_not_repeat_session_start(self):
        with tempfile.TemporaryDirectory() as directory:
            from unittest.mock import Mock
            start = Mock(return_value={"state": "ready"})
            code, out, _ = self.loop(self.settings(Path(directory)), ["/status", "/doctor", "/quit"], lambda *a, **kw: None,
                 **{"fusion_agent.parent_service.parent_status": lambda settings: {"state": "offline"},
                    "fusion_agent.parent_service.ensure_parent": start})
            self.assertEqual(code, 0)
            self.assertIn("offline", out)
            self.assertIn('"api_checked": false', out)
            self.assertEqual(start.call_count, 1)

    def test_session_start_failure_is_logged_and_keeps_debugging_available(self):
        with tempfile.TemporaryDirectory() as directory:
            from unittest.mock import Mock
            start = Mock(side_effect=ValueError("fixture 父服务未启动"))
            settings = self.settings(Path(directory))
            code, out, err = self.loop(settings, ["/logs", "/quit"], lambda *a, **kw: None,
                    **{"fusion_agent.parent_service.ensure_parent": start})
            self.assertEqual(code, 0)
            self.assertIn("可继续使用", err)
            self.assertIn("global_log", out)
            events = (Path(settings.runtime_dir) / "logs/agent.log").read_text()
            self.assertIn("interactive.parent_unavailable", events)
            self.assertNotIn('"event":', err)

    def test_standalone_doctor_skills_and_tools_never_start_parent(self):
        with tempfile.TemporaryDirectory() as directory, patch("fusion_agent.cli.load_settings", return_value=self.settings(Path(directory))), \
             patch("fusion_agent.parent_service.ensure_parent") as start, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for name in ("doctor", "skills", "tools"):
                self.assertEqual(main([name]), 0)
            start.assert_not_called()


class InteractiveHttpTests(unittest.TestCase):
    def test_real_http_model_switch_isolated_runs_and_formatted_answers(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.reply({"object": "list", "data": [{"id": "chatgpt"}, {"id": "qwen"}]})
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"])) ))
                content = json.dumps({"type": "final", "answer": "## 完成\n\n**回复**第 " + str(len(requests)) + " 轮。"}, ensure_ascii=False)
                self.reply({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}]})
            def reply(self, value):
                raw = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                config = directory / "agent.json"
                config.write_text(json.dumps({"base_url": f"http://127.0.0.1:{server.server_port}/v1", "workspace": "workspace", "runtime_dir": "runtime", "skills_dir": str(ROOT / "skills")}))
                result = subprocess.run([sys.executable, "-m", "fusion_agent", "chat", "--config", str(config)],
                     input="解释第一项独立问题\n/model qwen\n解释第二项独立问题\n/logs\n/quit\n", capture_output=True,
                     text=True, timeout=20, env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "FUSION_AGENT_API_KEY": "fixture-key"})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([row["model"] for row in requests], ["chatgpt", "qwen"])
                self.assertIn("解释第一项独立问题", json.dumps(requests[1], ensure_ascii=False))
                runs = list((directory / "runtime/runs").iterdir())
                self.assertEqual(len(runs), 2)
                self.assertTrue(all((run / "events.jsonl").exists() for run in runs))
                self.assertTrue(all((run / "result.md").exists() for run in runs))
                self.assertNotIn('"event":', result.stderr)
                self.assertNotIn("**回复**", result.stdout)
                self.assertNotIn("fixture-key", result.stdout + result.stderr)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


class PreflightRecordTests(unittest.TestCase):
    def settings(self, directory):
        return Settings(workspace=str(directory / "workspace"), runtime_dir=str(directory / "runtime"),
                        skills_dir=str(ROOT / "skills"))

    def check_failure(self, failure, expected_status, expected_code, expected_exit):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            settings = self.settings(directory)
            out, err = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {"FUSION_AGENT_API_KEY": "fixture-key"}), \
                 patch("fusion_agent.parent_service.ensure_parent", side_effect=failure), \
                 patch("fusion_agent.client.FusionClient.complete") as request, \
                 patch("fusion_agent.cli.components") as components, \
                 contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = execute_run("保留原始任务但不执行", settings, parser().parse_args(["run", "task"]))
            self.assertEqual(code, expected_exit)
            request.assert_not_called()
            components.assert_not_called()
            run_dir = next((directory / "runtime/runs").iterdir())
            state = json.loads((run_dir / "state.json").read_text())
            self.assertEqual(state["status"], expected_status)
            self.assertEqual(state["error_code"], expected_code)
            self.assertEqual(state["last_error"]["code"], expected_code)
            self.assertEqual(state["steps"], 0)
            self.assertFalse(state["task_executed"])
            for name in ("state.json", "transcript.json", "result.md", "events.jsonl"):
                self.assertEqual((run_dir / name).stat().st_mode & 0o777, 0o600)
            self.assertIn("保留原始任务但不执行", (run_dir / "transcript.json").read_text())
            self.assertNotIn(str(run_dir / "result.md"), err.getvalue())
            self.assertNotIn(str(run_dir / "events.jsonl"), err.getvalue())
            rows = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
            finished = [row for row in rows if row["event"] == "agent.finished"]
            self.assertEqual(len(finished), 1)
            self.assertEqual(finished[0]["status"], expected_status)
            self.assertNotIn('"event":', err.getvalue())

    def test_parent_failure_saves_zero_step_failed_result_with_parent_error_code(self):
        from fusion_agent.parent_service import ParentServiceError
        self.check_failure(ParentServiceError("parent_start_timeout", "fixture 父服务启动超时"), "failed", "parent_start_timeout", 2)

    def test_parent_start_interruption_saves_stopped_result(self):
        self.check_failure(KeyboardInterrupt(), "stopped", "interrupted", 130)

    def test_render_error_never_overwrites_completed_runtime_result(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            with patch.dict(os.environ, {"FUSION_AGENT_API_KEY": "fixture-key"}), \
                 patch("fusion_agent.parent_service.ensure_parent", return_value={"state": "ready"}), \
                 patch("fusion_agent.client.FusionClient.complete", return_value='{"type":"final","answer":"已生成答案"}'), \
                 patch("fusion_agent.cli.render_markdown", side_effect=ValueError("fixture render failure")), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(execute_run("解释问题", self.settings(directory), parser().parse_args(["run", "task"])), 2)
            run_dir = next((directory / "runtime/runs").iterdir())
            self.assertEqual(json.loads((run_dir / "state.json").read_text())["status"], "completed")
            self.assertIn("已生成答案", (run_dir / "result.md").read_text())
            self.assertNotIn("preflight", (run_dir / "state.json").read_text())
            rows = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
            self.assertEqual([row["status"] for row in rows if row["event"] == "agent.finished"], ["completed"])


if __name__ == "__main__":
    unittest.main()
