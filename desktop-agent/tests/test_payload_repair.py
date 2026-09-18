"""Local-only regression for malformed payloads observed in agent1.log."""

import json
from pathlib import Path
import tempfile
import unittest

from fusion_agent.runtime import ProtocolError, Runtime, parse_reply
from test_runtime import Registry, ScriptedClient


REPORTED_SHAPE = (
    r'{"type":"final","answer":"AI 行业摘要\\n\\n### 1. 大模型迭代\\n'
    r'\* **头部模型更新**：关注模型能力升级。\\n'
    r'\*\*总结\\*\*：以上是主要内容。"}'
)


class PayloadRepairTests(unittest.TestCase):
    def test_agent1_markdown_escapes_are_repaired_locally(self):
        changes = []
        reply = parse_reply(REPORTED_SHAPE, changes)
        self.assertEqual(reply["type"], "final")
        self.assertIn("* **头部模型更新**", reply["answer"])
        self.assertIn("* **头部模型更新**", reply["answer"])
        self.assertEqual([item["kind"] for item in changes],
                         ["markdown_payload_string_escapes"])
        self.assertEqual(changes[0]["characters"], ["*"])

    def test_literal_newlines_inside_answer_become_legal_json_newlines(self):
        source = '{"type":"final","answer":"第一行\n第二行\r\n第三行"}'
        changes = []
        reply = parse_reply(source, changes)
        self.assertEqual(reply["answer"], "第一行\n第二行\n第三行")
        self.assertEqual(changes[0]["kind"], "raw_json_string_newlines")
        self.assertEqual(changes[0]["count"], 2)

    def test_executable_fields_are_never_repaired(self):
        for source in (
            r'{"type":"action","tool":"shell\.run","arguments":{"argv":["true"]}}',
            r'{"type":"action","tool":"browser.open","arguments":{"url":"https://exa\*mple.com"}}',
            r'{"type":"action","tool":"files.write","arguments":{"path":"bad\*name","content":"ok"}}',
        ):
            with self.subTest(source=source), self.assertRaises(ProtocolError):
                parse_reply(source)

    def test_python_code_markdown_escapes_are_repaired_only_after_ast_check(self):
        source = (r'{"type":"action","tool":"python.run","arguments":{"code":"import sys\n'
                  r'item = sys.argv\[1\]\ntarget\_dir = item"},"summary":"修复代码",'
                  r'"plan":\["读取参数"\]}')
        changes = []
        action = parse_reply(source, changes)
        self.assertEqual(action["arguments"]["code"], "import sys\nitem = sys.argv[1]\ntarget_dir = item")
        self.assertEqual([item["kind"] for item in changes],
                         ["markdown_array_delimiters", "python_code_markdown_escapes"])

    def test_python_code_escape_repair_rejects_invalid_python(self):
        source = (r'{"type":"action","tool":"python.run","arguments":{"code":"x = \["},'
                  r'"summary":"修复代码"}')
        with self.assertRaises(ProtocolError):
            parse_reply(source)

    def test_unrepairable_json_fails_after_one_request_without_server_repair(self):
        client = ScriptedClient([r'{"type":"final","answer":"bad\q"}'])
        events = []
        with tempfile.TemporaryDirectory() as temporary:
            result = Runtime(client, Registry(), Path(temporary) / "run",
                             event=lambda name, fields: events.append((name, fields))).run("你好")
        self.assertEqual(result["error_code"], "invalid_protocol")
        self.assertEqual(len(client.messages), 1)
        self.assertNotIn("model.repair_requested", [name for name, _ in events])
        failed = next(fields for name, fields in events
                      if name == "model.local_protocol_repair_failed")
        self.assertFalse(failed["payload"]["server_retry"])


if __name__ == "__main__":
    unittest.main()
