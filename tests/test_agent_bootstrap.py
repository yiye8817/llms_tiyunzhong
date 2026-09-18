"""Browser tasks must use the Agent's own Python, without shell argument rewriting."""

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


AGENT = Path(__file__).resolve().parents[1] / "desktop-agent"
SPEC = importlib.util.spec_from_file_location("agent_bootstrap", AGENT / "bootstrap.py")
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class AgentBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        sys.path.insert(0, str(AGENT / "src"))
        self.addCleanup(sys.path.remove, str(AGENT / "src"))
        for mocked in (patch.dict(os.environ, {}, clear=True), patch.object(bootstrap.sys, "prefix", "/system"), patch.object(bootstrap.sys, "base_prefix", "/system")):
            mocked.start()
            self.addCleanup(mocked.stop)

    def make_venv(self, command, **kwargs):
        self.assertEqual(command, [sys.executable, "-m", "venv", str(self.root / ".venv")])
        executable = self.root / ".venv/bin/python"
        executable.parent.mkdir(parents=True)
        executable.write_text("fixture")
        executable.chmod(0o700)
        return SimpleNamespace(returncode=0)

    def test_browser_capability_creates_exact_agent_environment_without_installing_packages(self):
        with patch.object(bootstrap.subprocess, "run", side_effect=self.make_venv) as create:
            selected = bootstrap.select_python(["run", "task", "--allow", "shell,browser"], self.root)
        self.assertEqual(selected, str(self.root / ".venv/bin/python"))
        create.assert_called_once()

    def test_configured_browser_capability_and_core_help_discovery_paths(self):
        with patch("fusion_agent.config.load_settings", return_value=SimpleNamespace(allowed_capabilities=["browser"])) as load:
            self.assertTrue(bootstrap.browser_requested(["run", "task", "--config", "chosen.json"]))
            load.assert_called_once_with("chosen.json")
        for args in (["doctor"], ["tools"], ["--version"], ["run", "--help", "--allow", "browser"]):
            self.assertFalse(bootstrap.browser_requested(args))
        with patch("fusion_agent.config.load_settings", return_value=SimpleNamespace(allowed_capabilities=["files"])):
            with patch.object(bootstrap.subprocess, "run") as create:
                self.assertEqual(bootstrap.select_python(["run", "task"], self.root), sys.executable)
            create.assert_not_called()

    def test_explicit_interpreter_and_existing_virtual_environment_are_preserved(self):
        with patch.dict(os.environ, {"FUSION_AGENT_PYTHON": "/chosen/python"}), patch.object(bootstrap, "browser_requested") as check:
            self.assertEqual(bootstrap.select_python(["run", "--allow=browser"], self.root), sys.executable)
            check.assert_not_called()
        with patch.object(bootstrap.sys, "prefix", "/active-venv"), patch.object(bootstrap.subprocess, "run") as create:
            self.assertEqual(bootstrap.select_python(["run", "--allow=browser"], self.root), sys.executable)
            create.assert_not_called()

    def test_interactive_browser_tasks_share_venv_selection_and_plain_stdin_does_not_create_one(self):
        with patch("fusion_agent.config.load_settings", return_value=SimpleNamespace(allowed_capabilities=["browser"])):
            self.assertTrue(bootstrap.browser_requested(["chat"]))
            self.assertFalse(bootstrap.browser_requested(["chat", "--help"]))
            with patch.object(bootstrap.sys.stdin, "isatty", return_value=True):
                self.assertTrue(bootstrap.browser_requested([]))
            with patch.object(bootstrap.sys.stdin, "isatty", return_value=False):
                self.assertFalse(bootstrap.browser_requested([]))
        with patch.object(bootstrap.subprocess, "run", side_effect=self.make_venv) as create:
            self.assertEqual(bootstrap.select_python(["chat", "--allow=browser"], self.root), str(self.root / ".venv/bin/python"))
        create.assert_called_once()

    def test_environment_creation_failure_stops_before_model_or_agent_exec(self):
        with patch.object(bootstrap.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(RuntimeError, "创建失败"):
                bootstrap.select_python(["run", "--allow=browser"], self.root)
        (self.root / ".venv").symlink_to(self.root / "outside")
        with self.assertRaisesRegex(ValueError, "符号链接"):
            bootstrap.select_python(["run", "--allow=browser"], self.root)

    def test_verbose_flags_preserve_browser_environment_detection(self):
        with patch("fusion_agent.config.load_settings", return_value=SimpleNamespace(allowed_capabilities=["browser"])), \
             patch.object(bootstrap.sys.stdin, "isatty", return_value=True):
            for args in (["--v"], ["-v", "chat"], ["--verbose", "run", "task"], ["chat", "--v"]):
                with self.subTest(args=args):
                    self.assertTrue(bootstrap.browser_requested(args))
            for args in (["--v", "history"], ["--v", "skills"], ["--v", "skill-test", "demo"]):
                with self.subTest(args=args):
                    self.assertFalse(bootstrap.browser_requested(args))
    def test_exec_preserves_all_literal_task_arguments(self):
        arguments = ["run", "literal $(not-a-command) `quoted` 中文", "--allow", "shell,browser"]
        with patch.object(bootstrap, "select_python", return_value="/chosen/python"), patch.object(bootstrap.os, "execv") as execute:
            bootstrap.main(arguments)
        execute.assert_called_once_with("/chosen/python", ["/chosen/python", "-m", "fusion_agent", *arguments])

    def launcher_fixture(self):
        root = self.root / "agent project"
        root.mkdir()
        shutil.copy2(AGENT / "run.sh", root / "run.sh")

        def interpreter(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#!/bin/sh\nprintf \'%s\\0\' "$0" "$PYTHONPATH" "$@"\n')
            path.chmod(0o700)
            return path

        project = interpreter(root / ".venv/bin/python")
        active = interpreter(self.root / "active environment/bin/python")
        explicit = interpreter(self.root / "explicit choice/bin/python")
        fallback = interpreter(self.root / "path environment/bin/python3")
        environment = {"PATH": str(fallback.parent) + os.pathsep + os.defpath,
                       "PYTHONPATH": "existing-import-path"}
        return root, project, active, explicit, fallback, environment

    def launch_fixture(self, root, environment, arguments):
        result = subprocess.run(["bash", str(root / "run.sh"), *arguments], env=environment,
                                capture_output=True, timeout=5, check=True)
        fields = result.stdout.decode().split("\0")
        self.assertEqual(fields.pop(), "")
        self.assertEqual(fields[1], str(root / "src") + os.pathsep + "existing-import-path")
        self.assertEqual(fields[2:], [str(root / "bootstrap.py"), *arguments])
        return fields[0]

    def test_launcher_respects_active_then_explicit_python_before_project_environment(self):
        root, project, active, explicit, fallback, environment = self.launcher_fixture()
        arguments = ["run", "literal $(not-a-command) `quoted` 中文", "--allow", "shell,browser"]
        environment["VIRTUAL_ENV"] = str(active.parent.parent)
        self.assertEqual(self.launch_fixture(root, environment, arguments), str(active))
        environment["FUSION_AGENT_PYTHON"] = str(explicit)
        self.assertEqual(self.launch_fixture(root, environment, arguments), str(explicit))

    def test_launcher_falls_back_to_project_then_path_python(self):
        root, project, active, explicit, fallback, environment = self.launcher_fixture()
        environment["VIRTUAL_ENV"] = str(self.root / "missing-environment")
        self.assertEqual(self.launch_fixture(root, environment, ["doctor"]), str(project))
        project.unlink()
        self.assertEqual(self.launch_fixture(root, environment, ["run", "--help"]), str(fallback))


if __name__ == "__main__":
    unittest.main()
