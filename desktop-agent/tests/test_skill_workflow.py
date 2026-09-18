import json
from pathlib import Path
import tempfile
import unittest

from fusion_agent.contracts import ToolError
from fusion_agent.skill_demo import create_demo_skill
from fusion_agent.skills import SkillLibrary
from fusion_agent.skill_workflow import SkillSelection, skill_hints


def skill_text(name="demo", body="第一版内容"):
    return f"---\nname: {name}\ndescription: 测试技能\n---\n\n{body}\n"


class SkillWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.library = SkillLibrary(self.root / "skills")

    def tearDown(self):
        self.temporary.cleanup()

    def add(self, name="demo", body="第一版内容"):
        path = self.library.root / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(skill_text(name, body), encoding="utf-8")
        return path

    def test_selection_keeps_names_but_reloads_current_body_for_each_task(self):
        path = self.add()
        selected = SkillSelection(self.library, ["demo"])
        first = selected.contents_for_task()[0]
        self.assertIn("第一版内容", first["content"])
        (path / "SKILL.md").write_text(skill_text(body="已修改的第二版内容"), encoding="utf-8")
        second = selected.contents_for_task()[0]
        self.assertIn("已修改的第二版内容", second["content"])
        self.assertNotEqual(first, second)
        self.assertEqual(second["base_path"], str(path))
        self.assertEqual(selected.names, ("demo",))

    def test_invalid_edit_fails_without_reusing_old_body(self):
        path = self.add()
        selected = SkillSelection(self.library, ["demo"])
        (path / "SKILL.md").write_text("not valid frontmatter", encoding="utf-8")
        for read in (selected.reload, selected.contents_for_task):
            with self.subTest(read=read), self.assertRaises(ToolError):
                read()
        (path / "SKILL.md").write_text(skill_text(body=""), encoding="utf-8")
        with self.assertRaises(ToolError):
            selected.contents_for_task()
        (path / "SKILL.md").write_text(skill_text(body="修复后的正文"), encoding="utf-8")
        self.assertIn("修复后的正文", selected.reload()[0]["content"])

    def test_generation_is_immediately_discoverable_and_loadable(self):
        selected = SkillSelection(self.library)
        result = create_demo_skill("demo", self.library.root)
        self.assertEqual(self.library.catalog()[0]["name"], "demo")
        selected.load("demo")
        selected.load("demo")
        self.assertEqual(selected.names, ("demo",))
        self.assertEqual(result["hints"]["terminal_test"], "./run.sh skill-test demo")
        self.assertIn("./run.sh run --skill demo", result["hints"]["terminal_run"])
        self.assertTrue(selected.unload("demo"))
        self.assertFalse(selected.unload("demo"))
        self.assertEqual(selected.contents_for_task(), [])

    def test_invalid_new_selection_does_not_add_name(self):
        selected = SkillSelection(self.library)
        with self.assertRaises(ToolError):
            selected.load("missing")
        self.assertEqual(selected.names, ())
        for name in ("../demo", "bad;command", "Upper"):
            with self.subTest(name=name), self.assertRaises(ToolError):
                skill_hints(name)

    def test_static_check_parses_python_without_executing_it(self):
        path = self.add(body="参考 `scripts/check.py`。")
        (path / "scripts").mkdir()
        marker = self.root / "should-never-exist"
        (path / "scripts/check.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise RuntimeError('never run')\n",
            encoding="utf-8")
        result = self.library.check("demo")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["checked_python_scripts"], ["scripts/check.py"])
        self.assertFalse(result["executed_scripts"])
        self.assertFalse(marker.exists())
        self.assertEqual(result["status"], "structure_passed")

    def test_generated_and_shipped_skills_pass_structure_check(self):
        create_demo_skill("demo", self.library.root)
        self.assertTrue(self.library.check("demo")["ok"])
        shipped = SkillLibrary(Path(__file__).resolve().parents[1] / "skills")
        for entry in shipped.catalog():
            result = shipped.check(entry["name"])
            with self.subTest(name=entry["name"]):
                self.assertTrue(result["ok"], result)

    def test_invalid_syntax_and_missing_reference_are_reported(self):
        path = self.add(body="参考 [规则](references/missing.md) 与 `scripts/bad.py`。")
        (path / "scripts").mkdir()
        (path / "scripts/bad.py").write_text("if True print('invalid')\n")
        result = self.library.check("demo")
        self.assertFalse(result["ok"])
        self.assertTrue(any("missing.md" in error for error in result["errors"]))
        self.assertTrue(any("Python 语法错误" in error for error in result["errors"]))

    def test_nested_relative_reference_inside_skill_is_allowed(self):
        path = self.add(body="读取 [说明](references/info.md)。")
        (path / "references").mkdir()
        (path / "references/info.md").write_text("可返回 [技能正文](../SKILL.md)。", encoding="utf-8")
        self.assertTrue(self.library.check("demo")["ok"])
        (path / "references/info.md").write_text("不能越界 [外部](../../outside.md)。", encoding="utf-8")
        result = self.library.check("demo")
        self.assertFalse(result["ok"])
        self.assertTrue(any("边界" in error for error in result["errors"]))

    def test_symlink_resources_and_special_files_are_not_followed(self):
        path = self.add(body="读取 `references/outside.md`。")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "outside.md").write_text("PRIVATE_OUTSIDE_CONTENT", encoding="utf-8")
        (path / "references").symlink_to(outside, target_is_directory=True)
        result = self.library.check("demo")
        self.assertFalse(result["ok"])
        self.assertNotIn("PRIVATE_OUTSIDE_CONTENT", json.dumps(result))
        self.assertNotIn("references/outside.md", result["checked_files"])

    def test_unknown_script_language_is_disclosed_without_execution(self):
        path = self.add()
        (path / "scripts").mkdir()
        marker = self.root / "never-run"
        (path / "scripts/check.sh").write_text(f"touch '{marker}'\n")
        result = self.library.check("demo")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["warnings"])
        self.assertFalse(marker.exists())

    def test_invalid_frontmatter_and_empty_body_do_not_pass(self):
        path = self.add(body="")
        self.assertFalse(self.library.check("demo")["ok"])
        (path / "SKILL.md").write_text("---\nname: demo\npermissions: all\n---\n")
        self.assertFalse(self.library.check("demo")["ok"])


if __name__ == "__main__":
    unittest.main()
