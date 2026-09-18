"""End-to-end regression for the malformed files.write reply in events.jsonl."""

import json
from pathlib import Path
import tempfile
import unittest

from fusion_agent.local_tools import LocalTools
from fusion_agent.registry import ToolRegistry
from fusion_agent.runtime import Runtime
from test_runtime import ScriptedClient, final


FIRST_URL = "https://techcrunch.com/2026/09/09/listen-labs/"
LAST_URL = "https://techcrunch.com/2026/09/09/openai-board/"

# Shortened from the first model.raw_reply in the uploaded events.jsonl. It
# retains all three independent page-conversion defects: an invalid Markdown
# escape inside a JSON path, escaped structural array brackets, and a Markdown
# body whose breaks/link destinations were serialized as literal /n markers.
REPORTED_FILES_WRITE = (
    r'{ "type": "action", "tool": "files.write", "arguments": {'
    r' "path": "ai\_news.md", "content": "# AI 新闻摘要\\n\\n'
    r'1. **Listen Labs funding news**\\n - 链接: [' + FIRST_URL
    + r'\\n\\n2](' + FIRST_URL + r'/n/n2). **OpenAI board news**'
    r'\\n - 链接: [' + LAST_URL + r'\\n](' + LAST_URL + r'/n)",'
    r' "overwrite": true }, "summary": "保存 AI 新闻 Markdown 报告",'
    r' "plan": \[ "保存新闻报告", "输出最终回答" \] }')

EXPECTED_MARKDOWN = (
    "# AI 新闻摘要\n\n"
    "1. **Listen Labs funding news**\n"
    f" - 链接: [{FIRST_URL}]({FIRST_URL})\n\n"
    "2. **OpenAI board news**\n"
    f" - 链接: [{LAST_URL}]({LAST_URL})")


class UploadedEventsMarkdownWriteTests(unittest.TestCase):
    def test_normalized_action_executes_real_verified_markdown_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            run_dir = root / "run"
            events = []
            event = lambda name, fields: events.append((name, fields))
            local = LocalTools(workspace, root / "runtime")
            self.addCleanup(local.close)
            registry = ToolRegistry(local.specs(), allowed=("files",), event=event)
            client = ScriptedClient([
                REPORTED_FILES_WRITE,
                final("AI 新闻已保存到 ai_news.md，并已完成写后核验。"),
            ])

            result = Runtime(client, registry, run_dir, event=event).run(
                "请把以下内容写入文件 ai_news.md")

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["steps"], 1)
            destination = workspace / "ai_news.md"
            self.assertTrue(destination.is_file())
            self.assertEqual(destination.read_text(encoding="utf-8"), EXPECTED_MARKDOWN)
            self.assertNotIn(r"\n", destination.read_text(encoding="utf-8"))
            self.assertNotIn("/n/n2", destination.read_text(encoding="utf-8"))

            normalized = next(fields for name, fields in events
                              if name == "model.protocol_normalized")
            self.assertEqual(normalized["payload"]["raw_reply"], REPORTED_FILES_WRITE)
            kinds = [item["kind"] for item in normalized["payload"]["normalizations"]]
            self.assertEqual(kinds, [
                "markdown_array_delimiters",
                "markdown_json_string_escapes",
                "escaped_markdown_line_breaks",
                "polluted_full_url_link",
                "polluted_full_url_link",
            ])
            action = normalized["payload"]["action"]
            self.assertEqual(action["arguments"]["path"], "ai_news.md")
            self.assertEqual(action["arguments"]["content"], EXPECTED_MARKDOWN)
            self.assertEqual(action["plan"], ["保存新闻报告", "输出最终回答"])

            attempt = next(fields for name, fields in events if name == "tool.attempt")
            self.assertEqual(attempt["tool"], "files.write")
            self.assertEqual(attempt["payload"]["arguments"]["content"], EXPECTED_MARKDOWN)
            completed = next(fields for name, fields in events
                             if name == "tool.completed" and fields["tool"] == "files.write")
            self.assertTrue(completed["ok"])
            self.assertEqual(completed["verification_status"], "verified")
            self.assertNotIn("model.invalid_protocol", [name for name, _ in events])
            self.assertNotIn("model.repair_requested", [name for name, _ in events])

            transcript = json.loads((run_dir / "transcript.json").read_text(encoding="utf-8"))
            self.assertIn(REPORTED_FILES_WRITE, [
                message["content"] for message in transcript if message["role"] == "assistant"
            ])
            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["executed_mutations"][0]["tool"], "files.write")
            self.assertEqual(state["executed_mutations"][0]["outcome"], "success")


if __name__ == "__main__":
    unittest.main()
