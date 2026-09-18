"""Local regressions for agent2's workspace mismatch and partial JSON replies.

The fixture paths are temporary and no commands from the attachment are run.
"""

import json
from pathlib import Path
import tempfile
import unittest

from fusion_agent.local_tools import LocalTools
from fusion_agent.registry import ToolRegistry
from fusion_agent.runtime import ProtocolError, Runtime, parse_reply
from test_runtime import ScriptedClient, action, final


PARTIAL_ACTIONS = [
    '{"type":"action","',
    '{"type":"action","tool":"shell.run","arguments',
    '{"type":"action","tool":"shell',
]


class WorkspaceRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.target = self.root / "requested-target"
        self.target.mkdir()
        (self.target / "outside.txt").write_text("outside content", encoding="utf-8")
        self.local = LocalTools(self.workspace, self.root / "runtime")
        self.addCleanup(self.local.close)
        self.registry = ToolRegistry(self.local.specs(), allowed=["files"])
        self.run_dir = self.root / "run"

    def state(self):
        return json.loads((self.run_dir / "state.json").read_text())

    def test_path_rejection_exposes_exact_target_and_user_recovery_without_reading(self):
        result = self.registry.invoke("files.read", {"path": str(self.target / "outside.txt")})
        self.assertFalse(result["ok"])
        self.assertEqual(result["execution"], {"status": "not_started"})
        self.assertNotIn("outcome_unknown", result)
        self.assertEqual(result["error"]["details"], {
            "requested_path": str(self.target / "outside.txt"),
            "workspace": str(self.workspace),
            "recovery": "user_select_workspace",
            "access_performed": False,
        })
        self.assertNotIn("outside content", json.dumps(result))

    def test_listing_reports_directory_size_as_metadata_not_recursive_capacity(self):
        nested = self.workspace / "nested"
        nested.mkdir()
        (nested / "large.bin").write_bytes(b"x" * 12345)
        result = self.registry.invoke("files.list", {"path": ".", "recursive": True})
        directory = next(item for item in result["entries"] if item["path"] == "nested")
        self.assertEqual(directory["bytes"], nested.stat().st_size)
        self.assertEqual(result["size_semantics"], {
            "bytes": "lstat_st_size", "directory_total_measured": False, "file_contents_read": False,
        })
        self.assertIn("未统计", result["notice"])
        self.assertIn("推测", result["notice"])

    def test_three_uploaded_listing_variants_do_not_replace_original_target(self):
        for index, options in enumerate(({}, {"recursive": False}, {"recursive": False, "limit": 500})):
            with self.subTest(options=options):
                run_dir = self.root / f"run-{index}"
                client = ScriptedClient([
                    action("files.list", {"path": str(self.target), **options}),
                    action("files.list", {"path": ".", **options}),
                    final("空目录检查成功，全部完成"),
                ])
                events = []
                result = Runtime(client, self.registry, run_dir,
                                 event=lambda name, fields: events.append((name, fields))).run("列出目标目录")
                state = json.loads((run_dir / "state.json").read_text())
                self.assertEqual(result["error_code"], "workspace_required")
                self.assertEqual(result["status"], "failed")
                self.assertEqual(len(client.messages), 3)  # No futile extra model request.
                self.assertEqual(len(state["unresolved_failures"]), 1)
                self.assertEqual(state["unresolved_failures"][0]["path_context"]["requested_path"], str(self.target))
                self.assertEqual(state["pending_verifications"], {})
                self.assertEqual(state["uncertain_actions"], [])
                self.assertIn(str(self.target), result["answer"])
                self.assertIn(str(self.workspace), result["answer"])
                self.assertIn("/workspace", result["answer"])
                self.assertIn("/retry", result["answer"])
                self.assertNotIn("全部完成", result["answer"])
                self.assertFalse((run_dir / "final.md").exists())
                self.assertEqual(self.local.workspace, self.workspace)
                self.assertNotIn("resolved_by", state["failed_actions"][0])
                self.assertTrue(any(name == "workspace.required" for name, _ in events))

    def test_rejected_write_is_not_softened_into_readonly_workspace_recovery(self):
        target_file = self.target / "outside.txt"
        client = ScriptedClient([action("files.write", {"path": str(target_file), "content": "changed", "overwrite": True}), final()])
        result = Runtime(client, self.registry, self.run_dir).run(
            f"写入目标文件 {target_file}")
        self.assertEqual(result["error_code"], "tool_failed")
        self.assertEqual(target_file.read_text(), "outside content")
        self.assertTrue(self.state()["unresolved_failures"][0]["mutating"])

    def test_unrelated_failures_remain_visible_and_are_not_reclassified(self):
        client = ScriptedClient([
            action("files.read", {"path": "missing.txt"}),
            action("files.list", {"path": str(self.target)}),
            final("已完成"),
        ])
        result = Runtime(client, self.registry, self.run_dir).run("读取工作区文件及原目标")
        self.assertEqual(result["error_code"], "tool_failed")
        self.assertEqual(len(self.state()["unresolved_failures"]), 2)

    def test_user_selected_workspace_starts_new_run_and_reads_original_target(self):
        first = Runtime(ScriptedClient([action("files.list", {"path": str(self.target)}), final()]),
                        self.registry, self.run_dir).run("列出目标目录")
        self.assertEqual(first["error_code"], "workspace_required")
        selected = LocalTools(self.target, self.root / "runtime")
        self.addCleanup(selected.close)
        new_run = self.root / "selected-run"
        result = Runtime(ScriptedClient([action("files.list", {"path": "."}), final("已列出 outside.txt")]),
                         ToolRegistry(selected.specs(), allowed=["files"]), new_run).run("列出目标目录")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.state()["error_code"], "workspace_required")
        self.assertEqual(len(self.state()["unresolved_failures"]), 1)
        self.assertEqual(json.loads((new_run / "state.json").read_text())["unresolved_failures"], [])


class PartialReplyRecoveryTests(unittest.TestCase):
    def test_final_markdown_is_normalized_before_saving_with_raw_reply_preserved(self):
        raw_answer = r"# 文件说明\n\n| 文件 | 大小 |\n| --- | --- |\n| a.txt | 12 B |\n\n- 目录总大小未统计"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local = LocalTools(root / "workspace", root)
            self.addCleanup(local.close)
            events = []
            result = Runtime(ScriptedClient([final(raw_answer)]), ToolRegistry(local.specs()), root / "run",
                             event=lambda name, fields: events.append((name, fields))).run("整理已有文本")
            self.assertIn("# 文件说明\n\n| 文件", result["answer"])
            self.assertEqual((root / "run/final.md").read_text(), result["answer"] + "\n")
            transcript = json.loads((root / "run/transcript.json").read_text())
            self.assertEqual(json.loads(transcript[-1]["content"])["answer"], raw_answer)
            normalized = next(fields for name, fields in events if name == "result.normalized")
            self.assertEqual(normalized["payload"]["raw_answer"], raw_answer)
            self.assertEqual(normalized["payload"]["answer"], result["answer"])

    def test_exact_partial_responses_and_eof_are_identified_without_completion(self):
        for reply in PARTIAL_ACTIONS + ['{"type":"action",', '{"type":', '{"type":"final","answer":"ok"']:
            with self.subTest(reply=reply), self.assertRaises(ProtocolError) as caught:
                parse_reply(reply)
            self.assertTrue(caught.exception.details["incomplete_response"])
            self.assertEqual(caught.exception.details["recovery"], "inspect_complete_server_response")

    def test_nested_quote_error_is_not_misclassified_as_truncated(self):
        reply = '{"type":"action","tool":"shell.run","arguments":{"command":"python -c "print(1)""},"summary":"run"}'
        changes = []
        parsed = parse_reply(reply, changes)
        self.assertEqual(parsed["arguments"]["command"], 'python -c "print(1)"')
        self.assertEqual(changes[0]["kind"], "unescaped_shell_command_quotes")

    def test_partial_reply_stops_after_one_request_without_server_repair(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            local = LocalTools(directory / "workspace", directory)
            self.addCleanup(local.close)
            calls = []
            registry = ToolRegistry(local.specs(), allowed=["files"], event=lambda *args: calls.append(args))
            client = ScriptedClient(PARTIAL_ACTIONS)
            result = Runtime(client, registry, directory / "run").run("列出目标目录")
            self.assertEqual(result["error_code"], "incomplete_response")
            self.assertEqual(result["steps"], 0)
            self.assertEqual(len(client.messages), 1)
            self.assertEqual(calls, [])
            self.assertIn("不会再请求网页模型改写 JSON", result["answer"])
            self.assertFalse((directory / "run/final.md").exists())

    def test_partial_reply_does_not_request_or_execute_a_server_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local = LocalTools(root / "workspace", root)
            self.addCleanup(local.close)
            observed = []
            registry = ToolRegistry(local.specs(), allowed=["files"],
                                    event=lambda name, fields: observed.append((name, fields)))
            client = ScriptedClient([PARTIAL_ACTIONS[0], action("files.list", {"path": "."}), final("目录为空")])
            result = Runtime(client, registry, root / "run").run("列出工作区目录")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["steps"], 0)
            self.assertEqual(len(client.messages), 1)
            self.assertEqual(sum(name == "tool.started" for name, _ in observed), 0)
            transcript = json.loads((root / "run/transcript.json").read_text())
            self.assertEqual(transcript[2]["content"], PARTIAL_ACTIONS[0])


if __name__ == "__main__":
    unittest.main()
