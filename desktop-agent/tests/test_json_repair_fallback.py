from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fusion_agent.runtime as runtime_module
from fusion_agent.runtime import Runtime
from test_runtime import Registry, ScriptedClient, final
from test_protocol_repair import shell_registry


class JsonRepairFallbackTests(unittest.TestCase):
    def run_runtime(self, replies, *, expected_calls):
        events = []
        client = ScriptedClient(replies)
        with tempfile.TemporaryDirectory() as temporary:
            result = Runtime(client, Registry(), Path(temporary) / "run",
                             event=lambda name, fields: events.append((name, fields))).run("执行任务")
        self.assertEqual(len(client.messages), expected_calls)
        return result, events

    def test_local_repair_is_logged_before_any_fallback(self):
        broken = '{"type":"final","answer":"line1\\nline2",}'
        result, events = self.run_runtime([broken], expected_calls=1)
        self.assertEqual(result["status"], "completed")
        local = [(name, fields["payload"].get("stage"), fields["payload"].get("method"))
                 for name, fields in events if name == "model.json_repair_succeeded"]
        self.assertEqual(local, [("model.json_repair_succeeded", "local", "python_deterministic_v6")])
        self.assertFalse(any(name == "model.json_repair_started" and fields["payload"].get("stage") != "local"
                             for name, fields in events))

    def test_local_metadata_repair_bypasses_open_source_repair(self):
        broken = (r'{"type":"action","tool":"shell.run","arguments":{"argv":\["bash","-c","printf safe"\]},'
                  r'"summary":"查找 page\_id","plan":\["读取页面","继续任务"\]}')
        calls, events = [], []
        client = ScriptedClient([broken, final("已完成")])
        with patch.object(runtime_module, "_json_repair", side_effect=AssertionError("不应进入 json_repair")):
            with tempfile.TemporaryDirectory() as temporary:
                result = Runtime(client, shell_registry(calls), Path(temporary) / "run",
                                 event=lambda name, fields: events.append((name, fields))).run("执行格式修复测试")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(client.messages), 2)
        self.assertTrue(any(name == "model.json_repair_succeeded"
                            and fields["payload"].get("stage") == "local"
                            for name, fields in events))
        self.assertFalse(any(name == "model.json_repair_started"
                             and fields["payload"].get("stage") == "json_repair"
                             for name, fields in events))

    def test_open_source_repair_is_used_before_model(self):
        with patch.object(runtime_module, "_json_repair",
                          lambda raw, return_objects=True: {"type": "final", "answer": "开源修复"}):
            result, events = self.run_runtime(["不是 JSON"], expected_calls=1)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "开源修复")
        stages = [(name, fields["payload"].get("stage")) for name, fields in events
                  if name.startswith("model.json_repair_")]
        self.assertEqual(stages, [("model.json_repair_started", "local"),
                                  ("model.json_repair_started", "json_repair"),
                                  ("model.json_repair_succeeded", "json_repair")])

    def test_model_repair_is_used_after_open_source_failure(self):
        def broken(*args, **kwargs):
            raise ValueError("无法修复")
        with patch.object(runtime_module, "_json_repair", broken):
            result, events = self.run_runtime(["不是 JSON", final("模型修复")], expected_calls=2)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "模型修复")
        self.assertIn(("model.json_repair_succeeded", "model"),
                      [(name, fields["payload"].get("stage")) for name, fields in events
                       if name.startswith("model.json_repair_")])

    def test_all_repair_stages_fail_without_execution(self):
        def broken(*args, **kwargs):
            raise ValueError("无法修复")
        with patch.object(runtime_module, "_json_repair", broken):
            result, events = self.run_runtime(["不是 JSON", "仍然不是 JSON"], expected_calls=2)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "invalid_protocol")
        self.assertTrue(any(name == "model.json_repair_failed" and fields["payload"].get("stage") == "model"
                            for name, fields in events))

    def test_incomplete_local_repair_runs_json_repair_as_protocol_diagnostic_only(self):
        broken = (r'{"type":"action","tool":"shell.run","arguments":{'
                  r'"command":"echo "HOME')
        calls = []
        events = []
        client = ScriptedClient([broken])
        with patch.object(runtime_module, "_json_repair",
                          lambda raw, return_objects=False: {
                              "type": "action", "tool": "shell.run",
                              "arguments": {"command": "echo safe"},
                              "summary": "诊断候选",
                          }):
            with tempfile.TemporaryDirectory() as temporary:
                result = Runtime(client, shell_registry(calls), Path(temporary) / "run",
                                 event=lambda name, fields: events.append((name, fields))).run("诊断")
        self.assertEqual(result["error_code"], "incomplete_response")
        self.assertEqual(calls, [])
        self.assertEqual(len(client.messages), 1)
        self.assertTrue(any(name == "model.json_repair_failed"
                            and fields["payload"].get("protocol_repair_attempted")
                            for name, fields in events))


if __name__ == "__main__":
    unittest.main()
