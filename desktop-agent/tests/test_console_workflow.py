"""Exercise the console wiring across editor, task options and local queries."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fusion_agent.cli import execute_run, main, parser
from fusion_agent.config import Settings
from fusion_agent.interactive import Suggestions, interactive_loop
from fusion_agent.skills import SkillLibrary


class ConsoleWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(workspace=str(self.root / "workspace"),
                                 runtime_dir=str(self.root / "runtime"),
                                 skills_dir=str(self.root / "skills"))

    def loop(self, lines, execute):
        lines = iter(lines)
        output, errors = io.StringIO(), io.StringIO()
        with patch("fusion_agent.interactive._start_session"), \
             patch("fusion_agent.interactive.available_models", return_value=["chatgpt", "qwen"]), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = interactive_loop(self.settings, parser().parse_args(["chat"]), execute,
                                    read_line=lambda _: next(lines))
        self.assertEqual(code, 0)
        return output.getvalue(), errors.getvalue()

    def test_default_and_verbose_keep_same_durable_records(self):
        for verbose in (False, True):
            with self.subTest(verbose=verbose):
                flags = ["run", "解释一个问题"] + (["--v"] if verbose else [])
                output, errors = io.StringIO(), io.StringIO()
                with patch.dict(os.environ, {"FUSION_AGENT_API_KEY": "fixture-key"}), \
                     patch("fusion_agent.parent_service.ensure_parent", return_value={"web_progress_supported": True}) as parent, \
                     patch("fusion_agent.client.FusionClient.complete", return_value='{"type":"final","answer":"## 回复\\n\\n**已完成**解释。"}'), \
                     contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    code = execute_run("解释一个问题", self.settings, parser().parse_args(flags))
                self.assertEqual(code, 0, errors.getvalue())
                self.assertEqual(parent.call_args.kwargs["verbose"], verbose)
                self.assertEqual(str(self.root / "workspace") in errors.getvalue(), verbose)
                self.assertEqual("任务记录：" in errors.getvalue(), verbose)
                self.assertEqual("events.jsonl" in errors.getvalue(), verbose)
                self.assertNotIn('"event":', errors.getvalue())
                self.assertIn("已完成", output.getvalue())
        runs = list((self.root / "runtime/runs").iterdir())
        self.assertEqual(len(runs), 2)
        for run in runs:
            self.assertEqual(json.loads((run / "state.json").read_text())["status"], "completed")
            self.assertIn("已完成", (run / "result.md").read_text())
            self.assertIn("model.raw_reply", (run / "events.jsonl").read_text())

    def test_verbose_only_enters_interactive_and_option_placement_preserves_value(self):
        for arguments in (["--v", "chat"], ["chat", "--v"], ["--verbose", "run", "task"], ["run", "task", "-v"]):
            with self.subTest(arguments=arguments):
                self.assertTrue(parser().parse_args(arguments).verbose)
        with patch("sys.stdin.isatty", return_value=True), \
             patch("fusion_agent.cli.load_settings", return_value=self.settings), \
             patch("fusion_agent.interactive.interactive_loop", return_value=0) as chat:
            self.assertEqual(main(["--v"]), 0)
        self.assertTrue(chat.call_args.args[1].verbose)
        self.assertEqual(chat.call_args.args[1].command, "chat")

    def test_dynamic_skills_verbose_and_retry_preserve_task_text(self):
        calls = []
        def execute(task, selected, args, on_run):
            calls.append((task, selected.model, args.verbose, list(args.skill)))
        literal = "检查路径 `a\\b`，保留 don't 和未闭合引号 \"\n再给出说明"
        out, err = self.loop([
            "/skill-demo demo", "/skill test demo", "/skill load demo", "首个任务",
            "/verbose on", "/skill unload demo", "独立任务",
            "/skill run demo " + literal, "/model qwen", "/retry", "/verbose off",
            "最后任务", "/quit",
        ], execute)
        self.assertEqual(calls, [
            ("首个任务", "chatgpt", False, ["demo"]),
            ("独立任务", "chatgpt", True, []),
            (literal, "chatgpt", True, ["demo"]),
            (literal, "qwen", True, ["demo"]),
            ("最后任务", "qwen", False, []),
        ])
        self.assertIn("structure_passed", out)
        self.assertIn("./run.sh skill-test demo", out)
        self.assertNotIn("Agent 无法执行", err)

    def test_history_commands_are_read_only_and_do_not_start_parent_or_execute(self):
        calls = []
        out, err = self.loop(["/history 旧任务", "/sessions", "/history show missing", "/quit"],
                             lambda *a, **k: calls.append(a))
        self.assertEqual(calls, [])
        self.assertIn("没有匹配", out)
        with patch("fusion_agent.cli.load_settings", return_value=self.settings), \
             patch("fusion_agent.parent_service.ensure_parent") as parent, \
             patch("fusion_agent.cli.execute_run") as execute, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["history", "旧任务"]), 0)
            self.assertEqual(main(["sessions"]), 0)
            self.assertEqual(main(["history", "--limit", "0"]), 2)
        parent.assert_not_called()
        execute.assert_not_called()

    def test_suggestions_are_local_and_refresh_new_skill_catalog(self):
        from fusion_agent.skill_demo import create_demo_skill
        suggestions = Suggestions(SkillLibrary(Path(self.settings.skills_dir)), ["检查代码", "检查结果"])
        with patch("fusion_agent.interactive.available_models") as network:
            self.assertEqual(suggestions("检查"), ["检查结果", "检查代码"])
            self.assertEqual(suggestions("/mod")[0], "/models")
            self.assertIn("/model qwen", suggestions("/model q"))
            self.assertEqual(suggestions("/model gl"), ["/model glm"])
            self.assertEqual(suggestions("/model k"), ["/model kimi"])
            self.assertEqual(suggestions("/model web-gl"), ["/model web-glm"])
            self.assertEqual(suggestions("/model web-k"), ["/model web-kimi"])
            create_demo_skill("demo", Path(self.settings.skills_dir))
            suggestions.refresh_skills()
            self.assertEqual(suggestions("/skill load d"), ["/skill load demo"])
            self.assertEqual(suggestions("/skill run d"), ["/skill run demo "])
        network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
