import contextlib
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

from fusion_agent.audit import Audit
from fusion_agent.config import ROOT, Settings, initialize, load_settings
from fusion_agent.contracts import ToolError, ToolSpec
from fusion_agent.registry import ToolRegistry


class RegistryTests(unittest.TestCase):
    def spec(self, handler, capability="shell", mutating=True):
        return ToolSpec("demo.run", "fixture", {"type": "object", "properties": {"n": {"type": "integer", "minimum": 1, "maximum": 3}}, "required": ["n"], "additionalProperties": False}, capability, mutating, handler)

    def test_no_side_effect_before_schema_and_grant_validation(self):
        calls = []
        registry = ToolRegistry([self.spec(lambda args: calls.append(args) or {})])
        for args in ({}, {"n": True}, {"n": 4}, {"n": 1, "extra": "x"}):
            result = registry.invoke("demo.run", args)
            self.assertEqual(result["error"]["code"], "invalid_arguments")
            self.assertEqual(result["execution"], {"status": "not_started"})
        denied = registry.invoke("demo.run", {"n": 1})
        self.assertEqual(denied["error"]["code"], "capability_denied")
        self.assertEqual(denied["execution"], {"status": "not_started"})
        self.assertEqual(calls, [])

    def test_capability_is_granted_only_once_for_the_session(self):
        grants = []
        registry = ToolRegistry([self.spec(lambda args: {"answer": args["n"]})], authorize=lambda spec, args: grants.append(spec.capability) or True)
        self.assertTrue(registry.invoke("demo.run", {"n": 1})["ok"])
        self.assertTrue(registry.invoke("demo.run", {"n": 2})["ok"])
        self.assertEqual(grants, ["shell"])

    def test_denial_is_not_reprompted_and_not_marked_unknown(self):
        grants = []
        registry = ToolRegistry([self.spec(lambda _: {})], authorize=lambda *_: grants.append(1) or False)
        for _ in range(2):
            self.assertNotIn("outcome_unknown", registry.invoke("demo.run", {"n": 1}))
        self.assertEqual(grants, [1])

    def test_own_credential_never_enters_model_observation(self):
        registry = ToolRegistry([self.spec(lambda _: {"stdout": "fixture-key", "nested": ["xfixture-keyy"]})], allowed=["shell"], secrets=["fixture-key"])
        self.assertNotIn("fixture-key", json.dumps(registry.invoke("demo.run", {"n": 1})))

    def test_failsafe_interrupts_the_entire_runtime(self):
        def fail(_):
            raise ToolError("desktop_failsafe", "stop")
        registry = ToolRegistry([self.spec(fail, "desktop")], allowed=["desktop"])
        with self.assertRaises(KeyboardInterrupt):
            registry.invoke("demo.run", {"n": 1})

    def test_exception_after_mutation_is_explicitly_unknown(self):
        def fail(_):
            raise RuntimeError("private raw error")
        events = []
        result = ToolRegistry([self.spec(fail)], allowed=["shell"],
                              event=lambda name, fields: events.append((name, fields))).invoke("demo.run", {"n": 1})
        self.assertTrue(result["outcome_unknown"])
        self.assertEqual(result["execution"], {"status": "unknown"})
        self.assertNotIn("private raw error", json.dumps(result))
        self.assertEqual(events[-1][1]["payload"]["result"], result)
        self.assertTrue(events[-1][1]["outcome_unknown"])

    def test_explicit_preflight_failure_preserves_details_without_unknown_outcome(self):
        def fail(_):
            raise ToolError("dependency_missing", "Missing executable fixture-key", not_executed=True,
                            details={"stage": "process_start", "errno": 2, "diagnostic": "fixture-key"})
        events = []
        registry = ToolRegistry([self.spec(fail)], allowed=["shell"], secrets=["fixture-key"],
                                event=lambda name, fields: events.append((name, fields)))
        result = registry.invoke("demo.run", {"n": 1})
        self.assertFalse(result["ok"])
        self.assertNotIn("outcome_unknown", result)
        self.assertEqual(result["execution"], {"status": "not_started"})
        self.assertEqual(result["error"]["details"]["errno"], 2)
        self.assertNotIn("fixture-key", json.dumps(result) + json.dumps(events))
        self.assertEqual(events[-1][1]["payload"]["result"], result)

    def test_tool_error_is_uncertain_by_default_and_bad_details_cannot_mask_failure(self):
        for details in ({"value": float("nan")}, {"value": object()}):
            with self.subTest(details=type(details["value"]).__name__):
                def fail(_):
                    raise ToolError("desktop_action_failed", "Input may have been dispatched", details=details)
                result = ToolRegistry([self.spec(fail)], allowed=["shell"]).invoke("demo.run", {"n": 1})
                self.assertEqual(result["error"]["code"], "desktop_action_failed")
                self.assertNotIn("details", result["error"])
                self.assertTrue(result["outcome_unknown"])

    def test_preflight_annotation_does_not_bypass_capability_grant(self):
        calls = []
        def fail(_):
            calls.append(1)
            raise ToolError("missing", "Not reached", not_executed=True)
        result = ToolRegistry([self.spec(fail)]).invoke("demo.run", {"n": 1})
        self.assertEqual(result["error"]["code"], "capability_denied")
        self.assertEqual(calls, [])

    def test_explicit_failed_result_is_logged_failed_and_keeps_real_output(self):
        events = []
        result = ToolRegistry([self.spec(lambda _: {"ok": False, "error": {"code": "command_failed"}, "stdout": "actual output", "returncode": 7})],
                              allowed=["shell"], event=lambda event, fields: events.append((event, fields))).invoke("demo.run", {"n": 1})
        self.assertFalse(result["ok"])
        self.assertEqual(events[-1][0], "tool.failed")
        self.assertEqual(events[-1][1]["payload"]["result"]["stdout"], "actual output")

    def test_failed_verification_cannot_be_overridden_by_ok_true(self):
        result = ToolRegistry([self.spec(lambda _: {"ok": True, "verification": {"status": "failed"}})], allowed=["shell"]).invoke("demo.run", {"n": 1})
        self.assertFalse(result["ok"])


class IntegrationTests(unittest.TestCase):
    def test_cli_invalid_protocol_then_502_preserves_diagnostics_without_retry(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                if len(requests) == 1:
                    status = 200
                    data = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "普通说明，未输出动作 JSON。"}}]}
                else:
                    status = 502
                    data = {"error": {"code": "candidate_generation_failed", "message": "网页生成失败", "details": {
                        "request_id": "fixture-request-502", "errors": [{"provider": "qwen", "code": "send_not_ready", "message": "send_disabled；token=PRIVATE-REMOTE-TOKEN"}]}}}
                raw = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("X-Request-ID", "fixture-request-502" if status == 502 else "fixture-request-first")
                self.end_headers()
                self.wfile.write(raw)
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                temp = Path(temporary)
                config = temp / "agent.json"
                config.write_text(json.dumps({"base_url": f"http://127.0.0.1:{server.server_port}/v1", "workspace": "workspace", "runtime_dir": "runtime", "skills_dir": str(ROOT / "skills")}))
                env = {**os.environ, "FUSION_AGENT_API_KEY": "fixture-local-key", "FUSION_AGENT_PYTHON": sys.executable, "FUSION_LOG_CONTENT": "1"}
                run = subprocess.run([str(ROOT / "run.sh"), "run", "生成系统报告", "--model", "qwen", "--config", str(config), "--non-interactive"], cwd=temp, env=env, capture_output=True, text=True, timeout=20)
                self.assertEqual(run.returncode, 2, run.stderr)
                self.assertEqual(len(requests), 1)  # JSON repair is local; no second POST is made.
                self.assertEqual(requests[0]["model"], "qwen")
                self.assertIn("本地 Python JSON 修复器", run.stdout)
                state_path = next((temp / "runtime/runs").glob("*/state.json"))
                state = json.loads(state_path.read_text())
                self.assertEqual(state["status"], "failed")
                self.assertEqual(state["steps"], 0)
                self.assertIsNone(state.get("last_error"))
                saved = (temp / "runtime/logs/agent.log").read_text()
                run_log = state_path.parent / "events.jsonl"
                self.assertEqual(run_log.read_text(), saved)
                self.assertEqual(run_log.stat().st_mode & 0o777, 0o600)
                self.assertNotIn(str(run_log), run.stderr)
                self.assertIn("本地 Python JSON 修复器", (state_path.parent / "result.md").read_text())
                rows = [json.loads(line) for line in saved.splitlines()]
                events = {row["event"] for row in rows}
                self.assertTrue({"model.http_request", "model.raw_reply", "model.invalid_protocol",
                                 "model.local_protocol_repair_failed", "agent.finished"} <= events)
                self.assertNotIn('"event":', run.stderr)
                self.assertNotIn("普通说明，未输出动作 JSON。", run.stderr)
                self.assertIn("回复格式需要修正", run.stderr)
                self.assertIn("任务未完成", run.stderr)
                self.assertIn("普通说明", saved)
                self.assertIn("server_retry", saved)
                for secret in ("fixture-local-key", "PRIVATE-REMOTE-TOKEN"):
                    self.assertNotIn(secret, run.stdout + run.stderr + state_path.read_text())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_private_config_initialization_and_relative_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = initialize(Path(temporary) / "agent.json")
            settings = load_settings(path)
            self.assertEqual(settings.workspace, str(path.parent / "workspace"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(settings.timeout, 900)
            with self.assertRaises(FileExistsError):
                initialize(path)

    def test_long_response_timeout_default_and_explicit_configuration(self):
        self.assertEqual(Settings().validate().timeout, 900)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "agent.json"
            path.write_text(json.dumps({"timeout": 1200}))
            self.assertEqual(load_settings(path).timeout, 1200)
        for value in (0, 1801, float("inf"), float("nan"), True):
            with self.subTest(timeout=value), self.assertRaises(ValueError):
                Settings(timeout=value).validate()

    def test_audit_does_not_store_task_arguments_or_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            terminal = io.StringIO()
            audit = Audit(Path(temporary) / "agent.log", secrets=["fixture-key"], stream=terminal)
            audit("tool.attempt", {"tool": "shell.run", "action_id": "fixture-key", "prompt": "private-task", "arguments": "private-args", "summary": "private-summary"})
            audit.close()
            text = (Path(temporary) / "agent.log").read_text()
            self.assertEqual(terminal.getvalue(), "")
            for secret in ("fixture-key", "private-task", "private-args", "private-summary"):
                self.assertNotIn(secret, text)

    def test_audit_payload_is_complete_and_redacted_in_file_without_terminal_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            terminal = io.StringIO()
            path = Path(temporary) / "agent.log"
            audit = Audit(path, secrets=["fixture-key"], stream=terminal, chunk_chars=50)
            audit("model.raw_reply", {"status": 502, "server_code": "send_not_ready", "payload": {"reply": "完整内容\n" * 100, "api_key": "fixture-key", "password": "private-pass"}})
            audit.close()
            self.assertEqual(terminal.getvalue(), "")
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertGreater(len(rows), 1)
            payload = json.loads(''.join(row["payload"] for row in rows))
            self.assertEqual(payload["reply"], "完整内容\n" * 100)
            self.assertEqual(payload["password"], "[REDACTED]")
            self.assertTrue(all(row["part"] == i+1 and row["parts"] == len(rows) and not row["truncated"] for i, row in enumerate(rows)))
            self.assertNotIn("fixture-key", path.read_text())
            self.assertNotIn("private-pass", path.read_text())

    def test_audit_metadata_mode_and_explicit_truncation(self):
        with tempfile.TemporaryDirectory() as temporary:
            terminal = io.StringIO()
            audit = Audit(Path(temporary) / "meta.log", stream=terminal, content=False)
            audit("model.request", {"payload": {"prompt": "content omitted"}})
            audit.close()
            saved = (Path(temporary) / "meta.log").read_text()
            self.assertTrue(json.loads(saved)["payload_omitted"])
            self.assertNotIn("content omitted", saved)
            self.assertEqual(terminal.getvalue(), "")
            bounded = io.StringIO()
            audit = Audit(Path(temporary) / "bounded.log", stream=bounded, max_content_chars=30, chunk_chars=20)
            audit("model.request", {"payload": {"prompt": "x"*100}})
            audit.close()
            rows = [json.loads(line) for line in (Path(temporary) / "bounded.log").read_text().splitlines()]
            self.assertTrue(all(row["truncated"] for row in rows))
            self.assertEqual(len(''.join(row["payload"] for row in rows)), 30)
            self.assertEqual(bounded.getvalue(), "")

    def test_audit_redacts_entire_cookie_and_basic_auth_headers(self):
        with tempfile.TemporaryDirectory() as temporary:
            terminal = io.StringIO()
            audit = Audit(Path(temporary) / "headers.log", stream=terminal)
            audit("tool.completed", {"payload": {"stdout": 'Cookie: session=FIRST-SECRET; other=SECOND-SECRET\nAuthorization: Basic BASIC-SECRET\npassword="FIRST \\"QUOTED-SECRET"'}})
            audit.close()
            for secret in ("FIRST-SECRET", "SECOND-SECRET", "BASIC-SECRET", "QUOTED-SECRET"):
                self.assertNotIn(secret, (Path(temporary) / "headers.log").read_text())
            self.assertEqual(terminal.getvalue(), "")

    def test_cli_real_http_command_file_verification_and_final_record(self):
        requests = []
        replies = [
            {"type": "action", "tool": "shell.run", "arguments": {"argv": [sys.executable, "-c", "from pathlib import Path; Path('result.txt').write_text('fixture-success')"]}, "summary": "创建测试文件"},
            {"type": "action", "tool": "files.read", "arguments": {"path": "result.txt"}, "summary": "核验文件内容"},
            {"type": "final", "answer": "## 文件结果\n\n**已创建并核验** `result.txt`。\n\n| 文件 | 状态 |\n| --- | --- |\n| result.txt | 已核验 |"},
        ]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                reply = replies[len(requests) - 1]
                raw = json.dumps({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(reply, ensure_ascii=False)}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                temp = Path(temporary)
                config = temp / "agent.json"
                config.write_text(json.dumps({"base_url": f"http://127.0.0.1:{server.server_port}/v1", "workspace": "workspace", "runtime_dir": "runtime", "skills_dir": str(ROOT / "skills")}))
                env = {**os.environ, "FUSION_AGENT_API_KEY": "fixture-key", "FUSION_AGENT_PYTHON": sys.executable}
                run = subprocess.run([
                    str(ROOT / "run.sh"), "run",
                    "运行命令生成输出，然后读取文件 result.txt 进行核验",
                    "--config", str(config), "--allow", "shell", "--non-interactive",
                ], cwd=temp, env=env, capture_output=True, text=True, timeout=20)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual((temp / "workspace/result.txt").read_text(), "fixture-success")
                self.assertIn("已创建并核验", run.stdout)
                self.assertIn("文件结果", run.stdout)
                self.assertNotIn("**已创建并核验**", run.stdout)
                self.assertNotIn("## 文件结果", run.stdout)
                self.assertNotIn("\x1b", run.stdout)  # Captured output is non-TTY plain text.
                state_path = next((temp / "runtime/runs").glob("*/state.json"))
                state = json.loads(state_path.read_text())
                self.assertEqual(state["status"], "completed")
                self.assertIsNone(state["pending_action"])
                self.assertNotIn("fixture-key", run.stderr)
                self.assertEqual(len(requests), 3)
                self.assertEqual(set(requests[0]), {"model", "messages", "stream"})
                self.assertEqual(requests[0]["model"], "chatgpt")
                run_log = state_path.parent / "events.jsonl"
                saved = run_log.read_text()
                self.assertEqual(saved, (temp / "runtime/logs/agent.log").read_text())
                self.assertNotIn('"event":', run.stderr)
                self.assertNotIn("fixture-success", run.stderr)
                self.assertIn("第 1 步 · 创建测试文件 [shell.run]", run.stderr)
                self.assertIn("第 2 步 · 核验文件内容 [files.read]", run.stderr)
                self.assertEqual(run.stderr.count("任务完成"), 1)
                self.assertIn("model.response", saved)
                self.assertIn("已创建并核验", saved)
                self.assertIn("已创建并核验", (state_path.parent / "result.md").read_text())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
