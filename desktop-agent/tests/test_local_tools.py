import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fusion_agent.contracts import ToolError
from fusion_agent.local_tools import LocalTools
from fusion_agent.registry import ToolRegistry


class LocalToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspace"
        self.tools = LocalTools(self.workspace, self.base / "runtime", max_output_chars=1024)

    def tearDown(self):
        self.tools.close()
        self.temp.cleanup()

    def run_python(self, script, **kwargs):
        return self.tools.shell_run({"argv": [sys.executable, "-c", script], **kwargs})

    def assert_tool_error(self, code, function, args):
        with self.assertRaises(ToolError) as caught:
            function(args)
        self.assertEqual(caught.exception.code, code)

    @staticmethod
    def process_is_running(pid):
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            return False
        return state not in ("Z", "X")

    def test_specs_distinguish_mutations_and_shell_is_explicit(self):
        specs = {spec.name: spec for spec in self.tools.specs()}
        self.assertEqual(set(specs), {"shell.run", "files.list", "files.read", "files.stat",
                                      "files.search", "files.write", "files.mkdir", "files.copy",
                                      "files.move", "files.delete"})
        self.assertTrue(specs["shell.run"].mutating)
        self.assertEqual(specs["shell.run"].capability, "shell")
        self.assertTrue(specs["files.write"].mutating)
        self.assertFalse(specs["files.read"].mutating)
        self.assertFalse(specs["files.search"].mutating)
        self.assertTrue(all(specs[name].mutating for name in
                            ("files.mkdir", "files.copy", "files.move", "files.delete")))
        self.assertIn("NOT", specs["shell.run"].description)

    def test_common_file_operations_and_content_search(self):
        self.assertTrue(self.tools.files_mkdir({"path": "notes"})["ok"])
        self.tools.files_write({"path": "notes/a.txt", "content": "Alpha needle\n第二行"})
        info = self.tools.files_stat({"path": "notes/a.txt"})
        self.assertEqual(info["type"], "file")
        found = self.tools.files_search({"query": "needle"})
        self.assertTrue(found["ok"])
        self.assertEqual(found["backend"], "rg" if shutil.which("rg") else "grep")
        self.assertEqual(found["matches"][0]["line"], 1)
        self.assertTrue(self.tools.files_copy({"source": "notes/a.txt", "destination": "notes/b.txt"})["ok"])
        self.assertTrue(self.tools.files_move({"source": "notes/b.txt", "destination": "moved.txt"})["ok"])
        self.assertFalse((self.workspace / "notes/b.txt").exists())
        self.assertTrue(self.tools.files_delete({"path": "moved.txt"})["ok"])
        self.assertTrue(self.tools.files_delete({"path": "notes", "recursive": True})["ok"])

    def test_search_falls_back_to_grep_when_rg_is_unavailable(self):
        self.tools.files_write({"path": "a.txt", "content": "fallback token"})
        original = self.tools.shell_run
        def unavailable(args):
            if args.get("argv", [None])[0] == "rg":
                raise ToolError("command_not_found", "missing", not_executed=True)
            return original(args)
        with patch.object(self.tools, "shell_run", side_effect=unavailable):
            result = self.tools.files_search({"query": "fallback"})
        self.assertEqual(result["backend"], "grep")
        self.assertEqual(result["attempts"][0]["status"], "unavailable")

    def test_recursive_delete_unlinks_symlink_without_following_it(self):
        outside = self.base / "outside"
        outside.mkdir()
        protected = outside / "keep.txt"
        protected.write_text("keep")
        (self.workspace / "tree").mkdir()
        (self.workspace / "tree" / "link").symlink_to(outside, target_is_directory=True)
        result = self.tools.files_delete({"path": "tree", "recursive": True})
        self.assertTrue(result["ok"])
        self.assertEqual(protected.read_text(), "keep")

    def test_argv_has_no_shell_expansion(self):
        literal = "$(touch SHOULD_NOT_EXIST); $HOME * `id`"
        result = self.tools.shell_run({"argv": [sys.executable, "-c", "import sys; print(sys.argv[1])", literal]})
        self.assertEqual(result["stdout"].strip(), literal)
        self.assertFalse((self.workspace / "SHOULD_NOT_EXIST").exists())
        self.assertEqual(result["returncode"], 0)
        self.assertFalse(result["timed_out"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["verification"], {"status": "verified", "method": "process_exit", "scope": "shell"})

    def test_command_uses_bash_with_workspace_cwd(self):
        (self.workspace / "child").mkdir()
        result = self.tools.shell_run({"command": "v=hello; printf '%s' \"$v\"; pwd >&2", "cwd": "child"})
        self.assertEqual(result["stdout"], "hello")
        self.assertEqual(result["stderr"].strip(), str(self.workspace / "child"))
        self.assertEqual(result["cwd"], "child")

    def test_missing_executable_is_known_not_started(self):
        registry = ToolRegistry(self.tools.specs(), allowed=("shell",))
        result = registry.invoke("shell.run", {"argv": [str(self.base / "missing-executable")]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "command_not_found")
        self.assertEqual(result["execution"], {"status": "not_started"})
        self.assertNotIn("outcome_unknown", result)
        self.assertEqual(result["error"]["details"], {"stage": "process_start", "errno": 2,
                                                   "executable": str(self.base / "missing-executable")})
        self.assertEqual(self.tools._processes, set())

    def test_shell_validation_never_starts_a_process(self):
        registry = ToolRegistry(self.tools.specs(), allowed=("shell",))
        with patch("fusion_agent.local_tools.subprocess.Popen") as popen:
            for args in ({"argv": ["echo"], "command": "echo test"},
                         {"argv": ["echo"], "cwd": "../outside"}):
                result = registry.invoke("shell.run", args)
                self.assertFalse(result["ok"])
                self.assertEqual(result["execution"], {"status": "not_started"})
                self.assertNotIn("outcome_unknown", result)
        popen.assert_not_called()

    def test_started_nonzero_command_is_not_classified_as_preflight(self):
        registry = ToolRegistry(self.tools.specs(), allowed=("shell",))
        result = registry.invoke("shell.run", {"argv": [sys.executable, "-c", "from pathlib import Path; Path('started.txt').write_text('started'); raise SystemExit(7)"]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["returncode"], 7)
        self.assertNotEqual(result.get("execution", {}).get("status"), "not_started")
        self.assertEqual((self.workspace / "started.txt").read_text(), "started")

    def test_shell_environment_omits_ambient_credentials(self):
        secret_vars = {"OPENAI_API_KEY": "test-secret", "FUSION_TOKEN": "token", "AWS_SECRET_ACCESS_KEY": "aws", "SSH_AUTH_SOCK": "/test/socket", "BASH_ENV": "/not-sourced"}
        with patch.dict(os.environ, secret_vars):
            result = self.run_python("import json,os; print(json.dumps({k:os.getenv(k) for k in " + repr(list(secret_vars)) + "}))")
        self.assertEqual(json.loads(result["stdout"]), dict.fromkeys(secret_vars))

    def test_large_output_is_drained_without_deadlock_and_bounded(self):
        result = self.run_python("import os\nfor i in range(64):\n os.write(1, b'x'*65536)\n os.write(2, b'y'*65536)")
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["stdout"], "x" * 1024)
        self.assertEqual(result["stderr"], "y" * 1024)
        self.assertEqual(result["stdout_bytes"], 64 * 65536)
        self.assertEqual(result["stderr_bytes"], 64 * 65536)
        self.assertTrue(result["stdout_truncated"])
        self.assertTrue(result["stderr_truncated"])

    def test_nonzero_status_and_utf8_are_returned(self):
        result = self.run_python("import sys; print('中文输出'); print('错误', file=sys.stderr); sys.exit(7)")
        self.assertEqual(result["returncode"], 7)
        self.assertEqual(result["stdout"], "中文输出\n")
        self.assertEqual(result["stderr"], "错误\n")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "command_failed")
        self.assertEqual(result["verification"], {"status": "failed", "method": "process_exit", "scope": "shell"})

    def test_nonzero_command_keeps_bounded_output(self):
        result = self.run_python("import sys; print('x' * 2048); print('y' * 2048, file=sys.stderr); sys.exit(3)")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "command_failed")
        self.assertEqual(result["returncode"], 3)
        self.assertEqual(result["stdout"], "x" * 1024)
        self.assertEqual(result["stderr"], "y" * 1024)
        self.assertTrue(result["stdout_truncated"])
        self.assertTrue(result["stderr_truncated"])

    def test_timeout_kills_entire_process_group(self):
        script = "import subprocess,sys,time\np=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])\nprint(p.pid,flush=True)\ntime.sleep(60)"
        started = time.monotonic()
        result = self.run_python(script, timeout=1)
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "command_timeout")
        self.assertEqual(result["verification"], {"status": "failed", "method": "process_exit", "scope": "shell"})
        self.assertLess(time.monotonic() - started, 4)
        child = int(result["stdout"].strip())
        for _ in range(20):
            if not self.process_is_running(child):
                break
            time.sleep(0.02)
        self.assertFalse(self.process_is_running(child))

    def test_child_holding_pipes_after_parent_exit_is_killed_on_timeout(self):
        result = self.run_python("import subprocess,sys\np=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\nprint(p.pid,flush=True)", timeout=1)
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["returncode"], 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "command_timeout")
        self.assertEqual(result["verification"]["status"], "failed")
        child = int(result["stdout"].strip())
        for _ in range(20):
            if not self.process_is_running(child):
                break
            time.sleep(0.02)
        self.assertFalse(self.process_is_running(child))

    def test_keyboard_interrupt_cleans_up_process(self):
        captured = []
        original_popen = subprocess.Popen

        def capture(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            captured.append(process)
            return process

        selector = selectors.DefaultSelector()
        with patch("fusion_agent.local_tools.selectors.DefaultSelector", return_value=selector), patch.object(selector, "select", side_effect=KeyboardInterrupt), patch("fusion_agent.local_tools.subprocess.Popen", side_effect=capture):
            with self.assertRaises(KeyboardInterrupt):
                self.run_python("import time; time.sleep(60)")
        self.assertEqual(len(captured), 1)
        self.assertIsNotNone(captured[0].poll())

    def test_shell_invalid_arguments_and_cwd_escape(self):
        for args in ({"argv": ["true"], "command": "true"}, {"argv": []}, {"command": ""}, {"argv": ["true"], "timeout": True}, {"argv": ["true"], "timeout": 301}):
            self.assert_tool_error("invalid_arguments", self.tools.shell_run, args)
        self.assert_tool_error("path_outside_workspace", self.tools.shell_run, {"argv": ["true"], "cwd": ".."})
        (self.workspace / "escape").symlink_to(self.base)
        self.assert_tool_error("path_outside_workspace", self.tools.shell_run, {"argv": ["true"], "cwd": "escape"})

    def test_atomic_write_requires_explicit_overwrite(self):
        result = self.tools.files_write({"path": "hello.txt", "content": "中文"})
        self.assertEqual(result["bytes"], 6)
        self.assertTrue(result["ok"])
        self.assertEqual(result["verification"], {"status": "verified", "method": "file_readback", "scope": "files"})
        self.assertEqual((self.workspace / "hello.txt").stat().st_mode & 0o777, 0o600)
        self.assert_tool_error("file_exists", self.tools.files_write, {"path": "hello.txt", "content": "replace"})
        self.assertEqual((self.workspace / "hello.txt").read_text(), "中文")
        result = self.tools.files_write({"path": "hello.txt", "content": "replace", "overwrite": True})
        self.assertTrue(result["ok"])
        self.assertEqual(result["verification"]["status"], "verified")
        self.assertEqual((self.workspace / "hello.txt").read_text(), "replace")
        self.assertEqual([p.name for p in self.workspace.iterdir()], ["hello.txt"])

    def test_write_empty_file_is_verified(self):
        result = self.tools.files_write({"path": "empty.txt", "content": ""})
        self.assertTrue(result["ok"])
        self.assertEqual(result["verification"]["status"], "verified")
        self.assertEqual((self.workspace / "empty.txt").read_bytes(), b"")

    def test_write_readback_detects_real_content_mismatch(self):
        original_link = os.link
        destination = self.workspace / "changed.txt"

        def change_after_link(*args, **kwargs):
            original_link(*args, **kwargs)
            destination.write_bytes(b"wrong")

        with patch("fusion_agent.local_tools.os.link", side_effect=change_after_link):
            result = self.tools.files_write({"path": destination.name, "content": "right"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "verification_failed")
        self.assertEqual(result["verification"], {"status": "failed", "method": "file_readback", "scope": "files"})
        self.assertEqual(destination.read_bytes(), b"wrong")
        self.assertEqual([p.name for p in self.workspace.iterdir()], [destination.name])

    def test_write_readback_rejects_replacement_inode_even_with_same_bytes(self):
        original_link = os.link
        destination = self.workspace / "changed.txt"

        def replace_after_link(*args, **kwargs):
            original_link(*args, **kwargs)
            replacement = self.workspace / "replacement.txt"
            replacement.write_text("right")
            replacement.replace(destination)

        with patch("fusion_agent.local_tools.os.link", side_effect=replace_after_link):
            result = self.tools.files_write({"path": destination.name, "content": "right"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "verification_failed")
        self.assertEqual(result["verification"]["status"], "failed")

    def test_write_readback_rejects_symlink_and_fifo_replacements(self):
        original_link = os.link
        outside = self.base / "outside.txt"
        outside.write_text("right")
        for kind in ("symlink", "fifo"):
            with self.subTest(kind=kind):
                destination = self.workspace / kind

                def replace_after_link(*args, **kwargs):
                    original_link(*args, **kwargs)
                    destination.unlink()
                    if kind == "symlink":
                        destination.symlink_to(outside)
                    else:
                        os.mkfifo(destination)

                with patch("fusion_agent.local_tools.os.link", side_effect=replace_after_link):
                    result = self.tools.files_write({"path": destination.name, "content": "right"})
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "verification_failed")
                self.assertEqual(result["verification"]["status"], "failed")
                self.assertEqual(outside.read_text(), "right")

    def test_create_parent_directories_and_read_offset(self):
        self.assert_tool_error("not_found", self.tools.files_write, {"path": "a/b.txt", "content": "abcdefgh"})
        self.tools.files_write({"path": "a/b.txt", "content": "abcdefgh", "create_parents": True})
        result = self.tools.files_read({"path": "a/b.txt", "offset": 2, "max_bytes": 3})
        self.assertEqual(result["content"], "cde")
        self.assertEqual(result["bytes"], 3)
        self.assertEqual(result["file_bytes"], 8)
        self.assertEqual(result["next_offset"], 5)
        self.assertTrue(result["truncated"])
        self.assertEqual(self.tools.files_read({"path": "a/b.txt", "offset": 100})["content"], "")

    def test_file_escape_and_symlink_escape_rejected(self):
        outside = self.base / "outside.txt"
        outside.write_text("keep")
        (self.workspace / "escape.txt").symlink_to(outside)
        (self.workspace / "escape_dir").symlink_to(self.base)
        for path in ("../outside.txt", str(outside), "escape.txt", "escape_dir/outside.txt"):
            self.assert_tool_error("path_outside_workspace", self.tools.files_read, {"path": path})
            self.assert_tool_error("path_outside_workspace", self.tools.files_write, {"path": path, "content": "changed", "overwrite": True})
        self.assertEqual(outside.read_text(), "keep")

    def test_list_is_bounded_and_does_not_follow_directory_symlink(self):
        (self.workspace / "dir").mkdir()
        (self.workspace / "dir" / "nested.txt").write_text("x")
        (self.workspace / "escape").symlink_to(self.base)
        result = self.tools.files_list({"recursive": True})
        by_path = {entry["path"]: entry for entry in result["entries"]}
        self.assertEqual(by_path["escape"]["type"], "symlink")
        self.assertIn("dir/nested.txt", by_path)
        self.assertEqual(len(by_path), 3)
        self.assertTrue(self.tools.files_list({"limit": 1})["truncated"])

    def test_fifo_read_rejected_without_blocking(self):
        os.mkfifo(self.workspace / "fifo")
        self.assert_tool_error("invalid_file", self.tools.files_read, {"path": "fifo"})

    def test_size_limits_and_bad_utf8(self):
        self.assert_tool_error("file_too_large", self.tools.files_write, {"path": "large", "content": "x" * (1024 * 1024 + 1)})
        (self.workspace / "binary").write_bytes(b"a\xffb")
        self.assertEqual(self.tools.files_read({"path": "binary"})["content"], "a\ufffdb")
        self.assert_tool_error("invalid_arguments", self.tools.files_read, {"path": "binary", "max_bytes": 2**22})

    def test_close_is_idempotent_and_blocks_new_work(self):
        self.tools.close()
        self.tools.close()
        self.assert_tool_error("closed", self.tools.files_list, {})
        self.assert_tool_error("closed", self.tools.shell_run, {"argv": ["true"]})


if __name__ == "__main__":
    unittest.main()
