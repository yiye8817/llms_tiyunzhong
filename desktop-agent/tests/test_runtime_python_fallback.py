"""Runtime-level safety regressions for controlled Python file fallbacks."""

import json
from pathlib import Path
import tempfile
import unittest

from fusion_agent.local_tools import LocalTools
from fusion_agent.python_fallback import PythonFileFallbacks
from fusion_agent.registry import ToolRegistry
from fusion_agent.runtime import Runtime


def action(tool, arguments):
    return json.dumps({
        "type": "action",
        "tool": tool,
        "arguments": arguments,
        "summary": "执行用户明确请求的文件操作",
    }, ensure_ascii=False)


def final(answer="已完成并核验"):
    return json.dumps({"type": "final", "answer": answer}, ensure_ascii=False)


class ScriptedClient:
    model = "qwen"

    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def complete(self, messages):
        self.requests.append(json.loads(json.dumps(messages)))
        return next(self.replies)


class RuntimePythonFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.workspace = self.base / "workspace"
        self.local = LocalTools(self.workspace, self.base / "runtime")
        self.fallbacks = PythonFileFallbacks(self.local)

    def tearDown(self):
        self.local.close()
        self.temporary.cleanup()

    def registry(self, *, allowed=("files",), authorize=None, events=None):
        events = events if events is not None else []
        return ToolRegistry(
            [],
            allowed=allowed,
            authorize=authorize,
            fallbacks=self.fallbacks,
            event=lambda name, fields: events.append((name, fields)),
        )

    def test_runtime_materializes_missing_canonical_and_alias_then_really_writes(self):
        cases = (
            ("files.write", "canonical.md", "canonical"),
            ("filesystem.write_file", "alias.md", "alias"),
        )
        for index, (tool, path, content) in enumerate(cases):
            with self.subTest(tool=tool):
                events = []
                registry = self.registry(events=events)
                client = ScriptedClient([
                    action(tool, {"path": path, "content": content}),
                    final(),
                ])
                result = Runtime(
                    client,
                    registry,
                    self.base / f"run-{index}",
                    event=lambda name, fields: events.append((name, fields)),
                ).run(f"请将 {content} 写入文件 {path}")

                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["steps"], 1)
                self.assertEqual((self.workspace / path).read_text(), content)
                self.assertIn("files.write", {item["name"] for item in registry.catalog()})
                resolution = next(
                    fields for name, fields in events
                    if name == "tool.python_fallback_resolved"
                    and fields.get("payload", {}).get("executed") is False
                )
                self.assertEqual(resolution["original_tool"], tool)
                self.assertEqual(resolution["resolved_tool"], "files.write")
                self.assertEqual(
                    resolution["payload"]["resolution"]["implementation"],
                    "python_local_tools",
                )
                completed = next(
                    fields for name, fields in events
                    if name == "tool.completed" and fields.get("tool") == "files.write"
                )
                self.assertEqual(completed["verification_status"], "verified")

    def test_arbitrary_unknown_tool_remains_unavailable_and_never_executes(self):
        marker = self.base / "must-not-exist"
        requested = action("python.run", {
            "code": f'__import__("pathlib").Path({str(marker)!r}).write_text("bad")'
        })
        events = []
        registry = self.registry(events=events)
        result = Runtime(
            ScriptedClient([requested, requested, requested]),
            registry,
            self.base / "run-unknown",
            event=lambda name, fields: events.append((name, fields)),
        ).run("请执行文件操作，将结果保存到 marker 文件")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "invalid_protocol")
        self.assertEqual(result["steps"], 0)
        self.assertFalse(marker.exists())
        self.assertNotIn("python.run", {item["name"] for item in registry.catalog()})
        self.assertFalse(any(name in ("tool.started", "tool.completed") for name, _ in events))

    def test_fixed_python_tool_can_run_without_a_task_action_keyword(self):
        requested=action('write_file',{'path':'created.md','content':'actual'})
        registry=self.registry()
        runtime=Runtime(ScriptedClient([requested,final('已写入')]),registry,self.base/'run-uniform')
        result=runtime.run('处理这个文档')
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['steps'],1)
        self.assertEqual((self.workspace/'created.md').read_text(),'actual')

    def test_fallback_cannot_bypass_capability_authorization(self):
        events = []
        registry = self.registry(allowed=(), authorize=lambda *_: False, events=events)
        result = Runtime(
            ScriptedClient([
                action("write_file", {"path": "denied.md", "content": "bad"}),
                final("已写入"),
            ]),
            registry,
            self.base / "run-denied",
            event=lambda name, fields: events.append((name, fields)),
        ).run("请将 bad 写入文件 denied.md")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "tool_failed")
        self.assertEqual(result["steps"], 1)
        self.assertFalse((self.workspace / "denied.md").exists())
        failed = next(fields for name, fields in events
                      if name == "tool.failed" and fields.get("code") == "capability_denied")
        self.assertEqual(failed["execution"], {"status": "not_started"})

    def test_fallback_cannot_escape_workspace_or_overwrite_without_flag(self):
        outside = self.base / "escape.md"
        events = []
        traversal_registry = self.registry(events=events)
        traversal = Runtime(
            ScriptedClient([
                action("write_file", {"path": "../escape.md", "content": "bad"}),
                final("已写入"),
            ]),
            traversal_registry,
            self.base / "run-traversal",
            event=lambda name, fields: events.append((name, fields)),
        ).run("请将 bad 写入文件 ../escape.md")
        self.assertEqual(traversal["error_code"], "tool_failed")
        self.assertFalse(outside.exists())

        existing = self.workspace / "existing.md"
        existing.write_text("keep")
        overwrite_registry = self.registry()
        overwrite = Runtime(
            ScriptedClient([
                action("files.write", {"path": "existing.md", "content": "replace"}),
                final("已替换"),
            ]),
            overwrite_registry,
            self.base / "run-overwrite",
        ).run("请将 replace 写入已有文件 existing.md")
        self.assertEqual(overwrite["error_code"], "tool_failed")
        self.assertEqual(existing.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
