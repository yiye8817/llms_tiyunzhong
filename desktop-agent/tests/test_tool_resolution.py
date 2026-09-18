import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fusion_agent.local_tools import LocalTools
from fusion_agent.registry import ToolRegistry
from fusion_agent.runtime import Runtime
from fusion_agent.tool_resolution import alternatives, canonical_tool, simple_read_target


def action(name, arguments):
    return json.dumps({"type": "action", "tool": name, "arguments": arguments, "summary": "读取原文件"})


class Client:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.messages = []

    def complete(self, messages):
        self.messages.append(json.loads(json.dumps(messages)))
        return next(self.replies)


FINAL = '{"type":"final","answer":"已读取文件"}'


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspace"
        self.local = LocalTools(self.workspace, self.base / "runtime")
        self.events = []
        self.registry = ToolRegistry(self.local.specs(), allowed=("files", "shell"),
                                     event=lambda name, fields: self.events.append((name, fields)))
        (self.workspace / "report.txt").write_text("真实文件内容\n", encoding="utf-8")

    def tearDown(self):
        self.local.close()
        self.temp.cleanup()

    def run_script(self, *replies, task="读取 report.txt 文件"):
        self.client = Client(*replies)
        runtime = Runtime(self.client, self.registry, self.base / "run",
                          event=lambda name, fields: self.events.append((name, fields)))
        result = runtime.run(task)
        self.state = json.loads((self.base / "run" / "state.json").read_text())
        return result

    def test_exact_alias_reads_actual_file_without_shell(self):
        self.registry.allowed = {"files"}
        with patch("fusion_agent.local_tools.subprocess.Popen") as popen:
            result = self.run_script(action("file.read", {"path": "report.txt"}), FINAL)
        self.assertEqual(result["status"], "completed")
        popen.assert_not_called()
        observed = json.loads(self.client.messages[1][-1]["content"])
        self.assertEqual(observed["tool"], "files.read")
        self.assertEqual(observed["observation"]["content"], "真实文件内容\n")
        alias = [data for name, data in self.events if name == "tool.alias_resolved"]
        self.assertEqual(len(alias), 1)
        self.assertEqual(alias[0]["payload"]["arguments"], {"path": "report.txt"})
        self.assertEqual(self.state["failed_actions"], [])

    def test_alias_preserves_schema_and_workspace_boundary(self):
        (self.base / "private.txt").write_text("outside")
        for arguments, code in (({"path": "../private.txt"}, "path_outside_workspace"),
                                ({"filename": "report.txt"}, "invalid_arguments")):
            with self.subTest(arguments=arguments):
                result = self.registry.invoke("file.read", arguments)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], code)
                self.assertEqual(result["execution"]["status"], "not_started")

    def test_alias_preserves_denied_capability(self):
        self.registry.allowed = set()
        result = self.registry.invoke("file.write", {"path": "new.txt", "content": "must not write"})
        self.assertEqual(result["error"]["code"], "capability_denied")
        self.assertFalse((self.workspace / "new.txt").exists())

    def test_alias_schema_error_is_repaired_before_any_dispatch(self):
        result = self.run_script(action("file.read", {"filename": "report.txt"}),
                                 action("files.read", {"path": "report.txt"}), FINAL)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["steps"], 1)
        self.assertEqual(self.state["failed_actions"], [])
        self.assertEqual(self.state["unresolved_failures"], [])
        attempts = [fields for name, fields in self.events if name == "tool.attempt"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["payload"]["arguments"], {"path": "report.txt"})
        repair = json.loads(self.client.messages[1][-1]["content"])
        diagnostic = repair["error_details"]["argument_validation"]
        self.assertEqual(diagnostic["original_tool"], "file.read")
        self.assertEqual(diagnostic["original_arguments"], {"filename": "report.txt"})
        self.assertFalse(diagnostic["executed"])
        observation = json.loads(self.client.messages[2][-1]["content"])["observation"]
        self.assertEqual(observation["content"], "真实文件内容\n")

    def test_unknown_schema_field_never_dispatches_before_correction(self):
        invalid = action("files.write", {"path": "new.txt", "content": "unexpected", "append": True})
        result = self.run_script(invalid, invalid, FINAL)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "invalid_protocol")
        self.assertEqual(result["steps"], 0)
        self.assertFalse((self.workspace / "new.txt").exists())
        self.assertFalse(any(name in ("tool.attempt", "tool.started") for name, _ in self.events))
        self.assertEqual(self.state["failed_actions"], [])

    def test_valid_schema_with_outside_target_retains_real_failure(self):
        (self.base / "private.txt").write_text("outside")
        result = self.run_script(action("file.read", {"path": "../private.txt"}),
                                 action("files.read", {"path": "report.txt"}), FINAL,
                                 task="读取 ../private.txt 和 report.txt 文件")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "workspace_required")
        self.assertEqual(result["steps"], 2)
        self.assertEqual(len(self.state["unresolved_failures"]), 1)
        self.assertEqual(self.state["unresolved_failures"][0]["error"]["code"], "path_outside_workspace")

    def test_real_file_failure_is_not_discarded_by_unrelated_schema_repair(self):
        result = self.run_script(action("files.read", {"path": "missing.txt"}),
                                 action("file.read", {"filename": "report.txt"}),
                                 action("files.read", {"path": "report.txt"}), FINAL,
                                 task="读取 missing.txt 和 report.txt 文件")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "tool_failed")
        self.assertEqual(result["steps"], 2)
        self.assertEqual(len(self.state["unresolved_failures"]), 1)
        self.assertNotEqual(self.state["unresolved_failures"][0]["error"]["code"], "invalid_arguments")

    def test_unknown_tool_candidate_requires_new_model_action(self):
        with patch("fusion_agent.tool_resolution.shutil.which", side_effect=lambda name: "/usr/bin/" + name if name == "cat" else None):
            result = self.run_script(action("file.fetch", {"path": "report.txt"}),
                                     action("files.read", {"path": "report.txt"}), FINAL)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["steps"], 1)
        repair = json.loads(self.client.messages[1][-1]["content"])
        self.assertEqual(repair["type"], "tool_resolution")
        self.assertEqual(repair["alternatives"]["original_arguments"], {"path": "report.txt"})
        self.assertEqual(repair["alternatives"]["installed_cli_candidates"], [{"name": "cat", "path": "/usr/bin/cat"}])
        self.assertEqual(self.state["unresolved_failures"], [])

    def test_ambiguous_unknown_tool_never_executes_or_becomes_false_final(self):
        with patch("fusion_agent.local_tools.subprocess.Popen") as popen:
            result = self.run_script(action("file.rewrite", {"path": "report.txt"}), FINAL, FINAL)
        popen.assert_not_called()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["steps"], 0)
        self.assertEqual((self.workspace / "report.txt").read_text(), "真实文件内容\n")

    def test_model_cli_candidate_still_requires_shell_authorization(self):
        self.registry.allowed = {"files"}
        with patch("fusion_agent.local_tools.subprocess.Popen") as popen:
            result = self.run_script(action("file.fetch", {"path": "report.txt"}),
                                     action("shell.run", {"argv": ["cat", "report.txt"]}), FINAL,
                                     task="执行 shell 命令读取 report.txt 文件")
        popen.assert_not_called()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.state["unresolved_failures"][0]["error"]["code"], "capability_denied")

    def test_missing_read_cli_recovers_by_complete_same_file_read(self):
        # file.read is not an OS executable; the second step is a real bounded
        # files.read, rather than a fabricated shell result or a process mock.
        with patch.dict(os.environ, {"PATH": str(self.base / "no-programs")}):
            result = self.run_script(action("shell.run", {"argv": ["file.read", "report.txt"]}),
                                     action("files.read", {"path": "report.txt"}),
                                     action("shell.run", {"argv": [sys.executable, "-c",
                                             "from pathlib import Path; Path('report.txt').read_text()"]}),
                                     FINAL, task="执行 shell 命令并用文件工具读取 report.txt 文件")
        self.assertEqual(result["status"], "completed")
        failure = self.state["failed_actions"][0]
        self.assertEqual(failure["execution_status"], "not_started")
        self.assertEqual(failure["resolution_method"], "equivalent_complete_file_read")
        self.assertEqual(self.state["uncertain_actions"], [])
        self.assertEqual(self.state["pending_verifications"], {})
        self.assertEqual(self.state["unresolved_failures"], [])

    def test_other_file_cannot_resolve_missing_read_command(self):
        (self.workspace / "other.txt").write_text("other")
        with patch.dict(os.environ, {"PATH": str(self.base / "no-programs")}):
            result = self.run_script(action("shell.run", {"argv": ["cat", "report.txt"]}),
                                     action("files.read", {"path": "other.txt"}), FINAL,
                                     task="执行 cat 命令读取 report.txt，然后读取 other.txt 文件")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(self.state["unresolved_failures"]), 1)

    def test_partial_read_cannot_resolve_missing_read_command(self):
        with patch.dict(os.environ, {"PATH": str(self.base / "no-programs")}):
            result = self.run_script(action("shell.run", {"argv": ["cat", "report.txt"]}),
                                     action("files.read", {"path": "report.txt", "max_bytes": 1}), FINAL,
                                     task="执行 cat 命令读取 report.txt，失败后用文件工具仅读取一个字节")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(self.state["unresolved_failures"]), 1)

    def test_arbitrary_success_cannot_clear_missing_command_failure(self):
        result = self.run_script(action("shell.run", {"argv": [str(self.base / "missing")]}),
                                 action("shell.run", {"argv": [sys.executable, "-c", "print('ok')"]}), FINAL,
                                 task="执行两个 shell 命令：先运行缺失命令，再运行 Python 打印 ok")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.state["unresolved_failures"][0]["error"]["code"], "command_not_found")

    def test_started_shell_exit_127_is_not_not_started_and_is_not_replayed(self):
        result = self.run_script(
            action("shell.run", {"command": "printf ran >> proof.txt; missing_fusion_command_932"}), FINAL,
            task="执行一个会先产生测试痕迹再失败的 shell 命令")
        self.assertEqual(result["status"], "failed")
        self.assertEqual((self.workspace / "proof.txt").read_text(), "ran")
        self.assertFalse(any(name == "tool.alternatives_found" for name, _ in self.events))
        self.assertNotEqual(self.state["failed_actions"][0]["execution_status"], "not_started")

    def test_timeout_is_not_replayed_or_dispatched_as_substitution(self):
        result = self.run_script(
            action("shell.run", {"argv": [sys.executable, "-c", "from pathlib import Path; import time; Path('proof.txt').write_text('once'); time.sleep(5)"], "timeout": 1}),
            FINAL, task="执行一个会产生测试痕迹并触发超时的 shell 命令")
        self.assertEqual(result["status"], "failed")
        self.assertEqual((self.workspace / "proof.txt").read_text(), "once")
        self.assertFalse(any(name == "tool.alternatives_found" for name, _ in self.events))
        self.assertEqual(self.state["steps"], 1)

    def test_missing_cwd_is_not_claimed_to_be_missing_program(self):
        result = self.registry.invoke("shell.run", {"argv": [sys.executable, "-c", "print('no')"], "cwd": "missing-dir"})
        self.assertEqual(result["error"]["code"], "command_start_failed")
        self.assertEqual(result["execution"]["status"], "not_started")

    def test_missing_shebang_interpreter_is_not_missing_program(self):
        script = self.workspace / "runner"
        script.write_text("#!/missing/fusion-interpreter\n")
        script.chmod(0o700)
        result = self.registry.invoke("shell.run", {"argv": [str(script)]})
        self.assertEqual(result["error"]["code"], "command_start_failed")

    def test_read_equivalence_rejects_shell_options_and_outside_workspace(self):
        for arguments in ({"argv": ["cat", "-n", "report.txt"]},
                          {"argv": ["cat", "../outside"]},
                          {"argv": ["cat", "-"]},
                          {"command": "cat report.txt"},
                          {"argv": ["mystery", "report.txt"]}):
            self.assertIsNone(simple_read_target(arguments, self.workspace))
        self.assertEqual(simple_read_target({"argv": ["cat", "--", "report.txt"]}, self.workspace), "report.txt")

    def test_real_catalog_tool_wins_over_alias_and_absent_target_not_invented(self):
        self.assertEqual(canonical_tool("file.read", {"file.read", "files.read"}), "file.read")
        self.assertIsNone(canonical_tool("file.read", {"files.list"}))


if __name__ == "__main__":
    unittest.main()
