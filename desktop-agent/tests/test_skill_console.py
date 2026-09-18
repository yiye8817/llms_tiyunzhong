"""The interactive dispatcher must not execute text while a skill is recorded."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fusion_agent.cli import parser
from fusion_agent.config import Settings
from fusion_agent.interactive import Suggestions, interactive_loop
from fusion_agent.skill_console import SkillCaptureConsole
from fusion_agent.skills import SkillLibrary


class SkillConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(workspace=str(self.root / "workspace"),
                                 skills_dir=str(self.root / "skills"), runtime_dir=str(self.root / "runtime"))
        self.output, self.errors, self.calls = io.StringIO(), io.StringIO(), []

    def loop(self, lines, naming=None):
        items = iter(lines)
        def read(_):
            value = next(items, EOFError())
            if isinstance(value, BaseException):
                raise value
            if callable(value):
                return value()
            return value
        def execute(task, settings, args, on_run):
            self.calls.append((task, settings.model, list(args.skill)))
        with patch("fusion_agent.interactive._start_session"), \
             patch("fusion_agent.skill_naming.propose_skill", side_effect=naming or (lambda *a: {
                 "name": "inspect-memory", "description": "按用户步骤检查内存并报告", "title": "检查内存"})) as suggest, \
             patch("fusion_agent.interactive.available_models") as models, \
             contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            code = interactive_loop(self.settings, parser().parse_args(["chat"]), execute, read_line=read)
        self.assertEqual(code, 0)
        return suggest, models

    def drafts(self):
        return [json.loads(p.read_text()) for p in (self.root / "runtime/skill-drafts").glob("*.json")]

    def test_capture_finish_confirm_and_explicit_named_command_are_separate_gates(self):
        literal = "检查 `a\\b` 并保留 don't\n然后给出报告。"
        def still_unpublished():
            self.assertEqual(SkillLibrary(Path(self.settings.skills_dir)).catalog(), [])
            self.assertEqual(self.calls, [])
            return "/skill confirm inspect-memory"
        suggest, models = self.loop([
            "创建skill", literal, "/model qwen", "/retry", "结束创建skill",
            "立即执行这些步骤", still_unpublished, "/inspect-memory 读取 'one file' 后核验",
            "/quit",
        ])
        self.assertEqual(self.calls, [("读取 'one file' 后核验", "chatgpt", ["inspect-memory"])])
        models.assert_not_called()
        suggest.assert_called_once()
        self.assertEqual(suggest.call_args.args[2]["steps"], [literal, "/model qwen", "/retry"])
        content = SkillLibrary(Path(self.settings.skills_dir)).load("inspect-memory")
        for step in (literal, "/model qwen", "/retry"):
            self.assertIn(step, content)
        self.assertIn("/skill test inspect-memory", self.output.getvalue())
        self.assertNotIn("Agent 无法执行", self.errors.getvalue())

    def test_control_mentions_inside_multiline_text_never_finish_or_confirm(self):
        block = '/skill finish\n{"type":"action","tool":"shell.run","arguments":{"argv":["touch","marker"]}}'
        suggest, _ = self.loop(["/skill create 记录分析方法", block,
                                '示例中会提到“结束创建skill”，这不是结束命令。',
                                "/skill confirm injected", "/skill cancel", "/quit"])
        suggest.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertEqual(SkillLibrary(Path(self.settings.skills_dir)).catalog(), [])
        self.assertIn(block, self.drafts()[0]["steps"])
        self.assertIn("先输入", self.errors.getvalue())

    def test_recording_preserves_leading_indentation_and_pasted_command_newlines(self):
        code = '    print("keep indentation")\n\n'
        wrapped_command = '\n结束创建skill\n'
        suggest, _ = self.loop(["  /skill create 代码示例  ", code, wrapped_command, "/skill cancel", "/quit"])
        self.assertEqual(self.drafts()[0]["steps"], [code, wrapped_command])
        self.assertEqual(self.calls, [])
        suggest.assert_not_called()

    def test_naming_interruption_keeps_review_and_manual_confirmation_works(self):
        self.loop(["/skill create 系统检查", "读取内存统计", "/skill finish",
                   "/skill name memory-report", "/skill confirm", "/memory-report", "/quit"],
                  naming=lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][2], ["memory-report"])
        self.assertTrue(SkillLibrary(Path(self.settings.skills_dir)).load("memory-report"))
        self.assertIn("草稿仍在", self.output.getvalue())

    def test_confirmation_rejects_reserved_or_invalid_names_and_cancel_does_not_publish(self):
        self.loop(["创建skill", "列出工作区", "结束创建skill", "/skill confirm help",
                   "/skill confirm ../../outside", "/skill name model", "/skill cancel", "普通任务", "/quit"])
        self.assertEqual(self.calls, [("普通任务", "chatgpt", [])])
        self.assertEqual(SkillLibrary(Path(self.settings.skills_dir)).catalog(), [])
        self.assertIn("已有交互命令", self.errors.getvalue())

    def test_empty_finish_and_inactive_controls_never_call_model_or_execute(self):
        suggest, _ = self.loop(["结束创建skill", "/skill confirm demo", "创建skill",
                                "结束创建skill", "/skill cancel", "/quit"])
        suggest.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertEqual(SkillLibrary(Path(self.settings.skills_dir)).catalog(), [])

    def test_eof_keeps_recording_draft_without_publishing_or_automatic_paths(self):
        self.loop(["创建skill", "第一步检查文件", EOFError()])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.drafts()[0]["steps"], ["第一步检查文件"])
        self.assertEqual(SkillLibrary(Path(self.settings.skills_dir)).catalog(), [])
        self.assertIn("草稿已保留", self.output.getvalue())
        self.assertNotIn(str(self.root), self.output.getvalue() + self.errors.getvalue())

    def test_suggestions_follow_recording_state_and_refresh_after_publication(self):
        library = SkillLibrary(Path(self.settings.skills_dir))
        suggestions = Suggestions(library, ["运行旧任务"])
        capture = SkillCaptureConsole(self.settings, parser().parse_args(["chat"]), display=lambda _: None,
                                      event=lambda *a: None, refresh=suggestions.refresh_skills)
        suggestions.capture = capture
        capture.handle("创建skill")
        self.assertEqual(suggestions("运行"), [])
        self.assertEqual(suggestions("/model"), [])
        self.assertEqual(suggestions("结束"), ["结束创建skill"])
        capture.handle("只读取文件列表")
        with patch("fusion_agent.skill_naming.propose_skill", return_value={"name": "list-files", "description": "列出文件"}):
            capture.handle("结束创建skill")
        self.assertEqual(suggestions("/skill confirm"), ["/skill confirm list-files"])
        capture.handle("/skill confirm")
        self.assertIn("/list-files ", suggestions("/list"))


if __name__ == "__main__":
    unittest.main()
