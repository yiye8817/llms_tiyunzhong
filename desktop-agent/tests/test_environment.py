import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fusion_agent.contracts import ToolError
import fusion_agent.environment as environment
from fusion_agent.environment import EnvironmentTools
from fusion_agent.registry import ToolRegistry


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name)
        self.binary = self.runtime / "chromium"
        self.runner = Mock(return_value={"ok": True, "returncode": 0, "stdout": "installed", "stderr": ""})
        self.events = []
        self.tools = EnvironmentTools(self.runtime, self.runner, lambda name, fields: self.events.append((name, fields)))
        self.driver = types.SimpleNamespace(chromium=types.SimpleNamespace(executable_path=str(self.binary)), stop=Mock())
        self.api = types.SimpleNamespace(sync_playwright=Mock(return_value=types.SimpleNamespace(start=Mock(return_value=self.driver))))
        self.importer = self.enterContext(patch.object(environment.importlib, "import_module", return_value=self.api))
        self.invalidate = self.enterContext(patch.object(environment.importlib, "invalidate_caches"))
        self.enterContext(patch.object(environment.importlib.util, "find_spec", return_value=object()))
        self.enterContext(patch.object(sys, "prefix", str(self.runtime / "venv")))
        self.enterContext(patch.object(sys, "base_prefix", "/system/python"))
        self.enterContext(patch.object(sys, "executable", str(self.runtime / "venv/bin/python")))

    def test_check_reports_available_without_launch_or_install(self):
        self.binary.touch()
        result = self.tools.browser_check({})
        self.assertTrue(result["ok"])
        self.assertTrue(result["dependency_ready"])
        self.assertFalse(result["browser_launch_verified"])
        self.assertEqual(result["repair_tool"], "environment.browser_setup")
        self.assertEqual(result["python_executable"], sys.executable)
        self.assertTrue(result["in_virtualenv"])
        self.assertNotIn("verification", result)
        self.driver.stop.assert_called_once()
        self.runner.assert_not_called()

    def test_check_reports_missing_package_with_current_python(self):
        self.importer.side_effect = ModuleNotFoundError("No module named playwright")
        result = self.tools.browser_check({})
        self.assertTrue(result["ok"])
        self.assertFalse(result["dependency_ready"])
        self.assertFalse(result["package_ready"])
        self.assertIsNone(result["browser_binary_ready"])
        self.assertIn(sys.executable, result["check_error"]["message"])
        self.assertNotIn("error", result)
        self.assertNotIn("verification", result)
        self.runner.assert_not_called()

    def test_check_missing_binary_is_successful_diagnostic_without_task_verification(self):
        registry = ToolRegistry(self.tools.specs(), allowed=["browser"])
        result = registry.invoke("environment.browser_check", {})
        self.assertTrue(result["ok"])
        self.assertFalse(result["dependency_ready"])
        self.assertTrue(result["package_ready"])
        self.assertFalse(result["browser_binary_ready"])
        self.assertEqual(result["check_error"]["phase"], "browser_binary")
        self.assertNotIn("verification", result)
        self.runner.assert_not_called()

    def test_check_reports_active_sync_driver_error_without_guessing_missing_dependencies(self):
        self.api.sync_playwright.return_value.start.side_effect = RuntimeError("Playwright Sync API inside the asyncio loop")
        result = self.tools.browser_check({})
        self.assertFalse(result["ok"])
        self.assertTrue(result["package_ready"])
        self.assertIsNone(result["browser_binary_ready"])
        self.assertEqual(result["error"]["code"], "dependency_check_failed")
        self.assertEqual(result["error"]["phase"], "browser_binary_inspection")
        self.assertNotIn("verification", result)
        self.runner.assert_not_called()

    def test_check_borrows_active_browser_driver_without_starting_or_stopping_it(self):
        self.binary.touch()
        tools = EnvironmentTools(self.runtime, self.runner, active_driver=lambda: self.driver)
        result = tools.browser_check({})
        self.assertTrue(result["ok"])
        self.assertTrue(result["dependency_ready"])
        self.assertFalse(result["browser_launch_verified"])
        self.assertNotIn("verification", result)
        self.api.sync_playwright.assert_not_called()
        self.driver.stop.assert_not_called()
        self.runner.assert_not_called()

    def test_borrowed_driver_is_not_stopped_after_inspection_error(self):
        driver = types.SimpleNamespace(chromium=types.SimpleNamespace(), stop=Mock())
        tools = EnvironmentTools(self.runtime, self.runner, active_driver=lambda: driver)
        result = tools.browser_check({})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["phase"], "browser_binary_inspection")
        self.api.sync_playwright.assert_not_called()
        driver.stop.assert_not_called()

    def test_setup_without_shell_capability_never_runs_installer(self):
        registry = ToolRegistry(self.tools.specs(), allowed=["browser"])
        result = registry.invoke("environment.browser_setup", {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "capability_denied")
        self.runner.assert_not_called()

    def test_setup_rejects_system_python_before_inspection_or_commands(self):
        with patch.object(sys, "prefix", sys.base_prefix), self.assertRaises(ToolError) as error:
            self.tools.browser_setup({})
        self.assertEqual(error.exception.code, "dependency_environment")
        self.assertTrue(error.exception.not_executed)
        self.assertIn("./run.sh", str(error.exception))
        self.importer.assert_not_called()
        self.runner.assert_not_called()

    def test_rejects_user_package_and_arbitrary_argv(self):
        registry = ToolRegistry(self.tools.specs(), allowed=["shell"])
        for args in ({"argv": ["rm", "-rf", "/"]}, {"package": "something-else"}):
            with self.subTest(args=args):
                result = registry.invoke("environment.browser_setup", args)
                self.assertEqual(result["error"]["code"], "invalid_arguments")
        self.runner.assert_not_called()

    def test_setup_skips_every_install_when_dependencies_are_present(self):
        self.binary.touch()
        result = self.tools.browser_setup({})
        self.assertTrue(result["ok"])
        self.assertEqual(result["steps"], [])
        self.runner.assert_not_called()

    def test_fixed_install_commands_preserve_venv_interpreter_and_invalidate_import_cache(self):
        self.importer.side_effect = [ModuleNotFoundError("No module named playwright"), self.api, self.api]
        def run(args):
            if args["argv"][-2:] == ["install", "chromium"]:
                self.binary.touch()
            return {"ok": True, "returncode": 0, "stdout": "complete", "stderr": ""}
        self.runner.side_effect = run
        result = self.tools.browser_setup({})
        self.assertTrue(result["ok"])
        calls = [call.args[0] for call in self.runner.call_args_list]
        self.assertEqual([call["argv"] for call in calls], [
            [sys.executable, "-m", "pip", "install", "playwright>=1.50,<2"],
            [sys.executable, "-m", "playwright", "install", "chromium"],
        ])
        self.assertTrue(all(1 <= call["timeout"] <= 300 for call in calls))
        self.assertGreaterEqual(self.invalidate.call_count, 3)
        self.assertEqual(result["verification"]["method"], "dependency_check")
        self.assertFalse(result["browser_launch_verified"])
        self.assertEqual([name for name, _ in self.events], [
            "environment.command.started", "environment.command.completed",
            "environment.command.started", "environment.command.completed"])
        self.assertEqual(self.events[-1][1]["payload"]["result"]["stdout"], "complete")

    def test_ensurepip_only_when_needed_then_fixed_package_installer(self):
        self.importer.side_effect = [ModuleNotFoundError("No module named playwright"), self.api]
        self.binary.touch()
        with patch.object(environment.importlib.util, "find_spec", return_value=None):
            result = self.tools.browser_setup({})
        self.assertTrue(result["ok"])
        self.assertEqual([call.args[0]["argv"] for call in self.runner.call_args_list], [
            [sys.executable, "-m", "ensurepip", "--upgrade"],
            [sys.executable, "-m", "pip", "install", "playwright>=1.50,<2"]])

    def test_missing_binary_only_installs_chromium(self):
        def run(args):
            self.binary.touch()
            return {"ok": True, "returncode": 0}
        self.runner.side_effect = run
        result = self.tools.browser_setup({})
        self.assertTrue(result["ok"])
        self.assertEqual(self.runner.call_count, 1)
        self.assertEqual(self.runner.call_args.args[0]["argv"], [sys.executable, "-m", "playwright", "install", "chromium"])

    def test_failed_package_install_stops_without_browser_command(self):
        self.importer.side_effect = ModuleNotFoundError("No module named playwright")
        self.runner.return_value = {"ok": False, "returncode": 1, "stdout": "attempted", "stderr": "network unavailable"}
        result = self.tools.browser_setup({})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["step"], "install_playwright")
        self.assertIn(sys.executable, result["error"]["message"])
        self.assertEqual(result["steps"][0]["result"]["stderr"], "network unavailable")
        self.assertEqual(result["verification"]["status"], "failed")
        self.assertEqual(self.runner.call_count, 1)

    def test_timed_out_install_is_unknown_and_is_not_repeated(self):
        self.runner.return_value = {"ok": False, "returncode": -9, "timed_out": True, "stdout": "partial", "stderr": ""}
        result = self.tools.browser_setup({})
        self.assertFalse(result["ok"])
        self.assertTrue(result["outcome_unknown"])
        self.assertEqual(result["error"]["code"], "dependency_timeout")
        self.assertEqual(self.runner.call_count, 1)

    def test_success_exit_without_installed_binary_is_not_successful_setup(self):
        result = self.tools.browser_setup({})
        self.assertFalse(result["ok"])
        self.assertFalse(result["browser_binary_ready"])
        self.assertEqual(self.runner.call_count, 1)

    def test_driver_inspection_error_does_not_guess_missing_binary(self):
        self.api.sync_playwright.return_value.start.side_effect = RuntimeError("driver could not start")
        result = self.tools.browser_setup({})
        self.assertFalse(result["ok"])
        self.assertTrue(result["package_ready"])
        self.assertIsNone(result["browser_binary_ready"])
        self.assertEqual(result["error"]["phase"], "browser_binary_inspection")
        self.runner.assert_not_called()

    def test_total_command_budget_is_bounded(self):
        self.importer.side_effect = ModuleNotFoundError("No module named playwright")
        with patch.object(environment.time, "monotonic", side_effect=[0, 450]):
            result = self.tools.browser_setup({})
        self.assertFalse(result["ok"])
        self.assertEqual(self.runner.call_args.args[0]["timeout"], 150)

    def test_exhausted_total_budget_dispatches_nothing(self):
        with patch.object(environment.time, "monotonic", side_effect=[0, 601]):
            result = self.tools.browser_setup({})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "dependency_timeout")
        self.runner.assert_not_called()

    def test_keyboard_interrupt_is_not_converted_to_installer_failure(self):
        self.runner.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.tools.browser_setup({})
        self.assertEqual(self.runner.call_count, 1)


if __name__ == "__main__":
    unittest.main()
