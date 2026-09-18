from concurrent.futures import ThreadPoolExecutor
import errno
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fusion_agent.contracts import ToolError
from fusion_agent.skill_recording import MAX_RECORDING_BYTES, MAX_STEPS, SkillRecording
from fusion_agent.skills import MAX_SKILL_BYTES, SkillLibrary


class SkillRecordingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.skills = self.root / "skills"
        self.runtime = self.root / "runtime"
        self.recorder = SkillRecording(self.skills, self.runtime)

    def prepare(self, name="inspect-workspace", recorder=None):
        recorder = recorder or self.recorder
        recorder.start("检查工作区")
        recorder.append("读取当前工作区的文件列表，确认结果。")
        recorder.finish({"name": name, "description": "需要检查当前工作区时使用。", "title": "检查工作区"})
        return recorder

    def persisted(self, recorder=None):
        return json.loads((recorder or self.recorder).draft_path.read_text(encoding="utf-8"))

    def assertCode(self, code, action):
        with self.assertRaises(ToolError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)

    def test_explicit_record_review_confirmation_and_discovery(self):
        recorder = self.recorder
        self.assertEqual(recorder.phase, "idle")
        recorder.start("统计工作区")
        steps = ["  files.list {'path': '.'}  ", "读取结果\n\n核对数量。\n", "/run rm -rf SHOULD_NEVER_EXECUTE"]
        with mock.patch("subprocess.Popen", side_effect=AssertionError("recording executed a command")):
            for step in steps:
                recorder.append(step)
            preview = recorder.finish({"name": "count-workspace", "description": "统计工作区文件时使用。"})
            self.assertEqual(recorder.phase, "review")
            self.assertFalse(self.skills.exists())
            self.assertEqual(preview["steps"], steps)
            self.assertEqual(self.persisted()["steps"], steps)
            result = recorder.confirm()
        self.assertEqual(recorder.phase, "idle")
        self.assertEqual(result["name"], "count-workspace")
        self.assertTrue(result["published"])
        self.assertFalse(result["executed"])
        self.assertTrue(result["draft_saved"])
        library = SkillLibrary(self.skills)
        self.assertEqual([entry["name"] for entry in library.catalog()], ["count-workspace"])
        content = library.load("count-workspace")
        self.assertEqual(content, preview["markdown"])
        self.assertLessEqual(len(content.encode("utf-8")), MAX_SKILL_BYTES)
        positions = [content.index(step) for step in steps]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(self.persisted()["status"], "published")
        self.assertIn("/skill test count-workspace", result["hints"]["test"])
        for path in (recorder.draft_path, Path(result["files"][0])):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_all_state_gates_and_empty_finish(self):
        recorder = self.recorder
        for action in (lambda: recorder.append("run something"), recorder.finish,
                       lambda: recorder.rename("name"), recorder.confirm, recorder.cancel,
                       lambda: recorder.suggest({"name": "x", "description": "x"})):
            self.assertCode("skill_recording_state", action)
        recorder.start()
        self.assertCode("skill_recording_state", recorder.start)
        self.assertCode("skill_recording_state", recorder.confirm)
        self.assertCode("skill_recording_empty", recorder.finish)
        recorder.append("结束创建skill")
        self.assertEqual(recorder.phase, "recording")
        recorder.finish()
        for action in (recorder.start, lambda: recorder.append("new step"), recorder.finish):
            self.assertCode("skill_recording_state", action)
        recorder.confirm()
        self.assertCode("skill_recording_state", recorder.confirm)

    def test_cancel_keeps_private_record_but_publishes_nothing_and_new_start_uses_new_id(self):
        self.prepare()
        previous = self.recorder.draft_path
        self.recorder.cancel()
        self.assertEqual(self.recorder.phase, "idle")
        self.assertEqual(json.loads(previous.read_text())["status"], "cancelled")
        self.assertFalse(self.skills.exists())
        self.recorder.start("新的任务")
        self.assertNotEqual(previous, self.recorder.draft_path)
        self.assertEqual(json.loads(previous.read_text())["steps"], ["读取当前工作区的文件列表，确认结果。"])

    def test_draft_and_preview_are_defensive_copies(self):
        self.prepare()
        self.recorder.draft["steps"].append("injected")
        self.recorder.preview()["steps"].append("injected")
        self.assertEqual(len(self.recorder.draft["steps"]), 1)

    def test_metadata_suggestions_only_change_metadata(self):
        self.prepare()
        before = self.recorder.draft
        self.recorder.suggest({"name": "scan-local-files", "description": "用户需要扫描本地文件时使用。", "title": "扫描文件"})
        after = self.recorder.draft
        self.assertEqual(after["steps"], before["steps"])
        self.assertEqual(after["topic"], before["topic"])
        self.assertEqual(after["proposed_name"], "scan-local-files")
        self.assertFalse(self.skills.exists())
        self.recorder.rename("inspect-files")
        self.assertEqual(self.recorder.draft["description"], after["description"])

    def test_invalid_metadata_and_names_never_publish_or_change_draft(self):
        self.prepare()
        before = self.recorder.draft
        for name in ("../escape", "/tmp/x", "a/b", "x;touch /tmp/x", "$(touch /tmp/x)",
                     "a\nb", "a--b", "Upper", "x" * 65, "", None, 2):
            with self.subTest(name=name):
                self.assertCode("invalid_skill_name", lambda: self.recorder.rename(name))
                if name is not None:
                    self.assertCode("invalid_skill_name", lambda: self.recorder.confirm(name))
                self.assertEqual(self.recorder.draft, before)
        for proposal in ({"name": "x", "description": "d", "steps": ["bad"]},
                         {"name": "x", "description": "d", "command": "sh"},
                         {"name": "x", "description": "two\nlines"},
                         {"name": "x", "description": "x" * 513},
                         {"name": "x", "description": "d", "title": "x" * 161},
                         {"name": "x"}, None, []):
            with self.subTest(proposal=proposal):
                self.assertCode("invalid_skill_proposal", lambda: self.recorder.suggest(proposal))
                self.assertEqual(self.recorder.draft, before)
        self.assertFalse(self.skills.exists())

    def test_invalid_finish_proposal_keeps_recording_until_local_fallback(self):
        self.recorder.start("统计文件")
        self.recorder.append("先列出文件并核对数量")
        self.assertCode("invalid_skill_name", lambda: self.recorder.finish({"name": "../x", "description": "d"}))
        self.assertEqual(self.recorder.phase, "recording")
        result = self.recorder.finish()
        self.assertTrue(SkillLibrary._valid_name(result["proposed_name"]))
        self.assertIn("统计文件", result["description"])

    def test_known_credentials_are_removed_before_persistence_naming_and_publication(self):
        secret = "APIKEY_PRIVATE_123456789"
        recorder = SkillRecording(self.skills, self.runtime, secrets=(secret,))
        recorder.start("检查 " + secret)
        recorder.append("调用命令\n参数 " + secret + "\n完成核对")
        recorder.finish({"name": "inspect-request", "description": "检查 " + secret})
        result = recorder.confirm()
        for path in (recorder.draft_path, Path(result["files"][0])):
            self.assertNotIn(secret, path.read_text())
            self.assertIn("[REDACTED]", path.read_text())

    def test_invalid_text_and_size_limit_do_not_lose_previous_steps(self):
        recorder = self.recorder
        recorder.start()
        recorder.append("先保存这个步骤")
        before = recorder.draft
        for text in ("", "  ", "\x00", "\ud800", 1, None):
            with self.subTest(text=repr(text)):
                self.assertCode("invalid_skill_step", lambda: recorder.append(text))
                self.assertEqual(recorder.draft, before)
        self.assertCode("skill_recording_limit", lambda: recorder.append("中" * MAX_RECORDING_BYTES))
        self.assertEqual(recorder.draft, before)
        self.assertEqual(self.persisted(), before)

    def test_maximum_step_count_and_utf8_content_still_generate_loadable_skill(self):
        recorder = self.recorder
        recorder.start("录制最多步骤")
        for index in range(MAX_STEPS):
            recorder.append(f"步骤{index}：" + "测试" * 35)
        self.assertCode("skill_recording_limit", lambda: recorder.append("too many"))
        recorder.finish()
        result = recorder.confirm()
        self.assertLessEqual(len(SkillLibrary(self.skills).load(result["name"]).encode("utf-8")), MAX_SKILL_BYTES)

    def test_append_write_failure_keeps_memory_and_previous_atomic_file(self):
        recorder = self.recorder
        recorder.start()
        recorder.append("before")
        before = recorder.draft
        raw = recorder.draft_path.read_bytes()
        with mock.patch("fusion_agent.skill_recording.os.replace", side_effect=OSError(errno.ENOSPC, "disk full")):
            self.assertCode("skill_draft_write_failed", lambda: recorder.append("not accepted"))
        self.assertEqual(recorder.draft, before)
        self.assertEqual(recorder.draft_path.read_bytes(), raw)
        self.assertEqual([path.name for path in recorder.draft_path.parent.iterdir()], [recorder.draft_path.name])
        recorder.append("retry accepted")
        self.assertEqual(recorder.draft["steps"], ["before", "retry accepted"])

    def test_start_fsync_failure_keeps_idle_and_no_partial_draft(self):
        with mock.patch("fusion_agent.skill_recording.os.fsync", side_effect=OSError(errno.ENOSPC, "disk full")):
            self.assertCode("skill_draft_write_failed", self.recorder.start)
        self.assertEqual(self.recorder.phase, "idle")
        self.assertIsNone(self.recorder.draft_path)
        self.assertEqual(list((self.runtime / "skill-drafts").iterdir()), [])

    def test_finish_rename_cancel_failure_preserve_phase_and_content(self):
        recorder = self.recorder
        recorder.start()
        recorder.append("one")
        before = recorder.draft
        with mock.patch.object(recorder, "_persist", side_effect=ToolError("skill_draft_write_failed", "fail")):
            self.assertCode("skill_draft_write_failed", recorder.finish)
        self.assertEqual(recorder.phase, "recording")
        self.assertEqual(recorder.draft, before)
        recorder.finish()
        before = recorder.draft
        with mock.patch.object(recorder, "_persist", side_effect=ToolError("skill_draft_write_failed", "fail")):
            self.assertCode("skill_draft_write_failed", lambda: recorder.rename("new-name"))
            self.assertCode("skill_draft_write_failed", recorder.cancel)
        self.assertEqual(recorder.phase, "review")
        self.assertEqual(recorder.draft, before)

    def test_confirm_intent_write_failure_does_not_publish(self):
        self.prepare()
        before = self.recorder.draft
        with mock.patch.object(self.recorder, "_persist", side_effect=ToolError("skill_draft_write_failed", "fail")):
            self.assertCode("skill_draft_write_failed", self.recorder.confirm)
        self.assertEqual(self.recorder.draft, before)
        self.assertFalse(self.skills.exists())

    def test_publication_failure_keeps_review_and_leaves_no_partial_skill(self):
        self.prepare()
        with mock.patch("fusion_agent.skill_recording._publish_directory", side_effect=OSError(errno.ENOSYS, "unsupported")):
            self.assertCode("skill_atomic_unavailable", self.recorder.confirm)
        self.assertEqual(self.recorder.phase, "review")
        self.assertEqual(self.persisted()["steps"], self.recorder.draft["steps"])
        self.assertEqual(list(self.skills.iterdir()), [])
        self.recorder.rename("another-name")
        self.assertEqual(self.recorder.confirm()["name"], "another-name")

    def test_skill_write_failure_leaves_no_partial_directory(self):
        self.prepare()
        from fusion_agent.skill_recording import _write_text

        def write(directory, filename, content):
            if filename == "SKILL.md":
                raise OSError(errno.ENOSPC, "disk full")
            return _write_text(directory, filename, content)

        with mock.patch("fusion_agent.skill_recording._write_text", side_effect=write):
            self.assertCode("skill_create_failed", self.recorder.confirm)
        self.assertEqual(self.recorder.phase, "review")
        self.assertEqual(list(self.skills.iterdir()), [])

    def test_postpublication_draft_failure_reports_actual_published_result(self):
        self.prepare()
        persist = self.recorder._persist

        def save(value, **kwargs):
            if value["status"] == "published":
                raise ToolError("skill_draft_write_failed", "disk full")
            return persist(value, **kwargs)

        with mock.patch.object(self.recorder, "_persist", side_effect=save):
            result = self.recorder.confirm()
        self.assertTrue(result["published"])
        self.assertFalse(result["draft_saved"])
        self.assertTrue(result["warning"])
        self.assertEqual(self.recorder.phase, "idle")
        self.assertEqual(self.persisted()["publication"]["state"], "prepared")
        self.assertEqual(self.persisted()["steps"], self.recorder.draft["steps"])
        self.assertTrue(SkillLibrary(self.skills).load("inspect-workspace"))
        self.assertCode("skill_recording_state", self.recorder.confirm)

    def test_existing_file_directory_and_symlink_are_never_overwritten(self):
        self.skills.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (self.skills / "directory").mkdir()
        (self.skills / "file").write_text("keep")
        (self.skills / "link").symlink_to(outside, target_is_directory=True)
        self.prepare()
        for name in ("directory", "file", "link"):
            self.assertCode("skill_exists", lambda: self.recorder.confirm(name))
            self.assertEqual(self.recorder.phase, "review")
        self.assertEqual((self.skills / "file").read_text(), "keep")
        self.assertEqual(list((self.skills / "directory").iterdir()), [])
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(sorted(path.name for path in self.skills.iterdir()), ["directory", "file", "link"])

    def test_simultaneous_confirmation_publishes_only_one_complete_skill(self):
        first = self.prepare(recorder=SkillRecording(self.skills, self.runtime))
        second = self.prepare(recorder=SkillRecording(self.skills, self.runtime))

        def confirm(recorder):
            try:
                return recorder.confirm()
            except ToolError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(confirm, (first, second)))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn("skill_exists", results)
        self.assertEqual(sorted(path.name for path in self.skills.iterdir()), ["inspect-workspace"])
        self.assertTrue(SkillLibrary(self.skills).load("inspect-workspace"))
        self.assertEqual(sorted([first.phase, second.phase]), ["idle", "review"])

    def test_reject_linked_runtime_and_skill_roots(self):
        outside = self.root / "outside"
        outside.mkdir()
        linked = self.root / "linked"
        linked.symlink_to(outside, target_is_directory=True)
        recorder = SkillRecording(self.skills, linked)
        self.assertCode("skill_draft_write_failed", recorder.start)
        self.assertEqual(list(outside.iterdir()), [])
        recorder = self.prepare(recorder=SkillRecording(linked, self.runtime))
        self.assertCode("invalid_skill_root", recorder.confirm)
        self.assertEqual(list(outside.iterdir()), [])

    def test_invalid_root_paths_are_rejected_without_creation(self):
        for path in (self.root / "a" / ".." / "b", self.root / "bad\x00path", None):
            with self.subTest(path=path):
                self.assertCode("invalid_skill_root", lambda: SkillRecording(path, self.runtime))
                self.assertCode("invalid_skill_root", lambda: SkillRecording(self.skills, path))

    def test_mentioned_missing_resource_is_not_falsely_reported_as_verified(self):
        self.recorder.start("调用现有脚本")
        self.recorder.append("用 `scripts/not-provided.py` 输出结果。")
        self.recorder.finish({"name": "run-existing-script", "description": "需要复用此脚本流程时使用。"})
        result = self.recorder.confirm()
        report = SkillLibrary(self.skills).check(result["name"])
        self.assertFalse(report["ok"])
        self.assertFalse(report["executed_scripts"])
        self.assertEqual(result["files"], [str(self.skills / result["name"] / "SKILL.md")])


if __name__ == "__main__":
    unittest.main()
