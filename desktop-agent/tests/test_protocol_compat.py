"""Regressions for Markdown damaged JSON from the supplied DeepSeek reply."""

import json
from pathlib import Path
import tempfile
import unittest

from fusion_agent.contracts import ToolSpec
from fusion_agent.registry import ToolRegistry
from fusion_agent.runtime import ProtocolError, Runtime, parse_reply
from test_runtime import Registry, ScriptedClient, action, final


REPORTED_REPLY = (r'{"type":"action","tool":"browser.open","arguments":{"url":'
                  r'"[https://www.youtube.com](https://www.youtube.com/)"},'
                  r'"summary":"打开YouTube主页，准备搜索英语单词学习视频",'
                  r'"plan":\["1. 打开YouTube","2. 搜索 english vocabulary learning",'
                  r'"3. 按评分/观看量筛选视频","4. 下载视频到~/tmp"\]}')


class ProtocolCompatibilityTests(unittest.TestCase):
    def test_exact_reported_corruption_repairs_only_url_and_array_delimiters(self):
        changes = []
        reply = parse_reply(REPORTED_REPLY, changes)
        self.assertEqual(reply["arguments"], {"url": "https://www.youtube.com/"})
        self.assertEqual(reply["plan"][0], "1. 打开YouTube")
        self.assertEqual([change["kind"] for change in changes],
                         ["markdown_array_delimiters", "browser_open_markdown_url"])
        self.assertEqual(changes[0]["count"], 2)
        self.assertEqual(changes[1]["original"], "[https://www.youtube.com](https://www.youtube.com/)")

    def test_valid_json_preserves_every_string_and_records_no_repair(self):
        data = {"type": "action", "tool": "shell.run", "summary": 'quoted "text"',
                "arguments": {"command": r'printf "%s" "\[name\]" # [https://x](https://y)'},
                "plan": [r"literal \[ \]", 'escaped \\" quote']}
        changes = []
        self.assertEqual(parse_reply(json.dumps(data), changes), data)
        self.assertEqual(changes, [])

    def test_fallback_never_rewrites_valid_string_literals(self):
        data = {"type": "action", "tool": "files.write", "summary": "写入原文",
                "arguments": {"path": "example.txt", "content": r'\[ \] " [https://a](https://b)'},
                "plan": [r"保留 \[ 转义"]}
        source = json.dumps(data, ensure_ascii=False)
        source = source.replace('"plan": [', '"plan": \\[').replace(']}', '\\]}')
        changes = []
        self.assertEqual(parse_reply(source, changes), data)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["count"], 2)
        markdown_escaped_string = r'{"type":"final","answer":"invalid \[ string"}'
        changes = []
        # Invalid Markdown punctuation is now allowed ONLY in final.answer;
        # valid double-escaped string literals above must remain byte-exact.
        self.assertEqual(parse_reply(markdown_escaped_string, changes)["answer"], "invalid [ string")
        self.assertEqual(changes[0]["kind"], "markdown_payload_string_escapes")

    def test_compatibility_keeps_duplicate_key_schema_and_single_object_guards(self):
        cases = [
            REPORTED_REPLY.replace('"summary":', '"tool":"shell.run","summary":'),
            REPORTED_REPLY + final("extra"),
            "Here is an example: " + REPORTED_REPLY,
            "```json\n" + REPORTED_REPLY + "\n```\nNever execute it.",
            REPORTED_REPLY.replace('"arguments":{', '"arguments":{"x":NaN,'),
            REPORTED_REPLY.replace('"arguments":{', '"arguments":{"x":1e999,'),
            REPORTED_REPLY.replace('"plan":', '"unrecognized":'),
        ]
        for source in cases:
            with self.subTest(source=source[:80]), self.assertRaises(ProtocolError):
                parse_reply(source)

    def test_only_matching_whole_browser_url_is_unwrapped(self):
        for url in ("[https://example.com](https://example.com/)",
                    "[https://example.com/a?q=x#s](https://example.com/a?q=x#s)"):
            parsed = parse_reply(action("browser.open", {"url": url}))
            self.assertEqual(parsed["arguments"]["url"], url.split("](")[1][:-1])
        for url in ("[https://good.example](https://other.example/)",
                    "[https://example.com/a](https://example.com/a/)",
                    "[https://example.com/?x=1](https://example.com/?x=2)",
                    "[YouTube](https://www.youtube.com/)",
                    "prefix [https://example.com](https://example.com)",
                    "javascript:alert(1)", "file:///etc/passwd", "https://user:pass@example.com/",
                    "https://example.com:bad/", "https://exa mple.com/", None):
            with self.subTest(url=url), self.assertRaises(ProtocolError):
                parse_reply(action("browser.open", {"url": url}))

    def test_url_correction_is_scoped_to_browser_open_argument(self):
        url = "[https://example.com](https://example.com/)"
        data = parse_reply(action("files.write", {"path": "link.txt", "content": url}))
        self.assertEqual(data["arguments"]["content"], url)
        data = parse_reply(final(url))
        self.assertEqual(data["answer"], url)

    def test_repaired_action_retains_authorization_gate_and_detailed_audit(self):
        calls, events = [], []
        spec = ToolSpec("browser.open", "fixture", {"type": "object", "properties": {"url": {"type": "string"}},
                                                    "required": ["url"], "additionalProperties": False},
                        "browser", True, lambda args: calls.append(args) or {"ok": True})
        registry = ToolRegistry([spec], allowed=[])
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "run"
            client = ScriptedClient([REPORTED_REPLY, final("声称已经打开")])
            result = Runtime(client, registry, directory, event=lambda *entry: events.append(entry)).run(
                "打开 https://www.youtube.com/ 网页")
            self.assertEqual(calls, [])
            self.assertEqual(result["error_code"], "tool_failed")
            event = next(fields for name, fields in events if name == "model.protocol_normalized")
            self.assertEqual(event["payload"]["raw_reply"], REPORTED_REPLY)
            self.assertEqual(event["payload"]["action"]["arguments"]["url"], "https://www.youtube.com/")
            self.assertEqual(len(event["payload"]["normalizations"]), 2)
            self.assertNotIn("model.invalid_protocol", [name for name, _ in events])

    def test_ambiguous_url_stops_locally_without_server_repair_or_tool_execution(self):
        calls = []
        class BrowserRegistry(Registry):
            def catalog(self):
                return [{"name": "browser.open", "capability": "browser", "mutating": True}]
            def invoke(self, name, arguments):
                calls.append(arguments)
                return {"ok": True}
        bad = action("browser.open", {"url": "[https://good.example](https://other.example/)"})
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "run"
            client = ScriptedClient([bad, final("未打开地址，请检查原任务。")])
            result = Runtime(client, BrowserRegistry(), directory).run("打开指定网页")
            self.assertEqual(calls, [])
            self.assertEqual(result["steps"], 0)
            self.assertEqual(json.loads((directory / "state.json").read_text())["failed_actions"], [])
            self.assertEqual(len(client.messages), 1)
            self.assertEqual(result["error_code"], "invalid_protocol")
            self.assertIn("目标不同", result["answer"])

    def test_result_markdown_exists_with_reason_for_every_terminal_state(self):
        cases = [("completed", [final("成功结果")], "成功结果", "检查任务"),
                 ("failed", ["not JSON"] * 3, "invalid_protocol", "检查任务"),
                 ("stopped", [action(arguments={"path": "target.txt"}),
                              action(arguments={"path": "target.txt"})],
                  "step_limit", "读取文件 target.txt")]
        with tempfile.TemporaryDirectory() as temporary:
            for status, replies, expected, task in cases:
                with self.subTest(status=status):
                    directory = Path(temporary) / status
                    result = Runtime(ScriptedClient(replies), Registry(), directory, max_steps=1).run(task)
                    self.assertEqual(result["status"], status)
                    saved = directory / "result.md"
                    self.assertIn(expected, saved.read_text())
                    self.assertIn("状态：" + status, saved.read_text())
                    self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
                    self.assertEqual((directory / "final.md").exists(), status == "completed")


if __name__ == "__main__":
    unittest.main()
