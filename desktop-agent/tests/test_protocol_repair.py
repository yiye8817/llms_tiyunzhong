"""Strict, bounded repairs for the malformed replies in the uploaded Agent run."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

from fusion_agent.contracts import ToolSpec
from fusion_agent.registry import ToolRegistry
from fusion_agent.runtime import ProtocolError, Runtime, parse_reply
from test_runtime import ScriptedClient, action, final


# These preserve the two actual quote failures and Markdown bracket damage from
# the supplied transcript, without copying its user's private working directory.
BROKEN_COMMANDS = [
    (r'{"type":"action","tool":"shell.run","arguments":{"command":'
     r'''"python3 -c "import playwright; print('playwright installed')" 2>/dev/null || echo 'playwright not found'"},'''
     r'"summary":"检查playwright是否已安装","plan":\["检查安装状态","继续原任务"\]}'),
    (r'{"type":"action","tool":"shell.run","arguments":{"command":'
     r'''"python3 -c 'import playwright; print("playwright installed")' 2>/dev/null || echo 'playwright not found'"},'''
     r'"summary":"检查playwright是否已安装","plan":\["检查安装状态","继续原任务"\]}'),
]


def shell_registry(calls, allowed=("shell",)):
    def invoke(arguments):
        calls.append(arguments)
        return {"ok": True, "stdout": "playwright installed", "returncode": 0,
                "verification": {"status": "verified", "scope": "shell", "method": "exit_status"}}
    spec = ToolSpec("shell.run", "Fixture: never runs a command", {
        "type": "object", "properties": {"argv": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
        "required": ["argv"], "additionalProperties": False}, "shell", True, invoke)
    return ToolRegistry([spec], allowed=allowed)


class DeterministicRepairTests(unittest.TestCase):
    def test_uploaded_files_write_reply_repairs_markdown_path_and_plan_escapes(self):
        # Shortened from events.jsonl: the page converted the underscore in the
        # JSON path and the plan's structural brackets to Markdown escapes.
        source = (r'{ "type": "action", "tool": "files.write", "arguments": {'
                  r' "path": "ai\_news.md", "content": "# AI 新闻摘要\\n\\n来源: TechCrunch AI RSS",'
                  r' "overwrite": true }, "summary": "保存 Markdown 报告",'
                  r' "plan": \[ "保存新闻报告", "输出最终回答" \] }')
        changes = []
        parsed = parse_reply(source, changes)
        self.assertEqual(parsed["tool"], "files.write")
        self.assertEqual(parsed["arguments"]["path"], "ai_news.md")
        self.assertEqual(parsed["arguments"]["content"], r"# AI 新闻摘要\n\n来源: TechCrunch AI RSS")
        self.assertEqual(parsed["plan"], ["保存新闻报告", "输出最终回答"])
        self.assertEqual([item["kind"] for item in changes],
                         ["markdown_array_delimiters", "markdown_json_string_escapes"])
        self.assertEqual(changes[0]["count"], 2)
        self.assertEqual(changes[1]["count"], 1)
        self.assertEqual(changes[1]["characters"], ["_"])
        self.assertEqual(changes[1]["positions"], [source.index(r"\_")])

    def test_action_metadata_markdown_escapes_are_repaired_locally(self):
        source = (r'{"type":"action","tool":"shell.run","arguments":{"argv":\["bash","-c","printf safe"\]},'
                  r'"summary":"查找 page\_id","plan":\["读取页面","继续任务"\]}')
        changes = []
        parsed = parse_reply(source, changes)
        self.assertEqual(parsed["arguments"]["argv"], ["bash", "-c", "printf safe"])
        self.assertEqual(parsed["summary"], "查找 page_id")
        self.assertEqual(parsed["plan"], ["读取页面", "继续任务"])
        self.assertEqual([item["kind"] for item in changes],
                         ["markdown_array_delimiters", "markdown_action_metadata_escapes"])
        self.assertEqual(changes[1]["scope"], "summary")
        self.assertEqual(changes[1]["count"], 1)

    def test_string_repair_preserves_every_legal_json_escape(self):
        answer = 'quote " slash / backslash \\ newline\n tab\t unicode 中 regex \\.'
        expected = {"type": "action", "tool": "files.write",
                    "arguments": {"path": "ai_news.md", "content": answer},
                    "summary": "保存", "plan": ["完成"]}
        source = json.dumps(expected, ensure_ascii=False)
        source = source.replace('"path": "ai_news.md"', '"path": "ai\\_news.md"')
        changes = []
        parsed = parse_reply(source, changes)
        self.assertEqual(parsed["arguments"]["content"], answer)
        self.assertEqual(parsed["arguments"]["path"], "ai_news.md")
        self.assertEqual(changes[0]["characters"], ["_"])

    def test_non_markdown_and_broken_unicode_escapes_are_never_guessed(self):
        for source in (r'{"type":"final","answer":"invalid \q escape"}',
                       r'{"type":"final","answer":"invalid \u12x4 escape"}'):
            with self.subTest(source=source), self.assertRaises(ProtocolError):
                parse_reply(source)

    def test_invalid_string_escape_repair_is_limited_to_fixed_file_path_fields(self):
        accepted = (
            ("files.write", r"reports/ai\_news.md"),
            ("files.read", r"inputs/source\_one.txt"),
            ("files.list", r"reports/daily\_items"),
            ("file.write", r"reports/alias\_name.md"),
            ("file.read", r"inputs/alias\_source.txt"),
            ("file.list", r"reports/alias\_items"),
        )
        for tool, escaped_path in accepted:
            source = (r'{"type":"action","tool":"' + tool
                      + r'","arguments":{"path":"' + escaped_path
                      + r'","content":"safe"},"summary":"file operation"}')
            changes = []
            parsed = parse_reply(source, changes)
            self.assertEqual(parsed["arguments"]["path"], escaped_path.replace(r"\_", "_"))
            self.assertEqual([item["kind"] for item in changes], ["markdown_json_string_escapes"])

        rejected = (
            r'{"type":"action","tool":"shell.run","arguments":{"command":"echo safe\; echo injected"},"summary":"run"}',
            r'{"type":"action","tool":"shell\.run","arguments":{"argv":["true"]},"summary":"run"}',
            r'{"type":"action","tool":"browser.open","arguments":{"url":"https://example.com/path\?admin=1"},"summary":"open"}',
            r'{"type":"action","tool":"files.write","arguments":{"path":"safe.md","content":"body\_text"},"summary":"write"}',
            r'{"type":"action","tool":"files.write","arguments":{"path":"unsafe\-name.md","content":"safe"},"summary":"write"}',
            r'{"type":"action","tool":"write_file","arguments":{"path":"unsafe\_alias.md","content":"safe"},"summary":"write"}',
            r'{"type":"action","tool":"files.write","arguments":{"path":"safe\_name.md","content":"body\_text"},"summary":"write"}',
        )
        for source in rejected:
            with self.subTest(source=source), self.assertRaises(ProtocolError):
                parse_reply(source)

    def test_combined_structure_repairs_preserve_each_argument_character(self):
        code = 'print("literal, } [ \\] BOM \ufeff ```json")'
        expected = {"type": "action", "tool": "shell.run", "arguments": {"argv": [sys.executable, "-c", code]},
                    "summary": "测试格式修复", "plan": [r"原样保留 \[ 与 \]", "继续任务"]}
        source = json.dumps(expected, ensure_ascii=False)
        source = source.replace('"plan": [', '"plan": \\[')
        source = source[:-2] + ',\\],}'
        wrapped = " \ufeff```json\n" + source + "\n```\n"
        changes = []
        self.assertEqual(parse_reply(wrapped, changes), expected)
        self.assertEqual([item["kind"] for item in changes],
                         ["leading_bom", "whole_json_fence", "markdown_array_delimiters", "trailing_commas"])
        self.assertEqual(changes[-1]["count"], 2)

    def test_fence_and_bom_inside_string_are_never_removed(self):
        text = '\ufeff```json\n[1,] {"key": "value",}\n```'
        changes = []
        self.assertEqual(parse_reply(final(text), changes)["answer"], text)
        self.assertEqual(changes, [])

    def test_whole_fence_with_json_bom_is_recorded(self):
        changes = []
        self.assertEqual(parse_reply("```JSON\n\ufeff" + final("done") + "\n```", changes)["answer"], "done")
        self.assertEqual([item["kind"] for item in changes], ["whole_json_fence", "leading_bom"])

    def test_trailing_commas_can_be_repaired_at_multiple_depths(self):
        source = ('{"type":"action","tool":"files.read","arguments":{"path":"a", "hints":[1,{"x":true,},],},'
                  '"summary":"读取", "plan":["继续",],}')
        changes = []
        parsed = parse_reply(source, changes)
        self.assertEqual(parsed["arguments"], {"path": "a", "hints": [1, {"x": True}]})
        self.assertEqual(changes[0]["count"], 5)

    def test_complete_shell_command_quotes_are_repaired_locally(self):
        changes = []
        parsed = parse_reply(BROKEN_COMMANDS[0], changes)
        command = parsed["arguments"]["command"]
        self.assertIn('python3 -c "import playwright;', command)
        self.assertIn("echo 'playwright not found'", command)
        self.assertEqual([item["kind"] for item in changes],
                         ["unescaped_shell_command_quotes", "markdown_array_delimiters"])

    def test_truncated_shell_command_is_not_completed(self):
        source = (r'{"type":"action","tool":"shell.run","arguments":{'
                  r'"command":"echo "HOME')
        changes = []
        with self.assertRaises(ProtocolError) as caught:
            parse_reply(source, changes)
        self.assertEqual(changes, [])
        self.assertEqual(caught.exception.details["original_error"]["position"], 66)

    def test_invalid_quotes_and_structural_ambiguities_still_fail_without_committing_changes(self):
        invalid = [
            '{"type":"final","answer":,}', '{"type":"final","answer":"ok",,}',
            '{"type":"action","tool":"files.read","arguments":{,},"summary":"read"}',
            '{"type":"action","tool":"files.read","arguments":{"a":[,]},"summary":"read"}',
            '{"type":"action","tool":"files.read","arguments":{"a":[1,,]},"summary":"read"}',
            '{"type":"final","answer":"ok",]',
            '\ufeff\ufeff' + final("done"),
        ]
        for source in invalid:
            changes = []
            with self.subTest(source=source[:75]), self.assertRaises(ProtocolError):
                parse_reply(source, changes)
            self.assertEqual(changes, [])

    def test_repair_keeps_duplicate_key_finite_schema_and_whole_object_checks(self):
        invalid = [
            '{"type":"final","answer":"one","answer":"two",}',
            '{"type":"action","tool":"files.read","arguments":{"x":NaN,},"summary":"read",}',
            '{"type":"action","tool":"files.read","arguments":{"x":1e999,},"summary":"read",}',
            '{"type":"final","answer":"ok","extra":true,}',
            '{"type":"final","answer":"one",}' + final("two"),
            'Example only:\n```json\n{"type":"final","answer":"one",}\n```',
        ]
        for source in invalid:
            with self.subTest(source=source[:80]), self.assertRaises(ProtocolError):
                parse_reply(source)

    def test_trailing_comma_repair_is_bounded(self):
        source = '{"type":"action","tool":"files.read","summary":"x","arguments":{' + ','.join(
            json.dumps(str(index)) + ':[1,]' for index in range(65)) + '}}'
        with self.assertRaises(ProtocolError) as caught:
            parse_reply(source)
        self.assertEqual(caught.exception.details["trailing_commas_removed"], 64)

class ModelRepairTests(unittest.TestCase):
    def test_unsafe_string_escape_repairs_never_dispatch_an_action(self):
        cases = (
            (
                r'{"type":"action","tool":"shell.run","arguments":{"command":"echo safe\; echo injected"},"summary":"run"}',
                "请运行命令 echo safe",
                ToolSpec("shell.run", "fixture", {"type": "object", "properties": {
                    "command": {"type": "string"}, "argv": {"type": "array", "items": {"type": "string"}}},
                    "additionalProperties": False}, "shell", True, None),
                "shell",
            ),
            (
                r'{"type":"action","tool":"shell\.run","arguments":{"argv":["true"]},"summary":"run"}',
                "请运行命令 true",
                ToolSpec("shell.run", "fixture", {"type": "object", "properties": {
                    "command": {"type": "string"}, "argv": {"type": "array", "items": {"type": "string"}}},
                    "additionalProperties": False}, "shell", True, None),
                "shell",
            ),
            (
                r'{"type":"action","tool":"browser.open","arguments":{"url":"https://example.com/path\?admin=1"},"summary":"open"}',
                "请在浏览器打开 https://example.com/path?admin=1",
                ToolSpec("browser.open", "fixture", {"type": "object", "properties": {
                    "url": {"type": "string"}}, "required": ["url"], "additionalProperties": False},
                    "browser", True, None),
                "browser",
            ),
            (
                r'{"type":"action","tool":"files.write","arguments":{"path":"unsafe\-name.md","content":"safe"},"summary":"write"}',
                "请把 safe 写入 unsafe-name.md",
                ToolSpec("files.write", "fixture", {"type": "object", "properties": {
                    "path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"], "additionalProperties": False}, "files", True, None),
                "files",
            ),
        )
        for index, (reply, task, template, capability) in enumerate(cases):
            with self.subTest(reply=reply), tempfile.TemporaryDirectory() as temporary:
                calls = []
                spec = ToolSpec(template.name, template.description, template.parameters,
                                template.capability, template.mutating,
                                lambda arguments: calls.append(arguments) or {"ok": True})
                events = []
                result = Runtime(
                    ScriptedClient([reply] * 3),
                    ToolRegistry([spec], allowed=(capability,)),
                    Path(temporary) / f"run-{index}",
                    event=lambda name, fields: events.append((name, fields)),
                ).run(task)
                self.assertEqual(result["error_code"], "invalid_protocol")
                self.assertEqual(result["steps"], 0)
                self.assertEqual(calls, [])
                self.assertFalse(any(name in ("tool.attempt", "tool.started") for name, _ in events))

    def test_uploaded_quote_repair_still_requires_schema_before_dispatch(self):
        calls, events = [], []
        repaired = action("shell.run", {"argv": [sys.executable, "-c", "import playwright; print('playwright installed')"]})
        client = ScriptedClient(BROKEN_COMMANDS + [repaired, final("已检查依赖")])
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "run"
            result = Runtime(client, shell_registry(calls), directory,
                             event=lambda name, fields: events.append((name, fields))).run(
                                 "执行命令检查当前 Agent 的 Playwright 依赖")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(client.messages), 4)
            self.assertTrue(any(name == "model.protocol_repaired" for name, _ in events))
            transcript = json.loads((directory / "transcript.json").read_text())
            assistant_replies = [row["content"] for row in transcript if row["role"] == "assistant"]
            self.assertEqual(len(assistant_replies), 4)
            self.assertEqual(assistant_replies[:2], BROKEN_COMMANDS)

    def test_repaired_json_still_requires_tool_permission_and_arguments_schema(self):
        cases = [([], {"argv": [sys.executable, "-c", "pass"]}, "tool_failed"),
                 (["shell"], {"argv": "not-an-array"}, "invalid_protocol")]
        for allowed, arguments, expected_error in cases:
            with self.subTest(allowed=allowed), tempfile.TemporaryDirectory() as temporary:
                calls = []
                client = ScriptedClient([BROKEN_COMMANDS[0], action("shell.run", arguments), final("未执行")])
                result = Runtime(client, shell_registry(calls, allowed=allowed), Path(temporary) / "run").run(
                    "执行命令检查 Playwright 是否安装")
                # The repaired JSON still enters the normal permission and
                # argument-schema gates before dispatch.
                self.assertEqual(result["error_code"], expected_error)
                self.assertEqual(calls, [])

    def test_ambiguous_reply_is_never_guessed_or_executed_after_repair_budget(self):
        calls = []
        client = ScriptedClient([BROKEN_COMMANDS[0]] * 3)
        with tempfile.TemporaryDirectory() as temporary:
            result = Runtime(client, shell_registry(calls), Path(temporary) / "run").run("检查依赖")
            self.assertEqual(result["error_code"], "invalid_protocol")
            self.assertEqual(result["steps"], 0)
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
